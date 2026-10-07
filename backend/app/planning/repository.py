"""Short owned transactions for observations and proposal-only storage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.models import (
    ConversationTurn,
    Message,
    Plan,
    PlanContextItem,
    PlanningAction,
    PlanningBatch,
    Reminder,
    Task,
    VoiceSession,
)
from app.planning.policy import PlanningConsent, planning_capabilities
from app.planning.service import PlanningError, locked_state
from app.planning.types import (
    EXTRACTOR_VERSION,
    Decision,
    PlanningReceipt,
    PlanningSnapshot,
    Target,
)


async def _source(db, principal, session_id, turn_id, transcript):
    turn = await db.scalar(
        select(ConversationTurn)
        .where(
            ConversationTurn.id == turn_id,
            ConversationTurn.user_id == principal.user_id,
            ConversationTurn.session_id == session_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    message = await db.scalar(
        select(Message).where(
            Message.turn_id == turn_id,
            Message.user_id == principal.user_id,
            Message.role == "user",
            Message.sequence_no == 0,
            Message.is_final.is_(True),
        )
    )
    if turn is None or message is None or message.content != transcript:
        raise PlanningError("planning_source_unavailable")
    if turn.status in {"cancelled", "timed_out", "disconnected"}:
        raise PlanningError("planning_source_cancelled")
    return turn


async def _consent(db, principal, session_id):
    state = await locked_state(db, principal, session_id, create=False)
    if state is None:
        raise PlanningError("planning_no_consent")
    voice = await db.scalar(
        select(VoiceSession).where(
            VoiceSession.id == session_id,
            VoiceSession.user_id == principal.user_id,
        )
    )
    return state, PlanningConsent(
        user_id=principal.user_id,
        session_id=session_id,
        authenticated=True,
        session_active=True,
        mode=state.mode,
        state_version=state.state_version,
        expected_state_version=state.state_version,
        policy_version=state.policy_version,
        memory_excluded=bool((voice.client_metadata or {}).get("memory_excluded")),
    )


async def claim(db, settings, principal, session_id, turn_id, transcript, now_utc, timezone):
    """Safe observation marker serializes retries without storing shadow proposal content."""
    state, consent = await _consent(db, principal, session_id)
    if not planning_capabilities(settings, consent).extract:
        return None
    turn = await _source(db, principal, session_id, turn_id, transcript)
    if (turn.metadata_json or {}).get("planning_observation"):
        return None
    plans = (
        await db.scalars(
            select(Plan)
            .where(
                Plan.user_id == principal.user_id,
                Plan.status == "active",
            )
            .order_by(Plan.id)
            .limit(21)
        )
    ).all()
    targets = []
    complete = len(plans) <= 20
    if state.active_plan_id and not any(plan.id == state.active_plan_id for plan in plans[:20]):
        complete = False
    for model, kind, scheduled in ((Task, "task", "due_at"), (Reminder, "reminder", "trigger_at")):
        query = select(model).where(model.user_id == principal.user_id)
        query = query.where(model.plan_id == state.active_plan_id)
        query = query.where(model.status == ("pending" if kind == "task" else "scheduled"))
        rows = (await db.scalars(query.order_by(model.id).limit(51))).all()
        complete = complete and len(rows) <= 50
        for row in rows[:50]:
            at = getattr(row, scheduled)
            if at is not None and at.tzinfo is None:
                at = at.replace(tzinfo=UTC)  # SQLite test shim; PostgreSQL returns aware instants.
            targets.append(
                Target(
                    row.id,
                    kind,
                    row.title,
                    row.plan_id,
                    row.revision,
                    at,
                    getattr(row, "recurrence_rule", None),
                )
            )
    context = []
    if state.active_plan_id:
        rows = (
            await db.scalars(
                select(PlanContextItem)
                .where(
                    PlanContextItem.user_id == principal.user_id,
                    PlanContextItem.plan_id == state.active_plan_id,
                    PlanContextItem.status == "active",
                )
                .order_by(PlanContextItem.id)
                .limit(9)
            )
        ).all()
        complete = complete and len(rows) <= 8
        context = [row.content[:1024] for row in rows[:8]]
    turn.metadata_json = {
        **(turn.metadata_json or {}),
        "planning_observation": {
            "status": "claimed",
            "extractor_version": EXTRACTOR_VERSION,
            "policy_version": consent.policy_version,
            "state_version": consent.state_version,
        },
    }
    recent = await db.scalar(
        select(PlanningAction.result_json)
        .join(PlanningBatch, PlanningBatch.id == PlanningAction.batch_id)
        .where(
            PlanningBatch.user_id == principal.user_id,
            PlanningBatch.session_id == session_id,
            PlanningBatch.turn_id != turn_id,
            PlanningBatch.status == "completed",
            PlanningAction.user_id == principal.user_id,
            PlanningAction.ordinal == 0,
        )
        .order_by(PlanningBatch.created_at.desc(), PlanningBatch.id.desc())
        .limit(1)
    )
    recent_receipt = (recent or {}).get("_planning_receipt")
    snapshot = PlanningSnapshot(
        consent,
        now_utc,
        timezone,
        state.active_plan_id,
        tuple(Target(p.id, "plan", p.name, p.id, p.revision) for p in plans[:20]),
        tuple(targets),
        tuple(context),
        complete,
        PlanningReceipt.from_dict(recent_receipt) if recent_receipt else None,
    )
    await db.commit()  # No model request holds a consent/voice/turn lock.
    return snapshot


async def finish(
    db,
    settings,
    principal,
    turn_id: UUID,
    transcript: str,
    snapshot: PlanningSnapshot,
    decisions: tuple[Decision, ...],
    metrics: dict,
    *,
    persist_proposals: bool = True,
):
    state, consent = await _consent(db, principal, snapshot.consent.session_id)
    capabilities = planning_capabilities(settings, consent)
    if not capabilities.extract or state.state_version != snapshot.consent.state_version:
        raise PlanningError("planning_consent_revoked")
    turn = await _source(db, principal, consent.session_id, turn_id, transcript)
    marker = (turn.metadata_json or {}).get("planning_observation", {})
    if marker.get("status") != "claimed":
        return
    # Metadata is counts/reason codes only. No source digest, proposal text or spans.
    turn.metadata_json = {
        **(turn.metadata_json or {}),
        "planning_observation": {
            **marker,
            **metrics,
        },
    }
    if persist_proposals and capabilities.persist_proposals and metrics["status"] == "validated":
        batch_id = uuid4()
        db.add(
            PlanningBatch(
                id=batch_id,
                user_id=principal.user_id,
                session_id=consent.session_id,
                turn_id=turn_id,
                state_version=state.state_version,
                extractor_version=EXTRACTOR_VERSION,
                policy_version=consent.policy_version,
                source_digest=hashlib.sha256(transcript.encode()).hexdigest(),
                status="validated",
            )
        )
        await db.flush()
        for ordinal, decision in enumerate(decisions):
            # Rejected/nonactionable evidence does not become retained proposal content.
            if decision.disposition in {"DENY", "NO_ACTION"}:
                continue
            payload = {
                "proposal": decision.proposal.model_dump(mode="json"),
                "scheduled_at": decision.scheduled_at.isoformat()
                if decision.scheduled_at
                else None,
                "timezone": decision.timezone,
                "plan_ordinal": decision.plan_ordinal,
                "resolved_at": snapshot.now_utc.isoformat(),
            }
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            db.add(
                PlanningAction(
                    id=uuid4(),
                    user_id=principal.user_id,
                    batch_id=batch_id,
                    plan_id=decision.plan_id,
                    ordinal=ordinal,
                    action_type=decision.proposal.operation,
                    payload_json=payload,
                    payload_digest=hashlib.sha256(canonical.encode()).hexdigest(),
                    source_spans_json=[
                        span.model_dump()
                        for span in (
                            decision.proposal.source,
                            decision.proposal.temporal,
                            decision.proposal.recurrence_source,
                        )
                        if span
                    ],
                    disposition=decision.disposition,
                    reason=decision.reason,
                    target_id=decision.target_id,
                    target_revision=decision.target_revision,
                    confidence=decision.proposal.confidence,
                    status="pending",
                )
            )
    # No plan/context/task/reminder/memory handler or executor is called here.
    await db.commit()


async def erase_proposal_content(db, user_id: UUID, session_id: UUID) -> None:
    """Caller holds the owned voice lock and revokes consent in this transaction.

    Preserve action identities/resource provenance while removing proposal text,
    evidence, source hashes and any content-bearing results.
    """
    batch_ids = select(PlanningBatch.id).where(
        PlanningBatch.user_id == user_id,
        PlanningBatch.session_id == session_id,
    )
    redacted = {"redacted": True}
    await db.execute(
        update(PlanningAction)
        .where(
            PlanningAction.user_id == user_id,
            PlanningAction.batch_id.in_(batch_ids),
        )
        .values(
            payload_json=redacted,
            source_spans_json=[],
            result_json=None,
            payload_digest=hashlib.sha256(
                json.dumps(redacted, sort_keys=True).encode()
            ).hexdigest(),
        )
    )
    await db.execute(
        update(PlanningBatch)
        .where(
            PlanningBatch.user_id == user_id,
            PlanningBatch.session_id == session_id,
        )
        .values(source_digest="0" * 64)
    )
