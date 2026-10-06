"""Shared bounded background observation. Never emits responses or executes actions."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter

from app.planning import repository
from app.planning.extraction import ExtractionError, extract
from app.planning.resolution import validate
from app.planning.service import recognize_mode_control, recognize_plan_selection
from app.planning.types import EXTRACTOR_VERSION, MAX_TRANSCRIPT_CHARS

LOGGER = logging.getLogger("voice-assistance-backend")


class PlanningObserver:
    def __init__(self, settings, llm, session_factory):
        self.settings = settings
        self.llm = llm
        self.session_factory = session_factory
        self.tasks: dict[asyncio.Task, tuple] = {}

    def schedule(self, *, principal, session_id, turn_id, transcript, now_utc, timezone):
        started = time.perf_counter()
        if (
            not self.settings.plan_mode_enabled
            or self.settings.plan_extraction_mode == "off"
            or principal.user_id not in self.settings.plan_test_user_ids
            or not self.llm.enabled
            or not self.session_factory
            or not transcript.strip()
            or len(transcript) > MAX_TRANSCRIPT_CHARS
            or recognize_mode_control(transcript)
            or recognize_plan_selection(transcript) is not None
            or transcript.strip().casefold() in {"yes", "no", "confirm", "cancel"}
        ):
            return None
        capacity = min(
            self.settings.plan_extraction_max_concurrent,
            getattr(self.llm, "background_capacity", self.settings.llm_max_concurrent_requests - 1),
        )
        if len(self.tasks) >= capacity:
            self._metric(session_id, turn_id, {"status": "capacity_skip"})
            return None
        task = asyncio.create_task(
            self.observe(
                principal=principal,
                session_id=session_id,
                turn_id=turn_id,
                transcript=transcript,
                now_utc=now_utc,
                timezone=timezone,
            )
        )
        self.tasks[task] = (principal.user_id, session_id)
        task.add_done_callback(self.tasks.pop)
        self._metric(
            session_id,
            turn_id,
            {
                "status": "scheduled",
                "added_scheduling_latency_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        return task

    def _metric(self, session_id, turn_id, metrics):
        LOGGER.info(
            "Planning observation",
            extra={
                "event": "planning.observation",
                "session_id": str(session_id),
                "turn_id": str(turn_id),
                "extractor_version": EXTRACTOR_VERSION,
                "policy_version": self.settings.plan_policy_version,
                **metrics,
            },
        )

    async def observe(self, *, principal, session_id, turn_id, transcript, now_utc, timezone):
        started = time.perf_counter()
        snapshot = None
        decisions = ()
        metrics = {"status": "failed", "reason": "observer_failure", "candidate_count": 0}
        try:
            async with asyncio.timeout(self.settings.plan_extraction_timeout_ms / 1000):
                async with self.session_factory() as db:
                    snapshot = await repository.claim(
                        db,
                        self.settings,
                        principal,
                        session_id,
                        turn_id,
                        transcript,
                        now_utc,
                        timezone,
                    )
                if snapshot is None:
                    return
                envelope = await extract(
                    self.llm,
                    snapshot,
                    turn_id,
                    transcript,
                    timeout_ms=self.settings.plan_extraction_timeout_ms,
                    max_actions=self.settings.plan_max_actions_per_turn,
                    max_tokens=self.settings.llm_max_output_tokens,
                )
                decisions = validate(envelope, transcript, snapshot)
                metrics = {
                    "status": "validated",
                    "candidate_count": len(decisions),
                    "outcomes": dict(Counter(d.disposition for d in decisions)),
                    "reasons": dict(Counter(d.reason for d in decisions)),
                }
        except TimeoutError:
            metrics["reason"] = "timeout"
        except ExtractionError as error:
            metrics["reason"] = str(error)  # Only fixed internal reason codes.
        except asyncio.CancelledError:
            self._metric(session_id, turn_id, {"status": "cancelled"})
            raise
        except Exception:  # noqa: BLE001 - isolated shadow failures never escape to answering
            pass
        metrics["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if snapshot is not None:
            try:
                async with asyncio.timeout(1):
                    async with self.session_factory() as db:
                        await repository.finish(
                            db,
                            self.settings,
                            principal,
                            turn_id,
                            transcript,
                            snapshot,
                            decisions,
                            metrics,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - revocation/source purge/storage failure discards results
                metrics = {
                    "status": "discarded",
                    "reason": "authority_or_source_changed",
                    "duration_ms": metrics["duration_ms"],
                }
        self._metric(session_id, turn_id, metrics)

    async def cancel_session(self, user_id, session_id):
        tasks = [t for t, scope in self.tasks.items() if scope == (user_id, session_id)]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
