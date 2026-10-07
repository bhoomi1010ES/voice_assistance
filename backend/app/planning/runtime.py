"""Opted-in foreground extraction and execution; shadow stays in the observer."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import Counter

from sqlalchemy import select

from app.llm.errors import LLMError
from app.llm.tool_loop import ToolExecutor
from app.models import PlanningAction, PlanningBatch
from app.planning import repository
from app.planning.executor import PlanningExecutor
from app.planning.extraction import ExtractionError, extract
from app.planning.policy import planning_capabilities
from app.planning.resolution import validate
from app.planning.service import PlanningError
from app.planning.types import MAX_TRANSCRIPT_CHARS, PlanningReceipt
from app.services.tool_idempotency import PostgresToolIdempotencyStore


async def execute_turn(
    observer,
    executor,
    *,
    principal,
    session_id,
    turn_id,
    response_id,
    transcript,
    now_utc,
    timezone,
    device_time_context=None,
    cancel_guard=None,
) -> PlanningReceipt | None:
    settings = observer.settings
    if (
        not settings.plan_mode_enabled
        or settings.plan_extraction_mode != "on"
        or not settings.plan_auto_actions_enabled
        or principal.user_id not in settings.plan_test_user_ids
        or not observer.llm.enabled
        or transcript.strip().casefold() in {"yes", "no", "confirm", "cancel"}
    ):
        return None

    async with observer.session_factory() as db:
        try:
            _, consent = await repository._consent(db, principal, session_id)
        except PlanningError as error:
            if str(error) == "planning_no_consent":
                return None
            raise
        if not planning_capabilities(settings, consent).automatic_writes:
            return None
        await repository._source(db, principal, session_id, turn_id, transcript)
    if not transcript.strip() or len(transcript) > MAX_TRANSCRIPT_CHARS:
        raise PlanningError("planning_input_bound")
    capacity = settings.plan_extraction_max_concurrent
    count = getattr(observer, "foreground_count", 0)
    if count + len(observer.tasks) >= capacity:
        raise PlanningError("planning_capacity_skip")
    observer.foreground_count = count + 1
    started = time.perf_counter()
    snapshot = None
    decisions = ()
    metrics = {"status": "failed", "reason": "planning_extraction_failed", "candidate_count": 0}
    try:
        async with asyncio.timeout(settings.plan_extraction_timeout_ms / 1000):
            async with observer.session_factory() as db:
                snapshot = await repository.claim(
                    db,
                    settings,
                    principal,
                    session_id,
                    turn_id,
                    transcript,
                    now_utc,
                    timezone,
                )
                if snapshot is None:
                    _, fresh_consent = await repository._consent(db, principal, session_id)
                    if not planning_capabilities(settings, fresh_consent).automatic_writes:
                        raise PlanningError("planning_consent_revoked")
                    # Claim's owned locks/source checks also protect replay access.
                    batch = await db.scalar(
                        select(PlanningBatch).where(
                            PlanningBatch.user_id == principal.user_id,
                            PlanningBatch.turn_id == turn_id,
                            PlanningBatch.session_id == session_id,
                        )
                    )
                    first = (
                        None
                        if batch is None
                        else await db.scalar(
                            select(PlanningAction).where(
                                PlanningAction.user_id == principal.user_id,
                                PlanningAction.batch_id == batch.id,
                                PlanningAction.ordinal == 0,
                            )
                        )
                    )
                    if batch is not None and batch.source_digest != hashlib.sha256(
                        transcript.encode("utf-8")
                    ).hexdigest():
                        raise PlanningError("planning_replay_source_changed")
                    stored = (first.result_json or {}).get("_planning_receipt") if first else None
                    if stored and batch.status in {"completed", "failed"}:
                        return PlanningReceipt.from_dict(stored)
                    raise PlanningError("planning_turn_already_observed")
            envelope = await extract(
                observer.llm,
                snapshot,
                turn_id,
                transcript,
                timeout_ms=settings.plan_extraction_timeout_ms,
                max_actions=settings.plan_max_actions_per_turn,
                max_tokens=settings.llm_max_output_tokens,
            )
            decisions = validate(
                envelope,
                transcript,
                snapshot,
                max_actions=settings.plan_max_actions_per_turn,
            )
            metrics = {
                "status": "validated",
                "candidate_count": len(decisions),
                "outcomes": dict(Counter(d.disposition for d in decisions)),
                "reasons": dict(Counter(d.reason for d in decisions)),
            }
        metrics["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        async with observer.session_factory() as db:
            await repository.finish(
                db,
                settings,
                principal,
                turn_id,
                transcript,
                snapshot,
                decisions,
                metrics,
                persist_proposals=False,
            )
            if not decisions or (
                all(d.disposition in {"NO_ACTION", "DENY"} for d in decisions)
                and not any(d.reason == "duplicate" for d in decisions)
            ):
                return None
            tool_executor = executor.tool_executor
            if isinstance(tool_executor.idempotency_store, PostgresToolIdempotencyStore):
                tool_executor = ToolExecutor(
                    tool_executor.registry,
                    idempotency_store=PostgresToolIdempotencyStore(db),
                )
            scoped = PlanningExecutor(settings, tool_executor, executor.confirmation_service)
            return await scoped.execute_batch(
                db=db,
                principal=principal,
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                transcript=transcript,
                snapshot=snapshot,
                decisions=decisions,
                user_timezone=timezone,
                device_time_context=device_time_context,
                cancel_guard=cancel_guard,
            )
    except (TimeoutError, ExtractionError, LLMError) as error:
        reason = (
            "timeout"
            if isinstance(error, TimeoutError)
            else str(error)
            if isinstance(error, ExtractionError)
            else error.code
        )
        metrics = {"status": "failed", "reason": reason, "candidate_count": 0}
        if snapshot is not None:
            async with observer.session_factory() as db:
                await repository.finish(
                    db,
                    settings,
                    principal,
                    turn_id,
                    transcript,
                    snapshot,
                    (),
                    metrics,
                    persist_proposals=False,
                )
        raise PlanningError("planning_" + reason) from None
    except PlanningError as error:
        metrics = {"status": "discarded", "reason": str(error), "candidate_count": 0}
        raise
    finally:
        observer.foreground_count -= 1
        observer._metric(session_id, turn_id, metrics)
