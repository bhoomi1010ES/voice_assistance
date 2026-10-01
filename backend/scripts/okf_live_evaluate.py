"""Run labeled live OKF mode evaluations against explicitly allowlisted dev owners.

Input is JSONL with one case object per line. Output is content-bearing evaluation
evidence and is restricted to the project OKF evidence directory; use synthetic
queries and owner-approved disposable accounts only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import or_, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
EVIDENCE_ROOT = (PROJECT_ROOT / "docs" / "evidence" / "okf").resolve()
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
    run_id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9._:-]+$")
    owner_id: uuid.UUID
    query: str = Field(min_length=1, max_length=2_000)
    expected_result: str = Field(min_length=1, max_length=2_000)
    expected_lifecycle_behavior: str = Field(min_length=1, max_length=500)
    expected_privacy_behavior: str = Field(min_length=1, max_length=500)
    requested_modes: tuple[Literal["rag", "okf", "combined"], ...] = Field(
        min_length=1, max_length=3
    )
    expected_disposition: (
        Literal[
            "direct_answer",
            "continue_with_evidence",
            "no_result",
            "unavailable",
            "conflict",
            "cancelled",
        ]
        | None
    ) = None
    expected_by_mode: dict[
        str,
        Literal[
            "direct_answer",
            "continue_with_evidence",
            "no_result",
            "unavailable",
            "conflict",
            "cancelled",
        ],
    ] = Field(default_factory=dict)
    expected_source_memory_ids: tuple[uuid.UUID, ...] = ()
    allowed_source_memory_ids: tuple[uuid.UUID, ...] = ()
    expected_sources_by_mode: dict[str, tuple[uuid.UUID, ...]] = Field(default_factory=dict)
    allowed_sources_by_mode: dict[str, tuple[uuid.UUID, ...]] = Field(default_factory=dict)
    forbidden_source_memory_ids: tuple[uuid.UUID, ...] = ()
    session_id: uuid.UUID | None = None
    privacy_check: Literal["cross_user", "deleted", "excluded", "superseded"] | None = None

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("requested_modes")
    @classmethod
    def unique_modes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("requested_modes must not contain duplicates")
        return value

    @model_validator(mode="after")
    def require_expected_disposition(self):
        if self.expected_disposition is None and not self.expected_by_mode:
            raise ValueError("an expected disposition is required for scoring")
        if self.expected_disposition is None and set(self.expected_by_mode) != set(
            self.requested_modes
        ):
            raise ValueError("expected_by_mode must label every requested mode")
        if set(self.expected_by_mode) - set(self.requested_modes):
            raise ValueError("expected_by_mode contains a mode that was not requested")
        if set(self.expected_sources_by_mode) - set(self.requested_modes):
            raise ValueError("expected_sources_by_mode contains a mode that was not requested")
        if set(self.allowed_sources_by_mode) - set(self.requested_modes):
            raise ValueError("allowed_sources_by_mode contains a mode that was not requested")
        evidence_dispositions = {"direct_answer", "continue_with_evidence", "conflict"}
        for mode in self.requested_modes:
            disposition = self.expected_by_mode.get(mode, self.expected_disposition)
            expected_sources = set(
                self.expected_sources_by_mode.get(mode, self.expected_source_memory_ids)
            )
            allowed_sources = set(
                self.allowed_sources_by_mode.get(mode, self.allowed_source_memory_ids)
            )
            if disposition in evidence_dispositions and not expected_sources:
                raise ValueError(
                    "expected source memory IDs are required when evidence is expected"
                )
            if not expected_sources.issubset(allowed_sources):
                raise ValueError("allowed source memory IDs must include every expected source")
            if disposition == "no_result" and (expected_sources or allowed_sources):
                raise ValueError("no-result cases must not expect or allow source memory IDs")
        return self


def _percentiles(values: list[float]) -> dict[str, float | None]:
    ordered = sorted(values)
    if not ordered:
        return {"p50": None, "p95": None, "p99": None, "max": None}

    def percentile(p: float) -> float:
        index = round((p / 100) * (len(ordered) - 1))
        return round(ordered[index], 3)

    return {
        "p50": percentile(50),
        "p95": percentile(95),
        "p99": percentile(99),
        "max": round(ordered[-1], 3),
    }


def _assess_approved_gates(
    *,
    mode_metrics: dict[str, dict[str, Any]],
    sync_lag_ms: dict[str, float | None],
    timeout_count: int,
    error_count: int,
    unresolved_sync_job_count: int,
    rag_baseline_p95_ms: float,
) -> dict[str, Any]:
    latency_ceiling_ms = round(rag_baseline_p95_ms * 1.5, 4)
    gates = {
        "rag_labeled_correctness": (mode_metrics["rag"]["correctness"] or 0) >= 0.90,
        "okf_labeled_correctness": (mode_metrics["okf"]["correctness"] or 0) >= 0.90,
        "combined_labeled_correctness": (mode_metrics["combined"]["correctness"] or 0) >= 0.95,
        "expected_no_result_correctness": all(
            mode_metrics[mode]["no_result_correctness"] == 1.0
            for mode in ("rag", "okf", "combined")
            if mode_metrics[mode]["expected_no_result_cases"] > 0
        )
        and all(
            mode_metrics[mode]["expected_no_result_cases"] > 0
            for mode in ("rag", "okf", "combined")
        ),
        "provenance_correctness": all(
            mode_metrics[mode]["provenance_coverage"] == 1.0 for mode in ("rag", "okf", "combined")
        ),
        "cross_owner_leakage": all(
            mode_metrics[mode]["privacy_return_counts"]["cross_user"] == 0
            for mode in ("rag", "okf", "combined")
        ),
        "deleted_fact_return": all(
            mode_metrics[mode]["privacy_return_counts"]["deleted"] == 0
            for mode in ("rag", "okf", "combined")
        ),
        "excluded_fact_return": all(
            mode_metrics[mode]["privacy_return_counts"]["excluded"] == 0
            for mode in ("rag", "okf", "combined")
        ),
        "superseded_stale_fact_wins": all(
            mode_metrics[mode]["privacy_return_counts"]["superseded"] == 0
            for mode in ("rag", "okf", "combined")
        ),
        "retrieval_timeouts": timeout_count == 0,
        "retrieval_errors": error_count == 0,
        "unresolved_sync_jobs": unresolved_sync_job_count == 0,
        "controlled_corpus_sync_lag": sync_lag_ms.get("max") is not None
        and sync_lag_ms["max"] <= 5_000,
        "retrieval_p95_vs_frozen_rag": all(
            mode_metrics[mode]["latency_ms"]["p95"] is not None
            and mode_metrics[mode]["latency_ms"]["p95"] <= latency_ceiling_ms
            for mode in ("rag", "okf", "combined")
        ),
    }
    return {
        "status": "PASS" if all(gates.values()) else "FAIL",
        "approved_by_user": True,
        "approval_date": "2026-09-30",
        "thresholds": {
            "rag_correctness": 0.90,
            "okf_correctness": 0.90,
            "combined_correctness": 0.95,
            "no_result_correctness": 1.0,
            "provenance_coverage": 1.0,
            "privacy_and_stale_returns": 0,
            "timeouts": 0,
            "retrieval_errors": 0,
            "unresolved_sync_jobs": 0,
            "controlled_corpus_sync_lag_max_ms": 5_000,
            "frozen_rag_baseline_p95_ms": rag_baseline_p95_ms,
            "retrieval_p95_ceiling_ms": latency_ceiling_ms,
        },
        "gates": gates,
        "failed_gates": [name for name, passed in gates.items() if not passed],
    }


def _validate_output_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == EVIDENCE_ROOT or EVIDENCE_ROOT not in resolved.parents:
        raise ValueError("output must be a new file under docs/evidence/okf")
    if resolved.exists():
        raise ValueError("output already exists; choose a unique run file")
    return resolved


def _read_cases(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = EvaluationCase.model_validate_json(line)
        except ValidationError as error:
            raise ValueError(
                f"invalid case input on line {line_number}: {error.error_count()} errors"
            ) from error
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
        if len(cases) > 500:
            raise ValueError("input contains more than the 500-case safety limit")
    if not cases:
        raise ValueError("input contains no evaluation cases")
    return cases


async def _active_owned_sources(
    session, owner_id: uuid.UUID, memory_ids: set[uuid.UUID]
) -> set[uuid.UUID]:
    from app.models import (
        MemoryItem,
        OkfConcept,
        OkfConceptAssertion,
        OkfConceptSource,
        OkfConceptVersion,
        VoiceSession,
    )

    if not memory_ids:
        return set()
    rows = await session.scalars(
        select(OkfConceptSource.memory_id)
        .join(
            OkfConceptVersion,
            (OkfConceptVersion.id == OkfConceptSource.concept_version_id)
            & (OkfConceptVersion.user_id == OkfConceptSource.user_id),
        )
        .join(
            OkfConceptAssertion,
            (OkfConceptAssertion.id == OkfConceptVersion.assertion_id)
            & (OkfConceptAssertion.user_id == OkfConceptVersion.user_id)
            & (OkfConceptAssertion.current_version == OkfConceptVersion.version),
        )
        .join(
            OkfConcept,
            (OkfConcept.id == OkfConceptAssertion.concept_id)
            & (OkfConcept.user_id == OkfConceptAssertion.user_id),
        )
        .join(
            MemoryItem,
            (MemoryItem.id == OkfConceptSource.memory_id)
            & (MemoryItem.user_id == OkfConceptSource.user_id),
        )
        .outerjoin(VoiceSession, VoiceSession.id == MemoryItem.source_session_id)
        .where(
            OkfConceptSource.user_id == owner_id,
            OkfConceptSource.memory_id.in_(memory_ids),
            OkfConceptVersion.status == "active",
            OkfConceptAssertion.status == "active",
            OkfConcept.status.in_(("active", "contested")),
            MemoryItem.status == "active",
            or_(
                MemoryItem.source_session_id.is_(None),
                VoiceSession.id.is_(None),
                VoiceSession.client_metadata["memory_excluded"].astext.is_distinct_from("true"),
            ),
        )
    )
    return set(rows.all())


async def _evaluate(args: argparse.Namespace) -> dict[str, Any]:
    from app.core.config import Settings
    from app.db.session import Database
    from app.knowledge import KnowledgeSelector, build_knowledge_context
    from app.knowledge.engines import configured_engines
    from app.memory.providers import RemoteEmbeddingProvider, RemoteReranker
    from app.memory.retrieval import MemoryRetrievalService, build_memory_query_plan
    from app.models import MemoryChunk, MemoryItem, OkfSyncJob, User, VoiceSession
    from app.okf.retrieval import OkfRetrievalService
    from app.okf.shadow import ShadowReadCapacity, schedule_shadow_read
    from app.okf.types import KnowledgeRequest

    settings = Settings()
    if settings.app_env.lower() not in {"development", "test"}:
        raise ValueError("live evaluation is limited to development/test")
    if not settings.okf_evaluation_enabled:
        raise ValueError("set OKF_EVALUATION_ENABLED=true for a controlled local run")
    if not settings.okf_enabled or not settings.okf_sync_enabled:
        raise ValueError("live evaluation requires OKF and sync enabled")
    allowed = set(settings.okf_evaluation_user_ids)
    cases = _read_cases(args.input)
    if any(case.run_id != args.run_id for case in cases):
        raise ValueError("every case run_id must match --run-id")
    outside_allowlist = sorted(
        {str(case.owner_id) for case in cases if case.owner_id not in allowed}
    )
    if outside_allowlist:
        raise ValueError("case owner is not in OKF_EVALUATION_USER_IDS")
    if args.shadow:
        if not settings.okf_shadow_reads or settings.knowledge_mode != "rag":
            raise ValueError("shadow requires OKF_SHADOW_READS=true and KNOWLEDGE_MODE=rag")
        if not set(settings.okf_shadow_user_ids).issuperset({case.owner_id for case in cases}):
            raise ValueError("every shadow owner must also be in OKF_SHADOW_USER_IDS")

    output_path = _validate_output_path(args.output)
    database = Database(settings)
    if database.session_factory is None:
        raise ValueError("database is not configured")
    embedding = RemoteEmbeddingProvider(settings)
    reranker = RemoteReranker(settings)
    await embedding.initialize()
    await reranker.initialize()

    diagnostic_case_id: str | None = None
    diagnostic_mode: str | None = None
    diagnostic_events: list[dict[str, Any]] = []

    def capture_retrieval_trace(stage: str, duration_ms: float, fields: dict[str, Any]) -> None:
        if diagnostic_case_id == "C08-semantic-retrieval":
            diagnostic_events.append(
                {
                    "mode": diagnostic_mode,
                    "stage": stage,
                    "duration_ms": round(duration_ms, 3),
                    "fields": fields,
                }
            )

    rag_service = MemoryRetrievalService(
        settings,
        embedding_provider=embedding,
        reranker=reranker,
        diagnostic_trace=capture_retrieval_trace,
    )
    capacity = ShadowReadCapacity(settings.okf_shadow_max_concurrent)
    shadow_tasks: set[asyncio.Task[None]] = set()
    shadow_observations: dict[str, dict[str, Any]] = {}
    case_records: list[dict[str, Any]] = []
    latencies: dict[str, list[float]] = {mode: [] for mode in ("rag", "okf", "combined")}
    sync_lag_ms: list[float] = []
    try:
        for case in cases:
            diagnostic_case_id = case.case_id
            diagnostic_mode = None
            diagnostic_event_start = len(diagnostic_events)
            semantic_source_diagnostic: dict[str, Any] | None = None
            record: dict[str, Any] = {
                "run_id": args.run_id,
                "case_id": case.case_id,
                "owner_id": str(case.owner_id),
                "query": case.query,
                "expected_result": case.expected_result,
                "expected_lifecycle_behavior": case.expected_lifecycle_behavior,
                "expected_privacy_behavior": case.expected_privacy_behavior,
                "expected_disposition": case.expected_disposition,
                "expected_by_mode": case.expected_by_mode,
                "expected_source_memory_ids": [
                    str(value) for value in case.expected_source_memory_ids
                ],
                "allowed_source_memory_ids": [
                    str(value) for value in case.allowed_source_memory_ids
                ],
                "expected_sources_by_mode": {
                    mode: [str(value) for value in values]
                    for mode, values in case.expected_sources_by_mode.items()
                },
                "allowed_sources_by_mode": {
                    mode: [str(value) for value in values]
                    for mode, values in case.allowed_sources_by_mode.items()
                },
                "forbidden_source_memory_ids": [
                    str(value) for value in case.forbidden_source_memory_ids
                ],
                "privacy_check": case.privacy_check,
                "actual": {},
                "shadow_status": "not_requested",
                "failure_reason": [],
            }
            async with database.session_factory() as session:
                owner = await session.get(User, case.owner_id)
                if owner is None or owner.status != "active" or not owner.memory_enabled:
                    record["case_status"] = "precondition_failed"
                    record["failure_reason"].append("owner_inactive_or_memory_disabled")
                    case_records.append(record)
                    continue
                memory_excluded = False
                if case.session_id is not None:
                    voice_session = await session.scalar(
                        select(VoiceSession).where(
                            VoiceSession.id == case.session_id,
                            VoiceSession.user_id == case.owner_id,
                        )
                    )
                    if voice_session is None:
                        record["case_status"] = "precondition_failed"
                        record["failure_reason"].append("session_not_owned")
                        case_records.append(record)
                        continue
                    memory_excluded = (voice_session.client_metadata or {}).get(
                        "memory_excluded"
                    ) is True

                if case.case_id == "C08-semantic-retrieval":
                    source_ids = case.expected_sources_by_mode.get("rag", ())
                    source_rows = list(
                        (
                            await session.scalars(
                                select(MemoryItem).where(
                                    MemoryItem.id.in_(source_ids),
                                    MemoryItem.user_id == case.owner_id,
                                )
                            )
                        ).all()
                    )
                    chunks = list(
                        (
                            await session.execute(
                                select(
                                    MemoryChunk.memory_id,
                                    MemoryChunk.embedding.is_not(None),
                                ).where(
                                    MemoryChunk.user_id == case.owner_id,
                                    MemoryChunk.memory_id.in_(source_ids),
                                )
                            )
                        ).all()
                    )
                    for source in source_rows:
                        tagged_for_run = (source.metadata_json or {}).get(
                            "okf_run_id"
                        ) == args.run_id
                        semantic_source_diagnostic = {
                            "memory_id": str(source.id),
                            "owner_id": str(source.user_id),
                            "run_tag_verified": tagged_for_run,
                            "source_text": source.content if tagged_for_run else None,
                            "memory_type": source.memory_type,
                            "subject": source.subject,
                            "predicate": source.predicate,
                            "object": source.object_json,
                            "status": source.status,
                            "source_session_id": (
                                str(source.source_session_id) if source.source_session_id else None
                            ),
                            "session_excluded": memory_excluded,
                            "chunk_count": sum(
                                1 for memory_id, _ in chunks if memory_id == source.id
                            ),
                            "embedded_chunk_count": sum(
                                1
                                for memory_id, embedded in chunks
                                if memory_id == source.id and embedded
                            ),
                        }
                        break
                    cross_owner_rows = list(
                        (
                            await session.scalars(
                                select(MemoryItem).where(
                                    MemoryItem.id.in_(case.forbidden_source_memory_ids),
                                    MemoryItem.user_id != case.owner_id,
                                )
                            )
                        ).all()
                    )
                    cross_owner_controls = [
                        {
                            "memory_id": str(source.id),
                            "owner_id": str(source.user_id),
                            "run_tag_verified": (source.metadata_json or {}).get("okf_run_id")
                            == args.run_id,
                            "source_text": (
                                source.content
                                if (source.metadata_json or {}).get("okf_run_id") == args.run_id
                                else None
                            ),
                            "status": source.status,
                        }
                        for source in cross_owner_rows
                    ]
                    record["semantic_diagnostic"] = {
                        "normalized_query": build_memory_query_plan(case.query).normalized_query,
                        "source": semantic_source_diagnostic,
                        "other_owner_controls": cross_owner_controls,
                    }

                # Only expected OKF sources need materialized OKF provenance.
                # RAG-only transient/summary evidence may be valid without an OKF concept.
                precondition_source_ids = set()
                if "okf" in case.requested_modes:
                    precondition_source_ids.update(
                        case.expected_sources_by_mode.get("okf", case.expected_source_memory_ids)
                    )
                if precondition_source_ids:
                    expected_ids = precondition_source_ids
                    owned_active = set(
                        (
                            await session.scalars(
                                select(MemoryItem.id).where(
                                    MemoryItem.id.in_(expected_ids),
                                    MemoryItem.user_id == case.owner_id,
                                    MemoryItem.status == "active",
                                )
                            )
                        ).all()
                    )
                    linked_active = await _active_owned_sources(
                        session, case.owner_id, expected_ids
                    )
                    completed_jobs = list(
                        (
                            await session.scalars(
                                select(OkfSyncJob).where(
                                    OkfSyncJob.user_id == case.owner_id,
                                    OkfSyncJob.memory_id.in_(expected_ids),
                                    OkfSyncJob.event_type == "upsert_memory",
                                    OkfSyncJob.status == "completed",
                                )
                            )
                        ).all()
                    )
                    completed_memory_ids = {job.memory_id for job in completed_jobs}
                    if not expected_ids.issubset(
                        owned_active & linked_active & completed_memory_ids
                    ):
                        record["case_status"] = "precondition_failed"
                        record["failure_reason"].append(
                            "expected_source_not_synced_with_active_provenance"
                        )
                        case_records.append(record)
                        continue
                    for job in completed_jobs:
                        memory = await session.get(MemoryItem, job.memory_id)
                        if memory is not None and job.completed_at is not None:
                            sync_lag_ms.append(
                                max(
                                    0.0,
                                    (job.completed_at - memory.created_at).total_seconds() * 1000,
                                )
                            )

                request = KnowledgeRequest(
                    user_id=case.owner_id,
                    query=case.query,
                    now=datetime.now(UTC),
                    session_id=case.session_id,
                    memory_enabled=owner.memory_enabled,
                    memory_excluded=memory_excluded,
                )
                rag_result = None
                rag_evidence_ids: tuple[uuid.UUID, ...] = ()
                rag_latency_ms = 0.0
                record["case_status"] = "scored"
                for mode in case.requested_modes:
                    diagnostic_mode = mode
                    mode_settings = settings.model_copy(update={"knowledge_mode": mode})
                    selector = KnowledgeSelector(
                        mode,
                        configured_engines(
                            mode_settings,
                            rag_service=rag_service,
                            okf_service=OkfRetrievalService(mode_settings),
                        ),
                    )
                    started_ns = time.perf_counter_ns()
                    selection = await selector.retrieve(session, request)
                    elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
                    latencies[mode].append(elapsed_ms)
                    if mode == "rag":
                        rag_latency_ms = elapsed_ms
                        rag_result = selection.results[0].rag_result
                        rag_evidence_ids = selection.evidence_ids
                    context = build_knowledge_context(
                        selection,
                        max_chars=min(
                            settings.memory_context_max_chars, settings.okf_context_max_chars
                        ),
                    )
                    source_ids = set(selection.evidence_ids)
                    owned_sources = (
                        set(
                            (
                                await session.scalars(
                                    select(MemoryItem.id).where(
                                        MemoryItem.id.in_(source_ids),
                                        MemoryItem.user_id == case.owner_id,
                                        MemoryItem.status == "active",
                                    )
                                )
                            ).all()
                        )
                        if source_ids
                        else set()
                    )
                    forbidden_returned = sorted(
                        str(memory_id)
                        for memory_id in source_ids & set(case.forbidden_source_memory_ids)
                    )
                    okf_items = [
                        evidence
                        for result in selection.results
                        if result.okf_result is not None
                        for evidence in result.okf_result.evidence
                    ]
                    okf_source_ids = {
                        source_id for item in okf_items for source_id in item.source_memory_ids
                    }
                    active_okf_sources = await _active_owned_sources(
                        session, case.owner_id, okf_source_ids
                    )
                    provenance_valid = source_ids.issubset(
                        owned_sources
                    ) and okf_source_ids.issubset(active_okf_sources)
                    expected_disposition = case.expected_by_mode.get(
                        mode, case.expected_disposition
                    )
                    expected_ids = set(
                        case.expected_sources_by_mode.get(mode, case.expected_source_memory_ids)
                    )
                    allowed_ids = set(
                        case.allowed_sources_by_mode.get(mode, case.allowed_source_memory_ids)
                    )
                    missing_expected_ids = sorted(str(value) for value in expected_ids - source_ids)
                    unexpected_source_ids = sorted(str(value) for value in source_ids - allowed_ids)
                    matched = (
                        (
                            expected_disposition is None
                            or selection.disposition.value == expected_disposition
                        )
                        and expected_ids.issubset(source_ids)
                        and source_ids.issubset(allowed_ids)
                    )
                    failure_reasons = []
                    if not matched:
                        if (
                            expected_disposition is not None
                            and selection.disposition.value != expected_disposition
                        ):
                            failure_reasons.append("disposition_mismatch")
                        if missing_expected_ids:
                            failure_reasons.append("expected_source_missing")
                        if unexpected_source_ids:
                            failure_reasons.append("unexpected_source_returned")
                    if not provenance_valid:
                        failure_reasons.append("provenance_invalid")
                    if forbidden_returned:
                        failure_reasons.append("forbidden_source_returned")
                    record["actual"][mode] = {
                        "disposition": selection.disposition.value,
                        "reason": selection.reason,
                        "engine_results": [
                            {
                                "engine": result.engine,
                                "disposition": result.disposition.value,
                                "reason": result.reason,
                            }
                            for result in selection.results
                        ],
                        "evidence_ids": [str(value) for value in selection.evidence_ids],
                        "facts": [
                            {
                                "key": fact.key,
                                "kind": fact.kind,
                                "text": fact.text,
                                "source_memory_ids": [
                                    str(value) for value in fact.source_memory_ids
                                ],
                            }
                            for fact in selection.facts
                        ],
                        "expected_source_memory_ids": sorted(str(value) for value in expected_ids),
                        "allowed_source_memory_ids": sorted(str(value) for value in allowed_ids),
                        "missing_expected_source_ids": missing_expected_ids,
                        "unexpected_source_ids": unexpected_source_ids,
                        "okf_concept_assertion_ids": [
                            {
                                "concept_id": str(item.concept_id),
                                "assertion_id": str(item.assertion_id),
                            }
                            for item in okf_items
                        ],
                        "provenance_valid": provenance_valid,
                        "forbidden_source_ids_returned": forbidden_returned,
                        "expected_match": matched,
                        "pass": matched and provenance_valid and not forbidden_returned,
                        "failure_reason": failure_reasons,
                        "latency_ms": round(elapsed_ms, 3),
                        "okf_service_latency_ms": next(
                            (
                                round(result.okf_result.duration_ms, 3)
                                for result in selection.results
                                if result.okf_result is not None
                            ),
                            None,
                        ),
                        "context_characters": len(context.text),
                        "context_evidence_ids": [str(value) for value in context.evidence_ids],
                    }
                    record["failure_reason"].extend(
                        f"{mode}:{reason}" for reason in failure_reasons
                    )

                # Schedule after the RAG result is captured. The shared scheduler returns
                # immediately; shadow completion is collected after all cases are issued.
                if args.shadow:
                    if "rag" not in case.requested_modes or rag_result is None:
                        record["shadow_status"] = "not_scheduled"
                        record["failure_reason"].append("shadow_requires_rag_result")
                    else:
                        case_key = case.case_id
                        rag_disposition = record["actual"]["rag"]["disposition"]

                        def capture_shadow(observation, key=case_key):
                            shadow_observations[key] = dict(observation.fields)

                        record["shadow_status"] = schedule_shadow_read(
                            settings=settings,
                            session_factory=database.session_factory,
                            user_id=case.owner_id,
                            session_id=case.session_id,
                            query=case.query,
                            now=request.now,
                            rag_disposition=rag_disposition,
                            rag_evidence_ids=rag_evidence_ids,
                            rag_latency_ms=rag_latency_ms,
                            capacity=capacity,
                            task_set=shadow_tasks,
                            correlation={"run_id": args.run_id, "case_id": case.case_id},
                            on_complete=capture_shadow,
                        )
                record["rag_response_ready_ms"] = (
                    round(rag_latency_ms, 3) if rag_result is not None else None
                )
                if case.case_id == "C08-semantic-retrieval":
                    record["semantic_diagnostic"]["pipeline"] = diagnostic_events[
                        diagnostic_event_start:
                    ]
                    record["semantic_diagnostic"]["rag_final"] = record["actual"].get("rag")
                    semantic_probes = (
                        ("where_do_i_live", "Where do I live?", True),
                        ("what_city_do_i_live_in", "What city do I live in?", True),
                        ("where_is_my_home", "Where is my home?", True),
                        ("what_is_my_home_location", "What is my home location?", True),
                        ("which_city_is_my_residence", "Which city is my residence?", True),
                        ("tell_me_where_i_stay", "Tell me where I stay.", True),
                        ("brother_location_negative", "Where does my brother live?", False),
                        ("named_person_location_negative", "Where does Alice live?", False),
                        ("office_location_negative", "Where is my office?", False),
                        ("work_location_negative", "Where do I work?", False),
                        (
                            "other_owner_location_negative",
                            f"Where does {args.run_id.lower()}-other-owner live?",
                            False,
                        ),
                    )
                    probe_settings = settings.model_copy(update={"knowledge_mode": "rag"})
                    probe_selector = KnowledgeSelector(
                        "rag",
                        configured_engines(
                            probe_settings,
                            rag_service=rag_service,
                            okf_service=OkfRetrievalService(probe_settings),
                        ),
                    )
                    probe_results = []
                    for probe_id, probe_query, expected_positive in semantic_probes:
                        diagnostic_mode = f"probe:{probe_id}"
                        event_start = len(diagnostic_events)
                        probe_started_ns = time.perf_counter_ns()
                        async with database.session_factory() as probe_session:
                            probe_selection = await probe_selector.retrieve(
                                probe_session,
                                request.model_copy(update={"query": probe_query}),
                            )
                        probe_results.append(
                            {
                                "probe_id": probe_id,
                                "query": probe_query,
                                "expected": (
                                    "expected_source" if expected_positive else "no_result"
                                ),
                                "actual_disposition": probe_selection.disposition.value,
                                "actual_reason": probe_selection.reason,
                                "evidence_ids": [
                                    str(value) for value in probe_selection.evidence_ids
                                ],
                                "facts": [
                                    {
                                        "key": fact.key,
                                        "text": fact.text,
                                        "source_memory_ids": [
                                            str(value) for value in fact.source_memory_ids
                                        ],
                                    }
                                    for fact in probe_selection.facts
                                ],
                                "latency_ms": round(
                                    (time.perf_counter_ns() - probe_started_ns) / 1_000_000,
                                    3,
                                ),
                                "pipeline": diagnostic_events[event_start:],
                            }
                        )
                    record["semantic_diagnostic"]["probes"] = probe_results
                    diagnostic_mode = None
                record["pass"] = record["case_status"] == "scored" and not record["failure_reason"]
                case_records.append(record)

        if shadow_tasks:
            await asyncio.gather(*tuple(shadow_tasks), return_exceptions=True)
        for record in case_records:
            if record.get("shadow_status") == "scheduled":
                record["shadow_observation"] = shadow_observations.get(record["case_id"])
                if record["shadow_observation"] is None:
                    record["shadow_status"] = "error"
                    record["pass"] = False

        mode_metrics: dict[str, Any] = {}
        for mode in ("rag", "okf", "combined"):
            rows = [
                (record, record.get("actual", {}).get(mode))
                for record in case_records
                if record.get("case_status") == "scored" and mode in record.get("actual", {})
            ]
            expected_no_result = [
                actual
                for record, actual in rows
                if record.get("expected_by_mode", {}).get(mode, record.get("expected_disposition"))
                == "no_result"
            ]
            returned_facts = sum(len(actual["evidence_ids"]) for _, actual in rows)
            provenance_valid_facts = sum(
                len(actual["evidence_ids"]) if actual["provenance_valid"] else 0
                for _, actual in rows
            )
            engine_call_count = sum(len(actual.get("engine_results", [])) for _, actual in rows)
            mode_timeout_count = sum(
                result["reason"] in {"engine_timeout", "retrieval_timeout"}
                for _, actual in rows
                for result in actual.get("engine_results", [])
            )
            mode_error_count = sum(
                result["reason"]
                in {"engine_unavailable", "database_unavailable", "retrieval_degraded"}
                for _, actual in rows
                for result in actual.get("engine_results", [])
            )
            mode_metrics[mode] = {
                "evaluated_cases": len(rows),
                "correctness": (
                    round(sum(actual["expected_match"] for _, actual in rows) / len(rows), 4)
                    if rows
                    else None
                ),
                "no_result_correctness": (
                    round(
                        sum(actual["disposition"] == "no_result" for actual in expected_no_result)
                        / len(expected_no_result),
                        4,
                    )
                    if expected_no_result
                    else None
                ),
                "expected_no_result_cases": len(expected_no_result),
                "provenance_coverage": (
                    round(provenance_valid_facts / returned_facts, 4) if returned_facts else None
                ),
                "engine_call_count": engine_call_count,
                "timeout_count": mode_timeout_count,
                "timeout_rate": (
                    round(mode_timeout_count / engine_call_count, 4) if engine_call_count else None
                ),
                "retrieval_error_count": mode_error_count,
                "retrieval_error_rate": (
                    round(mode_error_count / engine_call_count, 4) if engine_call_count else None
                ),
                "cross_user_or_forbidden_returns": sum(
                    len(actual["forbidden_source_ids_returned"]) for _, actual in rows
                ),
                "privacy_return_counts": {
                    check: sum(
                        len(actual["forbidden_source_ids_returned"])
                        for record, actual in rows
                        if record.get("privacy_check") == check
                    )
                    for check in ("cross_user", "deleted", "excluded", "superseded")
                },
                "latency_ms": _percentiles(latencies[mode]),
            }
        shadow_rows = [
            record.get("shadow_observation")
            for record in case_records
            if record.get("shadow_observation") is not None
        ]
        shadow_latencies = [
            float(item["latency_ms"]) for item in shadow_rows if item.get("latency_ms") is not None
        ]
        shadow_overlaps = [int(item.get("overlap_count", 0)) for item in shadow_rows]
        shadow_provenance = [
            float(item["okf_provenance_coverage"])
            for item in shadow_rows
            if item.get("okf_provenance_coverage") is not None
        ]
        run_source_ids = {
            source_id
            for case in cases
            for source_id in (
                *case.expected_source_memory_ids,
                *case.allowed_source_memory_ids,
                *case.forbidden_source_memory_ids,
                *(value for values in case.expected_sources_by_mode.values() for value in values),
                *(value for values in case.allowed_sources_by_mode.values() for value in values),
            )
        }
        run_jobs = []
        if run_source_ids:
            async with database.session_factory() as job_session:
                run_jobs = list(
                    (
                        await job_session.scalars(
                            select(OkfSyncJob).where(OkfSyncJob.memory_id.in_(run_source_ids))
                        )
                    ).all()
                )
        unresolved_jobs = [
            job for job in run_jobs if job.status in {"pending", "retry_wait", "running", "dead"}
        ]
        sync_lag_percentiles = _percentiles(sync_lag_ms)
        timeout_count = sum(
            result["reason"] in {"engine_timeout", "retrieval_timeout"}
            for record in case_records
            for actual in record.get("actual", {}).values()
            for result in actual.get("engine_results", [])
        )
        error_count = sum(
            result["reason"] in {"engine_unavailable", "database_unavailable", "retrieval_degraded"}
            for record in case_records
            for actual in record.get("actual", {}).values()
            for result in actual.get("engine_results", [])
        )
        engine_call_count = sum(
            len(actual.get("engine_results", []))
            for record in case_records
            for actual in record.get("actual", {}).values()
        )
        summary = {
            "run_id": args.run_id,
            "case_count": len(case_records),
            "scored_count": sum(record.get("case_status") == "scored" for record in case_records),
            "precondition_failed_count": sum(
                record.get("case_status") == "precondition_failed" for record in case_records
            ),
            "failed_case_ids": [
                record["case_id"] for record in case_records if not record.get("pass")
            ],
            "latency_ms": {mode: _percentiles(values) for mode, values in latencies.items()},
            "mode_metrics": mode_metrics,
            "cross_user_leak_count": sum(
                metric["privacy_return_counts"]["cross_user"] for metric in mode_metrics.values()
            ),
            "deleted_fact_return_count": sum(
                metric["privacy_return_counts"]["deleted"] for metric in mode_metrics.values()
            ),
            "excluded_fact_return_count": sum(
                metric["privacy_return_counts"]["excluded"] for metric in mode_metrics.values()
            ),
            "superseded_stale_fact_return_count": sum(
                metric["privacy_return_counts"]["superseded"] for metric in mode_metrics.values()
            ),
            "engine_call_count": engine_call_count,
            "retrieval_timeout_count": timeout_count,
            "retrieval_timeout_rate": (
                round(timeout_count / engine_call_count, 4) if engine_call_count else None
            ),
            "retrieval_error_count": error_count,
            "retrieval_error_rate": (
                round(error_count / engine_call_count, 4) if engine_call_count else None
            ),
            "sync_jobs": {
                "total": len(run_jobs),
                "completed": sum(job.status == "completed" for job in run_jobs),
                "cancelled": sum(job.status == "cancelled" for job in run_jobs),
                "retry_wait": sum(job.status == "retry_wait" for job in run_jobs),
                "dead": sum(job.status == "dead" for job in run_jobs),
                "pending_or_running": sum(job.status in {"pending", "running"} for job in run_jobs),
                "unresolved": len(unresolved_jobs),
            },
            "sync_lag_ms": sync_lag_percentiles,
            "shadow_latency_ms": _percentiles(shadow_latencies),
            "shadow_success_count": sum(
                item.get("shadow_status") == "completed" for item in shadow_rows
            ),
            "shadow_timeout_count": sum(
                item.get("shadow_status") == "timeout" for item in shadow_rows
            ),
            "shadow_error_count": sum(
                record.get("shadow_status") == "error" for record in case_records
            ),
            "shadow_counts": {
                status: sum(record.get("shadow_status") == status for record in case_records)
                for status in ("scheduled", "disabled", "skipped", "error")
            },
            "shadow_eligible_count": sum("rag" in case.requested_modes for case in cases)
            if args.shadow
            else 0,
            "shadow_overlap_count": sum(shadow_overlaps),
            "shadow_no_result_agreement_count": sum(
                item.get("no_result_agreement") is True for item in shadow_rows
            ),
            "shadow_no_result_comparison_count": sum(
                item.get("no_result_agreement") is not None for item in shadow_rows
            ),
            "shadow_provenance_coverage": (
                round(sum(shadow_provenance) / len(shadow_provenance), 4)
                if shadow_provenance
                else None
            ),
            "threshold_assessment": _assess_approved_gates(
                mode_metrics=mode_metrics,
                sync_lag_ms=sync_lag_percentiles,
                timeout_count=timeout_count,
                error_count=error_count,
                unresolved_sync_job_count=len(unresolved_jobs),
                rag_baseline_p95_ms=args.rag_baseline_p95_ms,
            ),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("x", encoding="utf-8") as output:
            for record in case_records:
                output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            output.write(json.dumps({"summary": summary}, separators=(",", ":")) + "\n")
        return {"summary": summary, "output": str(output_path)}
    finally:
        await embedding.close()
        await reranker.close()
        await database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSONL synthetic case manifest")
    parser.add_argument(
        "--output", type=Path, required=True, help="New JSONL report under docs/evidence/okf"
    )
    parser.add_argument("--run-id", required=True, help="Unique content-free run label")
    parser.add_argument(
        "--rag-baseline-p95-ms",
        type=float,
        default=431.5948,
        help="Frozen live RAG baseline P95 approved for this run (default: Phase6 F3 431.5948 ms)",
    )
    parser.add_argument(
        "--shadow",
        action="store_true",
        help="Submit RAG cases to the shared non-blocking shadow scheduler",
    )
    args = parser.parse_args()
    if (
        not args.run_id
        or len(args.run_id) > 96
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for char in args.run_id
        )
    ):
        parser.error("--run-id must use 1-96 letters, numbers, dot, underscore, colon, or hyphen")
    try:
        result = asyncio.run(_evaluate(args))
    except (OSError, ValueError, ValidationError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
