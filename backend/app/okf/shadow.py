"""Privacy-safe, non-authoritative OKF shadow reads for explicit test cohorts."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.knowledge.selector import KnowledgeSelector
from app.knowledge.types import KnowledgeEngineResult
from app.okf.types import KnowledgeDisposition

from .retrieval import OkfRetrievalService
from .types import KnowledgeRequest


class ShadowReadCapacity:
    """Non-blocking per-process cap; callers either acquire immediately or skip."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("shadow_capacity_limit_invalid")
        self.limit = limit
        self._active = 0

    @property
    def active(self) -> int:
        return self._active

    def try_acquire(self) -> bool:
        # This synchronous section is atomic within the owning asyncio loop.
        if self._active >= self.limit:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        if self._active < 1:
            raise RuntimeError("shadow_capacity_not_acquired")
        self._active -= 1


@dataclass(frozen=True)
class ShadowReadObservation:
    fields: dict[str, Any]


def schedule_shadow_read(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession] | None,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    query: str,
    now: datetime,
    rag_disposition: str,
    rag_evidence_ids: tuple[uuid.UUID, ...],
    rag_latency_ms: float,
    capacity: ShadowReadCapacity,
    task_set: set[asyncio.Task[None]],
    correlation: dict[str, str],
    on_complete: Callable[[ShadowReadObservation], None] | None = None,
) -> str:
    """Schedule the shared non-blocking shadow observer used by voice and eval."""

    logger = logging.getLogger("voice-assistance-backend")
    base = {
        key: value
        for key, value in correlation.items()
        if key in {"session_id", "turn_id", "response_id", "case_id", "run_id"}
    }
    if (
        not settings.okf_enabled
        or not settings.okf_shadow_reads
        or settings.knowledge_mode != "rag"
        or user_id not in settings.okf_shadow_user_ids
    ):
        return "disabled"
    if session_factory is None:
        logger.info(
            "OKF shadow read skipped",
            extra={
                "event": "okf.shadow.read",
                **base,
                "shadow_status": "skipped",
                "skip_reason": "independent_session_unavailable",
                "latency_ms": 0.0,
            },
        )
        return "skipped"
    if not capacity.try_acquire():
        logger.info(
            "OKF shadow read skipped",
            extra={
                "event": "okf.shadow.read",
                **base,
                "shadow_status": "skipped",
                "skip_reason": "capacity_limit",
                "latency_ms": 0.0,
            },
        )
        return "skipped"
    scheduled_ns = time.perf_counter_ns()

    async def observe() -> None:
        started = time.perf_counter()
        try:
            observation = await perform_shadow_read(
                settings=settings,
                session_factory=session_factory,
                user_id=user_id,
                session_id=session_id,
                query=query,
                now=now,
                rag_disposition=rag_disposition,
                rag_evidence_ids=rag_evidence_ids,
                rag_latency_ms=rag_latency_ms,
                scheduled_ns=scheduled_ns,
            )
            if on_complete is not None:
                on_complete(observation)
            status = observation.fields.get("shadow_status", "completed")
            logger.info(
                "OKF shadow read completed",
                extra={
                    "event": "okf.shadow.read",
                    **base,
                    **observation.fields,
                    "shadow_status": status,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - shadow errors never affect caller
            logger.warning(
                "OKF shadow read failed",
                extra={
                    "event": "okf.shadow.read",
                    **base,
                    "shadow_status": "failed",
                    "failure_reason": type(error).__name__,
                    "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
                },
            )
        finally:
            capacity.release()

    task = asyncio.create_task(observe(), name=f"okf-shadow-{correlation.get('case_id', 'read')}")
    task_set.add(task)
    task.add_done_callback(task_set.discard)
    return "scheduled"


async def perform_shadow_read(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    query: str,
    now: datetime,
    rag_disposition: str,
    rag_evidence_ids: tuple[uuid.UUID, ...],
    rag_latency_ms: float = 0.0,
    scheduled_ns: int | None = None,
) -> ShadowReadObservation:
    """Read OKF in an independent session and return content-free comparison data."""

    started_ns = time.perf_counter_ns()
    stages: dict[str, float] = {}

    def trace(stage: str, duration_ms: float, _metadata: dict[str, Any]) -> None:
        stages[stage] = round(duration_ms, 3)

    result = None
    session_setup_ms = None
    connection_acquire_ms = None
    outer_timeout = False
    try:
        async with asyncio.timeout(settings.okf_retrieval_timeout_ms / 1_000):
            setup_started_ns = time.perf_counter_ns()
            async with session_factory() as session:
                session_setup_ms = round((time.perf_counter_ns() - setup_started_ns) / 1_000_000, 3)
                connection_started_ns = time.perf_counter_ns()
                await session.connection()
                connection_acquire_ms = round(
                    (time.perf_counter_ns() - connection_started_ns) / 1_000_000, 3
                )
                result = await OkfRetrievalService(settings).retrieve(
                    session,
                    KnowledgeRequest(
                        user_id=user_id,
                        query=query,
                        now=now,
                        session_id=session_id,
                    ),
                    trace=trace,
                )
    except TimeoutError:
        outer_timeout = True

    okf_latency_ms = result.duration_ms if result is not None else None
    okf_ids = (
        {memory_id for item in result.evidence for memory_id in item.source_memory_ids}
        if result is not None
        else set()
    )
    rag_ids = set(rag_evidence_ids)
    rag_status = _normalize_rag_disposition(rag_disposition)
    overlap_count = len(rag_ids & okf_ids)
    rag_count = len(rag_ids)
    okf_count = len(okf_ids)
    okf_status = (
        result.status.value if result is not None else KnowledgeDisposition.UNAVAILABLE.value
    )
    okf_reason = (
        result.degraded_reason
        if result is not None
        else "retrieval_timeout"
        if outer_timeout
        else "retrieval_failed"
    )
    combine_started_ns = time.perf_counter_ns()
    combined = KnowledgeSelector.combine(
        "combined",
        (
            KnowledgeEngineResult(
                engine="rag",
                disposition=KnowledgeDisposition(rag_status),
                reason="authoritative_rag_observation",
                evidence_ids=tuple(rag_ids),
            ),
            KnowledgeEngineResult(
                engine="okf",
                disposition=(
                    result.status if result is not None else KnowledgeDisposition.UNAVAILABLE
                ),
                reason=okf_reason or "okf_observation",
                evidence_ids=tuple(okf_ids),
            ),
        ),
    )
    combine_ms = max(0.0, (time.perf_counter_ns() - combine_started_ns) / 1_000_000)
    combined_latency_ms = (
        rag_latency_ms + okf_latency_ms + combine_ms if okf_latency_ms is not None else None
    )
    no_result_case = rag_status == "no_result" or okf_status == "no_result"
    finished_ns = time.perf_counter_ns()
    elapsed_ms = round((finished_ns - started_ns) / 1_000_000, 3)
    return ShadowReadObservation(
        fields={
            "shadow_status": "timeout" if outer_timeout else "completed",
            "rag_disposition": rag_status,
            "okf_disposition": okf_status,
            "okf_reason": okf_reason,
            "combined_disposition": combined.disposition.value,
            "rag_evidence_count": rag_count,
            "okf_evidence_count": okf_count,
            "combined_evidence_count": len(rag_ids | okf_ids),
            "overlap_count": overlap_count,
            "overlap_ratio": (
                round(overlap_count / len(rag_ids | okf_ids), 4) if rag_ids | okf_ids else None
            ),
            "no_result_case": no_result_case,
            "no_result_agreement": (rag_status == okf_status if no_result_case else None),
            "okf_fact_count": len(result.evidence) if result is not None else 0,
            "okf_provenance_coverage": (
                round(
                    sum(bool(item.source_memory_ids) for item in result.evidence)
                    / len(result.evidence),
                    4,
                )
                if result is not None and result.evidence
                else None
            ),
            "conflict_count": 1 if okf_status == "conflict" else 0,
            "rag_latency_ms": round(rag_latency_ms, 3),
            "okf_latency_ms": round(okf_latency_ms, 3) if okf_latency_ms is not None else None,
            "combined_latency_ms": (
                round(combined_latency_ms, 3) if combined_latency_ms is not None else None
            ),
            "session_setup_ms": session_setup_ms,
            "connection_acquire_ms": connection_acquire_ms,
            "schedule_to_start_ms": (
                round((started_ns - scheduled_ns) / 1_000_000, 3)
                if scheduled_ns is not None
                else 0.0
            ),
            "latency_ms": elapsed_ms,
            "stage_timings_ms": stages,
        }
    )


def _normalize_rag_disposition(value: str) -> str:
    return {
        "DIRECT_RAG": "direct_answer",
        "RAG_PLUS_LLM": "continue_with_evidence",
        "NO_RESULT": "no_result",
        "UNAVAILABLE": "unavailable",
    }.get(value, value.casefold())
