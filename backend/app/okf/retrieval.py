from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import (
    MemoryItem,
    OkfConcept,
    OkfConceptAssertion,
    OkfConceptSource,
    OkfConceptVersion,
    User,
    VoiceSession,
)

from .query_plan import plan_okf_query
from .types import (
    KnowledgeDisposition,
    KnowledgeRequest,
    KnowledgeResult,
    OkfEvidence,
    OkfQueryPlan,
)

_MAX_CONCEPT_CANDIDATES = 500
_MAX_SOURCE_IDS_PER_ASSERTION = 8


def _canonical_term_pattern(term: str) -> str:
    """Match a planned term as a whole canonical-key segment."""

    return rf"(^|/|-){re.escape(term)}($|/|-)"


class OkfRetrievalService:
    """Bounded deterministic retrieval that requires active owned provenance."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def retrieve(
        self,
        session: AsyncSession,
        request: KnowledgeRequest,
        *,
        trace: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> KnowledgeResult:
        started_ns = time.perf_counter_ns()
        started = started_ns / 1_000_000_000
        if request.cancellation_check:
            return self._result(KnowledgeDisposition.CANCELLED, started)
        if not self.settings.okf_enabled:
            return self._result(KnowledgeDisposition.UNAVAILABLE, started, reason="okf_disabled")
        if not request.memory_enabled or request.memory_excluded:
            return self._result(
                KnowledgeDisposition.UNAVAILABLE, started, reason="memory_unavailable"
            )

        planning_started_ns = time.perf_counter_ns()
        plan = plan_okf_query(request.query, limit=self.settings.okf_query_limit)
        _trace_stage(trace, "query_planning", planning_started_ns)
        try:
            async with asyncio.timeout(self.settings.okf_retrieval_timeout_ms / 1_000):
                transaction_started_ns = time.perf_counter_ns()
                try:
                    # Reads are an optional voice enhancement. Keep database
                    # errors/cancellation inside a savepoint so the caller's
                    # outer turn transaction remains usable.
                    async with session.begin_nested():
                        result = await self._retrieve_database(
                            session, request, plan, started, trace=trace
                        )
                    return result
                finally:
                    _trace_stage(trace, "database_transaction", transaction_started_ns)
        except TimeoutError:
            return self._result(
                KnowledgeDisposition.UNAVAILABLE, started, reason="retrieval_timeout"
            )
        except SQLAlchemyError:
            # Keep the failure surface content-free and fail closed.
            return self._result(
                KnowledgeDisposition.UNAVAILABLE,
                started,
                reason="database_unavailable",
            )
        finally:
            _trace_stage(trace, "total_retrieval", started_ns)

    async def _retrieve_database(
        self,
        session: AsyncSession,
        request: KnowledgeRequest,
        plan: OkfQueryPlan,
        started: float,
        *,
        trace: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> KnowledgeResult:
        owner_started_ns = time.perf_counter_ns()
        owner_can_read = await self._owner_can_read(session, request.user_id)
        _trace_stage(trace, "owner_check", owner_started_ns)
        if not owner_can_read:
            return self._result(
                KnowledgeDisposition.UNAVAILABLE, started, reason="memory_unavailable"
            )
        if request.session_id is not None:
            session_started_ns = time.perf_counter_ns()
            session_can_read = await self._session_can_read(
                session, user_id=request.user_id, session_id=request.session_id
            )
            _trace_stage(trace, "session_check", session_started_ns)
            if not session_can_read:
                return self._result(
                    KnowledgeDisposition.UNAVAILABLE, started, reason="session_unavailable"
                )
        concept_ids = await self._matching_concept_ids(
            session, request=request, plan=plan, trace=trace
        )
        if not concept_ids:
            return self._result(KnowledgeDisposition.NO_RESULT, started)
        evidence = await self._active_evidence(
            session,
            user_id=request.user_id,
            concept_ids=concept_ids,
            now=request.now,
            limit=plan.limit,
            trace=trace,
        )
        if not evidence:
            return self._result(KnowledgeDisposition.NO_RESULT, started)
        conflict_started_ns = time.perf_counter_ns()
        assertion_counts: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for item in evidence:
            assertion_counts[item.concept_id].add(item.assertion_id)
        conflicts = tuple(item for item in evidence if len(assertion_counts[item.concept_id]) > 1)
        _trace_stage(trace, "conflict_resolution", conflict_started_ns)
        if conflicts:
            return self._result(
                KnowledgeDisposition.CONFLICT,
                started,
                evidence=tuple(evidence),
                conflicts=conflicts,
            )
        shaping_started_ns = time.perf_counter_ns()
        disposition = (
            KnowledgeDisposition.DIRECT_ANSWER
            if len(evidence) == 1
            else KnowledgeDisposition.CONTINUE_WITH_EVIDENCE
        )
        result = self._result(disposition, started, evidence=tuple(evidence))
        _trace_stage(trace, "result_shaping", shaping_started_ns)
        return result

    async def _owner_can_read(self, session: AsyncSession, user_id: uuid.UUID) -> bool:
        return (
            await session.scalar(
                select(User.memory_enabled).where(
                    User.id == user_id,
                    User.status == "active",
                    User.memory_enabled.is_(True),
                )
            )
        ) is True

    async def _session_can_read(
        self, session: AsyncSession, *, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> bool:
        row = (
            await session.execute(
                select(VoiceSession.id, VoiceSession.client_metadata).where(
                    VoiceSession.id == session_id,
                    VoiceSession.user_id == user_id,
                )
            )
        ).first()
        return row is not None and (row[1] or {}).get("memory_excluded") is not True

    async def _matching_concept_ids(
        self,
        session: AsyncSession,
        *,
        request: KnowledgeRequest,
        plan: OkfQueryPlan,
        trace: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> tuple[uuid.UUID, ...]:
        conditions = [
            OkfConcept.user_id == request.user_id,
            OkfConcept.status.in_(("active", "contested")),
        ]
        if plan.concept_types:
            conditions.append(OkfConcept.concept_type.in_(plan.concept_types))
        for term in plan.key_terms:
            conditions.append(OkfConcept.canonical_key.op("~")(_canonical_term_pattern(term)))
        candidate_limit = min(max(plan.limit * 10, 50), _MAX_CONCEPT_CANDIDATES)
        query_started_ns = time.perf_counter_ns()
        base_ids = tuple(
            (
                await session.scalars(
                    select(OkfConcept.id)
                    .where(*conditions)
                    .order_by(OkfConcept.canonical_key, OkfConcept.id)
                    .limit(candidate_limit)
                )
            ).all()
        )
        _trace_stage(trace, "concept_candidate_query", query_started_ns, {"count": len(base_ids)})
        if not base_ids or not plan.project_expansion:
            return base_ids[: plan.limit * 5]

        expansion_started_ns = time.perf_counter_ns()
        concepts = list(
            (
                await session.scalars(
                    select(OkfConcept).where(
                        OkfConcept.user_id == request.user_id,
                        OkfConcept.id.in_(base_ids),
                    )
                )
            ).all()
        )
        expanded_ids = set(base_ids)
        parents = {item.parent_concept_id for item in concepts if item.parent_concept_id}
        roots = {
            item.id
            for item in concepts
            if item.concept_type == "project" and item.parent_concept_id is None
        }
        if parents:
            expanded_ids.update(
                (
                    await session.scalars(
                        select(OkfConcept.id)
                        .where(
                            OkfConcept.user_id == request.user_id,
                            OkfConcept.id.in_(parents),
                            OkfConcept.status.in_(("active", "contested")),
                        )
                        .order_by(OkfConcept.canonical_key, OkfConcept.id)
                        .limit(plan.limit * 5)
                    )
                ).all()
            )
        if roots:
            expanded_ids.update(
                (
                    await session.scalars(
                        select(OkfConcept.id)
                        .where(
                            OkfConcept.user_id == request.user_id,
                            OkfConcept.parent_concept_id.in_(roots),
                            OkfConcept.status.in_(("active", "contested")),
                        )
                        .order_by(OkfConcept.canonical_key, OkfConcept.id)
                        .limit(plan.limit * 5)
                    )
                ).all()
            )
        expanded = tuple(sorted(expanded_ids, key=str)[: plan.limit * 5])
        _trace_stage(
            trace, "parent_child_expansion", expansion_started_ns, {"count": len(expanded)}
        )
        return expanded

    async def _active_evidence(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        concept_ids: tuple[uuid.UUID, ...],
        now: datetime,
        limit: int,
        trace: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> list[OkfEvidence]:
        current = and_(
            OkfConceptVersion.user_id == user_id,
            OkfConceptVersion.assertion_id == OkfConceptAssertion.id,
            OkfConceptVersion.version == OkfConceptAssertion.current_version,
            OkfConceptVersion.status == "active",
        )
        valid = and_(
            or_(OkfConceptAssertion.valid_from.is_(None), OkfConceptAssertion.valid_from <= now),
            or_(OkfConceptAssertion.valid_to.is_(None), OkfConceptAssertion.valid_to > now),
        )
        assertion_started_ns = time.perf_counter_ns()
        assertions = list(
            (
                await session.execute(
                    select(OkfConcept, OkfConceptAssertion, OkfConceptVersion)
                    .join(
                        OkfConceptAssertion,
                        and_(
                            OkfConceptAssertion.concept_id == OkfConcept.id,
                            OkfConceptAssertion.user_id == user_id,
                            OkfConceptAssertion.status == "active",
                        ),
                    )
                    .join(OkfConceptVersion, current)
                    .where(
                        OkfConcept.user_id == user_id,
                        OkfConcept.id.in_(concept_ids),
                        OkfConcept.status.in_(("active", "contested")),
                        valid,
                    )
                    .order_by(OkfConcept.canonical_key, OkfConceptAssertion.id)
                    .limit(limit + 1)
                )
            ).all()
        )
        assertions = assertions[:limit]
        _trace_stage(trace, "assertion_loading", assertion_started_ns, {"count": len(assertions)})
        if not assertions:
            return []
        version_ids = tuple(item[2].id for item in assertions)
        memory_excluded = or_(
            VoiceSession.client_metadata.is_(None),
            ~VoiceSession.client_metadata.contains({"memory_excluded": True}),
        )
        ranked_sources = (
            select(
                OkfConceptSource.concept_version_id.label("version_id"),
                MemoryItem.id.label("memory_id"),
                func.row_number()
                .over(
                    partition_by=OkfConceptSource.concept_version_id,
                    order_by=MemoryItem.id,
                )
                .label("source_rank"),
            )
            .join(
                MemoryItem,
                and_(MemoryItem.id == OkfConceptSource.memory_id, MemoryItem.user_id == user_id),
            )
            .join(
                VoiceSession,
                and_(
                    VoiceSession.id == MemoryItem.source_session_id,
                    VoiceSession.user_id == user_id,
                ),
            )
            .join(
                User,
                and_(
                    User.id == MemoryItem.user_id,
                    User.status == "active",
                    User.memory_enabled.is_(True),
                ),
            )
            .where(
                OkfConceptSource.user_id == user_id,
                OkfConceptSource.concept_version_id.in_(version_ids),
                OkfConceptSource.evidence_role == "supports",
                MemoryItem.status == "active",
                memory_excluded,
            )
            .subquery()
        )
        provenance_started_ns = time.perf_counter_ns()
        source_rows = (
            await session.execute(
                select(ranked_sources.c.version_id, ranked_sources.c.memory_id).where(
                    ranked_sources.c.source_rank <= _MAX_SOURCE_IDS_PER_ASSERTION
                )
            )
        ).all()
        _trace_stage(
            trace, "provenance_loading", provenance_started_ns, {"count": len(source_rows)}
        )
        source_ids_by_version: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for version_id, memory_id in source_rows:
            source_ids_by_version[version_id].append(memory_id)

        shaping_started_ns = time.perf_counter_ns()
        result = []
        for concept, assertion, version in assertions:
            source_ids = tuple(source_ids_by_version.get(version.id, ()))
            # No active, included source means this materialized claim is not readable.
            if not source_ids:
                continue
            result.append(
                OkfEvidence(
                    concept_id=concept.id,
                    assertion_id=assertion.id,
                    concept_type=concept.concept_type,
                    canonical_key=concept.canonical_key,
                    display_text=version.display_text,
                    status=version.status,
                    source_memory_ids=source_ids,
                    valid_from=version.valid_from,
                    valid_to=version.valid_to,
                )
            )
        _trace_stage(trace, "result_shaping", shaping_started_ns, {"count": len(result)})
        return result

    @staticmethod
    def _result(
        status: KnowledgeDisposition,
        started: float,
        *,
        evidence: tuple[OkfEvidence, ...] = (),
        conflicts: tuple[OkfEvidence, ...] = (),
        reason: str | None = None,
    ) -> KnowledgeResult:
        duration_ms = max(0.0, (time.perf_counter() - started) * 1_000)
        return KnowledgeResult(
            engine="okf",
            status=status,
            evidence=evidence,
            conflicts=conflicts,
            degraded_reason=reason,
            duration_ms=duration_ms,
        )


def _trace_stage(
    trace: Callable[[str, float, dict[str, Any]], None] | None,
    stage: str,
    started_ns: int,
    metadata: dict[str, Any] | None = None,
) -> None:
    if trace is None:
        return
    duration_ms = max(0.0, (time.perf_counter_ns() - started_ns) / 1_000_000)
    trace(
        stage,
        duration_ms,
        {"started_monotonic_ns": started_ns, **(metadata or {})},
    )
