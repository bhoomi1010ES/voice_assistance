"""Run the Phase 6 retrieval acceptance evaluation against the real stack.

This evaluator deliberately uses the application MemoryWriter, embedding job
handler, pgvector query, PostgreSQL FTS query, RRF fusion, and configured
reranker.  It creates isolated synthetic users and removes them on exit.
"""

# The script is run from the repository root, so add the backend application
# package before importing the production services under evaluation.

from __future__ import annotations

# The evaluator is intentionally runnable from the repository root without
# installing the backend package as an editable distribution.
# ruff: noqa: E402
import asyncio
import json
import math
import os
import sys
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import quantiles
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import Settings
from app.memory.jobs import MemoryJobWorker
from app.memory.policy import ExtractionCandidate
from app.memory.providers import RemoteEmbeddingProvider, RemoteReranker
from app.memory.retrieval import (
    _base_memory_query,
    apply_relevance_boundary,
    dense_retrieve,
    fts_retrieve,
    fuse_candidates,
    rerank_fused,
    should_run_structured_retrieval,
    structured_retrieve,
)
from app.memory.types import (
    MemoryIntent,
    MemorySourceKind,
    MemoryType,
    build_memory_query_plan,
)
from app.memory.writer import MemoryWriter
from app.models import MemoryItem, MemoryJob, User

FIXTURE_PATH = BACKEND / "tests" / "fixtures" / "phase6_retrieval_corpus_v1.json"
EVIDENCE_DIR = ROOT / "docs" / "evidence" / "phase6"

FIXED_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
OWNER_EMAIL = "phase6-retrieval-evaluation-owner@example.invalid"
OTHER_EMAIL = "phase6-retrieval-evaluation-other@example.invalid"

RECORD_OVERRIDES: dict[str, dict[str, Any]] = {
    "work-mumbai": {"subject": "user", "predicate": "work_location"},
    "work-remote-old": {"subject": "user", "predicate": "work_location"},
    "tea-old": {"subject": "user", "predicate": "beverage"},
    "tea-new": {"subject": "user", "predicate": "beverage"},
    "rahul": {"subject": "Rahul", "predicate": "colleague"},
    "name": {"subject": "user", "predicate": "name"},
    "trip": {
        "subject": "user",
        "predicate": "visited",
        "occurred_start_at": FIXED_NOW.replace(day=8),
    },
    "timezone": {"subject": "user", "predicate": "timezone"},
    "negation": {"subject": "user", "predicate": "coffee"},
    "injection": {"subject": "user", "predicate": "saved_instruction"},
    "deleted-memory": {
        "content": "I used to live in Jaipur.",
        "memory_type": "fact",
        "subject": "user",
        "predicate": "home_location",
    },
    "food-distractor": {
        "content": "I prefer green vegetables.",
        "memory_type": "preference",
        "subject": "user",
        "predicate": "food",
    },
    "project-atlas": {
        "content": "Project Atlas beta launches on 2026-09-20.",
        "memory_type": "project",
        "subject": "Project Atlas",
        "predicate": "milestone",
    },
    "weekend-hiking": {
        "content": "I enjoy hiking on weekends.",
        "memory_type": "routine",
        "subject": "user",
        "predicate": "weekend_activity",
    },
    "weekend-cycling": {
        "content": "I enjoy cycling on weekends.",
        "memory_type": "routine",
        "subject": "user",
        "predicate": "weekend_activity",
    },
    "travel-train": {
        "content": "My favorite way to commute is by train.",
        "memory_type": "preference",
        "subject": "user",
        "predicate": "commute",
    },
    "cross-user-secret": {
        "content": "The other user's confidential project is Apollo.",
        "memory_type": "project",
        "subject": "Apollo",
        "predicate": "confidential_project",
    },
}

EXTRA_CASES = [
    {
        "id": "project-lookup",
        "category": "work_project",
        "query": "What is happening with Project Atlas?",
        "expected_ids": ["project-atlas"],
        "excluded_ids": [],
    },
    {
        "id": "multiple-weekend-activities",
        "category": "multiple_relevant",
        "query": "What do I enjoy doing on weekends?",
        "expected_ids": ["weekend-hiking", "weekend-cycling"],
        "excluded_ids": [],
    },
    {
        "id": "low-overlap-commute",
        "category": "low_lexical_overlap",
        "query": "How do I usually get around?",
        "expected_ids": ["travel-train"],
        "excluded_ids": [],
    },
    {
        "id": "high-overlap-location",
        "category": "high_lexical_overlap",
        "query": "Which Mumbai location is my current work location?",
        "expected_ids": ["work-mumbai"],
        "excluded_ids": ["work-remote-old"],
    },
    {
        "id": "distractor-preference",
        "category": "distractor",
        "query": "What beverage do I prefer?",
        "expected_ids": ["tea-new", "tea-old"],
        "excluded_ids": ["food-distractor"],
    },
]

ACTIVE_REPLACEMENTS = {"tea-old": "tea-new", "work-remote-old": "work-mumbai"}


def load_fixture() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    records = list(fixture["records"])
    known = {record["id"] for record in records}
    for record_id, override in RECORD_OVERRIDES.items():
        if record_id not in known:
            records.append(
                {
                    "id": record_id,
                    "content": override["content"],
                    "memory_type": override["memory_type"],
                    "trust": "untrusted",
                }
            )
    cases = list(fixture["cases"]) + EXTRA_CASES
    return records, cases


def effective_expected_ids(raw_case: dict[str, Any]) -> set[str]:
    """Evaluate only memories allowed by the active-memory retrieval policy."""

    return {
        ACTIVE_REPLACEMENTS.get(memory_id, memory_id)
        for memory_id in raw_case.get("expected_ids", [])
    }


def percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return float(quantiles(values, n=100, method="inclusive")[value - 1])


def binary_ndcg(ids: list[str], expected: set[str], cutoff: int = 5) -> float:
    if not expected:
        return 1.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, memory_id in enumerate(ids[:cutoff], start=1)
        if memory_id in expected
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(cutoff, len(expected)) + 1))
    return dcg / ideal if ideal else 0.0


def stage_scores(ids: list[str], expected: set[str]) -> dict[str, float | int | None]:
    first = next(
        (rank for rank, memory_id in enumerate(ids, start=1) if memory_id in expected),
        None,
    )
    return {
        "first_relevant_rank": first,
        "recall_at_1": (len(set(ids[:1]) & expected) / len(expected)) if expected else None,
        "recall_at_3": (len(set(ids[:3]) & expected) / len(expected)) if expected else None,
        "recall_at_5": (len(set(ids[:5]) & expected) / len(expected)) if expected else None,
        "mrr": (1.0 / first) if first else (0.0 if expected else None),
        "ndcg_at_5": binary_ndcg(ids, expected),
        "top1_relevant": bool(ids and ids[0] in expected) if expected else None,
    }


def relevance_labels(ids: list[str], expected: set[str]) -> list[dict[str, str | bool]]:
    return [{"memory_id": memory_id, "relevant": memory_id in expected} for memory_id in ids]


def candidate_details(
    candidates: Sequence[Any], label_by_uuid: dict[uuid.UUID, str]
) -> list[dict[str, Any]]:
    return [
        {
            "memory_id": label_by_uuid.get(candidate.memory_id, str(candidate.memory_id)),
            "rank": candidate.source_rank,
            "score": round(float(candidate.score), 8),
            "source": candidate.source,
        }
        for candidate in candidates
    ]


def fused_details(
    fused: Sequence[Any], label_by_uuid: dict[uuid.UUID, str]
) -> list[dict[str, Any]]:
    return [
        {
            "memory_id": label_by_uuid.get(item.memory_id, str(item.memory_id)),
            "rank": item.rank,
            "score": round(float(item.score), 8),
            "sources": list(item.sources),
        }
        for item in fused
    ]


def fusion_contributions(
    sources: Sequence[Sequence[Any]],
    *,
    k: int,
    label_by_uuid: dict[uuid.UUID, str],
) -> list[dict[str, Any]]:
    contributions: dict[uuid.UUID, dict[str, Any]] = {}
    for source_candidates in sources:
        for candidate in source_candidates:
            item = contributions.setdefault(
                candidate.memory_id,
                {
                    "memory_id": label_by_uuid.get(candidate.memory_id, str(candidate.memory_id)),
                    "by_source": {},
                },
            )
            item["by_source"][candidate.source] = {
                "rank": candidate.source_rank,
                "score": round(float(candidate.score), 8),
                "rrf_contribution": round(1.0 / (k + candidate.source_rank), 8),
            }
    for item in contributions.values():
        item["rrf_total"] = round(
            sum(value["rrf_contribution"] for value in item["by_source"].values()), 8
        )
    return sorted(contributions.values(), key=lambda item: str(item["memory_id"]))


async def legacy_fts_retrieve(
    session,
    *,
    user_id: uuid.UUID,
    plan,
    limit: int,
) -> list[Any]:
    """Reproduce the pre-remediation AND lexical source for the baseline only."""

    from sqlalchemy import func

    tsquery = func.websearch_to_tsquery("simple", plan.normalized_query)
    query = _base_memory_query(user_id, plan).where(MemoryItem.search_tsv.op("@@")(tsquery))
    rank = func.ts_rank_cd(MemoryItem.search_tsv, tsquery)
    rows = list(
        (
            await session.execute(
                query.with_only_columns(MemoryItem, rank.label("rank_score"))
                .order_by(rank.desc(), MemoryItem.id.asc())
                .limit(limit)
            )
        ).all()
    )
    from app.memory.types import MemoryCandidate

    return [
        MemoryCandidate(
            memory_id=row[0].id,
            user_id=row[0].user_id,
            content=row[0].content,
            source="fts",
            source_rank=index,
            score=float(row[1] or 0.0),
            created_at=row[0].created_at,
            memory_type=MemoryType(row[0].memory_type),
            subject=row[0].subject,
        )
        for index, row in enumerate(rows, start=1)
    ]


async def seed_records(
    session,
    settings: Settings,
    embedding: RemoteEmbeddingProvider,
    owner_id: uuid.UUID,
    other_id: uuid.UUID,
) -> dict[str, uuid.UUID]:
    records, _ = load_fixture()
    writer = MemoryWriter(settings)
    memory_ids: dict[str, uuid.UUID] = {}
    created: dict[str, MemoryItem] = {}
    for record in records:
        override = RECORD_OVERRIDES.get(record["id"], {})
        candidate = ExtractionCandidate(
            content=override.get("content", record["content"]),
            memory_type=MemoryType(override.get("memory_type", record["memory_type"])),
            subject=override.get("subject"),
            predicate=override.get("predicate"),
            confidence=1.0,
            salience=0.8,
        )
        user_id = other_id if record["id"] == "cross-user-secret" else owner_id
        item, _ = await writer.write_candidate(
            session,
            user_id=user_id,
            candidate=candidate,
            source_kind=MemorySourceKind.MANUAL_API,
            metadata_json={"phase6_retrieval_evaluation": record["id"]},
        )
        memory_ids[record["id"]] = item.id
        created[record["id"]] = item
        if "occurred_start_at" in override:
            item.occurred_start_at = override["occurred_start_at"]

    # The writer's preference conflict path creates the historical tea row.
    # Establish the same superseded state for the work-location fixture so the
    # evaluation covers the shared retrieval status filter for both examples.
    old_work = created["work-remote-old"]
    current_work = created["work-mumbai"]
    old_work.status = "superseded"
    current_work.supersedes_id = old_work.id
    await session.commit()

    active_ids = [
        item.id
        for key, item in created.items()
        if item.status == "active" and key != "cross-user-secret"
    ]
    jobs = list(
        (
            await session.scalars(
                select(MemoryJob).where(
                    MemoryJob.user_id == owner_id,
                    MemoryJob.memory_id.in_(active_ids),
                    MemoryJob.status == "pending",
                )
            )
        ).all()
    )
    worker = MemoryJobWorker(settings, embedding_provider=embedding)
    for job in jobs:
        await worker._embed_memory(session, job)
    other_job = await session.scalar(
        select(MemoryJob).where(
            MemoryJob.user_id == other_id,
            MemoryJob.memory_id == memory_ids["cross-user-secret"],
            MemoryJob.status == "pending",
        )
    )
    if other_job is not None:
        await worker._embed_memory(session, other_job)
    await session.commit()

    # Deletion follows the production hard-delete semantics and occurs only
    # after the record has passed through the normal writer/indexing path.
    deleted_item = created["deleted-memory"]
    await session.delete(deleted_item)
    await session.commit()
    return memory_ids


async def explain_vector_index(
    session, vector: tuple[float, ...], owner_id: uuid.UUID
) -> dict[str, Any]:
    indexes = list(
        (
            await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE tablename = 'memory_chunks' "
                    "AND indexname = 'ix_memory_chunks_embedding_hnsw'"
                )
            )
        ).all()
    )
    vector_text = "[" + ",".join(str(value) for value in vector) + "]"
    plan_rows = list(
        (
            await session.execute(
                text(
                    "EXPLAIN (ANALYZE, BUFFERS) "
                    "SELECT mc.memory_id FROM memory_chunks mc "
                    "JOIN memory_items mi ON mi.id = mc.memory_id "
                    "AND mi.user_id = mc.user_id "
                    "WHERE mc.user_id = :user_id AND mi.status = 'active' "
                    "AND mc.embedding IS NOT NULL "
                    "ORDER BY mc.embedding <=> CAST(:embedding AS vector) LIMIT 30"
                ),
                {"user_id": owner_id, "embedding": vector_text},
            )
        ).all()
    )
    plan = "\n".join(str(row[0]) for row in plan_rows)
    return {
        "index_present": bool(indexes),
        "index_definition": indexes[0][1] if indexes else None,
        "explain": plan,
        "explain_uses_hnsw": "ix_memory_chunks_embedding_hnsw" in plan,
    }


async def evaluate() -> dict[str, Any]:
    settings = Settings()
    baseline_mode = os.getenv("PHASE6_RETRIEVAL_BASELINE") == "1"
    if (
        settings.memory_retrieval_mode == "off"
        or not settings.embedding_api_url
        or not settings.rerank_api_url
    ):
        raise RuntimeError("Phase 6 retrieval must be enabled with embedding and rerank endpoints")
    engine = create_async_engine(settings.database_dsn, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    embedding = RemoteEmbeddingProvider(settings)
    reranker = RemoteReranker(settings)
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    record_ids: dict[str, uuid.UUID] = {}
    try:
        await embedding.initialize()
        await reranker.initialize()
        async with factory() as session:
            session.add_all(
                [
                    User(id=owner_id, email=OWNER_EMAIL, password_hash="evaluation-only"),
                    User(id=other_id, email=OTHER_EMAIL, password_hash="evaluation-only"),
                ]
            )
            await session.commit()
            record_ids = await seed_records(session, settings, embedding, owner_id, other_id)
            records, raw_cases = load_fixture()
            label_by_uuid = {value: key for key, value in record_ids.items()}
            owner_record_ids = {key for key in record_ids if key != "cross-user-secret"}
            status_by_record_id = {
                key: (
                    "deleted"
                    if key == "deleted-memory"
                    else "superseded"
                    if key in ACTIVE_REPLACEMENTS
                    else "cross_user"
                    if key == "cross-user-secret"
                    else "active"
                )
                for key in record_ids
            }
            stored_by_record_id = {
                record["id"]: {
                    "id": record["id"],
                    "content": RECORD_OVERRIDES.get(record["id"], {}).get(
                        "content", record["content"]
                    ),
                    "memory_type": RECORD_OVERRIDES.get(record["id"], {}).get(
                        "memory_type", record["memory_type"]
                    ),
                    "user_id": (
                        "test-user-other"
                        if record["id"] == "cross-user-secret"
                        else "test-user-owner"
                    ),
                    "status": status_by_record_id[record["id"]],
                }
                for record in records
            }
            first_vector = (await embedding.embed(("index verification",))).vectors[0]
            index_evidence = await explain_vector_index(session, first_vector, owner_id)

            case_outputs: list[dict[str, Any]] = []
            for raw_case in raw_cases:
                query = raw_case["query"]
                expected = effective_expected_ids(raw_case)
                plan = build_memory_query_plan(query, now=FIXED_NOW)
                started = time.perf_counter()
                vector_started = time.perf_counter()
                dense = await dense_retrieve(
                    session,
                    user_id=owner_id,
                    plan=plan,
                    provider=embedding,
                    limit=settings.memory_candidate_count,
                )
                vector_latency = (time.perf_counter() - vector_started) * 1000
                structured = (
                    await structured_retrieve(
                        session,
                        user_id=owner_id,
                        plan=plan,
                        limit=settings.memory_candidate_count,
                    )
                    if baseline_mode or should_run_structured_retrieval(plan)
                    else []
                )
                fts = await (
                    legacy_fts_retrieve(
                        session,
                        user_id=owner_id,
                        plan=plan,
                        limit=settings.memory_candidate_count,
                    )
                    if baseline_mode
                    else fts_retrieve(
                        session,
                        user_id=owner_id,
                        plan=plan,
                        limit=settings.memory_candidate_count,
                    )
                )
                sources = tuple(source for source in (structured, fts, dense) if source)
                fused = fuse_candidates(
                    sources,
                    k=settings.memory_rrf_k,
                    limit=settings.memory_candidate_count,
                )
                hybrid_latency = (time.perf_counter() - started) * 1000
                rerank_started = time.perf_counter()
                reranked = await rerank_fused(
                    fused,
                    query=plan.normalized_query,
                    provider=reranker,
                    limit=settings.memory_final_context_count,
                )
                final = (
                    reranked
                    if baseline_mode
                    else apply_relevance_boundary(
                        reranked,
                        minimum_score=settings.memory_min_rerank_score,
                        trusted_memory_ids={candidate.memory_id for candidate in fts}
                        | (
                            {candidate.memory_id for candidate in structured}
                            if plan.intent == MemoryIntent.TIME_RANGE
                            else set()
                        ),
                    )
                )
                final_latency = (time.perf_counter() - rerank_started) * 1000
                total_latency = (time.perf_counter() - started) * 1000

                dense_ids = [
                    label_by_uuid[item.memory_id]
                    for item in dense
                    if item.memory_id in label_by_uuid
                ]
                hybrid_ids = [
                    item_id
                    for item_id in (label_by_uuid.get(item.memory_id) for item in fused)
                    if item_id
                ]
                final_ids = [
                    item_id
                    for item_id in (label_by_uuid.get(item.memory_id) for item in final)
                    if item_id
                ]
                returned_owner_ok = all(item.user_id == owner_id for item in final)
                case_outputs.append(
                    {
                        "case_id": raw_case["id"],
                        "user_id": "test-user-owner",
                        "database_user_id": str(owner_id),
                        "query": query,
                        "category": raw_case["category"],
                        "manifest_expected_relevant_ids": sorted(raw_case.get("expected_ids", [])),
                        "expected_relevant_ids": sorted(expected),
                        "expected_irrelevant_ids": sorted(
                            set(raw_case.get("excluded_ids", []))
                            | set(raw_case.get("expected_ids", [])) & set(ACTIVE_REPLACEMENTS)
                        ),
                        "no_result_expected": not expected,
                        "stored_memory_ids": sorted(
                            owner_record_ids | {"cross-user-secret", "deleted-memory"}
                        ),
                        "stored_memories": list(stored_by_record_id.values()),
                        "hnsw_retrieved_ids": dense_ids,
                        "hybrid_retrieved_ids": hybrid_ids,
                        "final_retrieved_ids": final_ids,
                        "candidate_pools": {
                            "structured": len(structured),
                            "lexical": len(fts),
                            "vector": len(dense),
                            "hybrid": len(fused),
                            "reranked": len(reranked),
                            "accepted": len(final),
                        },
                        "structured_candidates": candidate_details(structured, label_by_uuid),
                        "lexical_candidates": candidate_details(fts, label_by_uuid),
                        "vector_candidates": candidate_details(dense, label_by_uuid),
                        "hybrid_candidates": fused_details(fused, label_by_uuid),
                        "reranked_candidates": fused_details(reranked, label_by_uuid),
                        "accepted_candidates": fused_details(final, label_by_uuid),
                        "fusion_contributions": fusion_contributions(
                            sources,
                            k=settings.memory_rrf_k,
                            label_by_uuid=label_by_uuid,
                        ),
                        "hnsw_relevance_labels": relevance_labels(dense_ids, expected),
                        "hybrid_relevance_labels": relevance_labels(hybrid_ids, expected),
                        "final_relevance_labels": relevance_labels(final_ids, expected),
                        "hnsw_scores": stage_scores(dense_ids, expected),
                        "hybrid_scores": stage_scores(hybrid_ids, expected),
                        "final_scores": stage_scores(final_ids, expected),
                        "no_result_validation": {
                            "top_candidate": (
                                label_by_uuid.get(reranked[0].memory_id) if reranked else None
                            ),
                            "top_reranker_score": float(reranked[0].score) if reranked else None,
                            "final_decision": bool(final),
                            "memory_injected": bool(final),
                            "relevance_boundary": settings.memory_min_rerank_score,
                        },
                        "latency_ms": {
                            "hnsw_vector_stage": round(vector_latency, 3),
                            "hybrid_before_rerank": round(hybrid_latency, 3),
                            "rerank_stage": round(final_latency, 3),
                            "total": round(total_latency, 3),
                        },
                        "ownership_ok": returned_owner_ok,
                        "returned_deleted_ids": [
                            item_id for item_id in final_ids if item_id == "deleted-memory"
                        ],
                        "returned_superseded_ids": [
                            item_id
                            for item_id in final_ids
                            if item_id in {"work-remote-old", "tea-old"}
                        ],
                        "returned_cross_user_ids": [
                            item_id for item_id in final_ids if item_id == "cross-user-secret"
                        ],
                        "provider_status": "live",
                    }
                )

            positive = [item for item in case_outputs if item["expected_relevant_ids"]]

            def avg(stage: str, key: str) -> float:
                values = [item[stage][key] for item in positive if item[stage][key] is not None]
                return sum(values) / len(values) if values else 0.0

            final_top1 = sum(
                bool(item["final_scores"]["top1_relevant"]) for item in positive
            ) / len(positive)
            latencies = [item["latency_ms"]["total"] for item in case_outputs]
            safety = {
                "cross_user_leakage": sum(
                    len(item["returned_cross_user_ids"]) for item in case_outputs
                ),
                "deleted_memory_retrieval": sum(
                    len(item["returned_deleted_ids"]) for item in case_outputs
                ),
                "superseded_obsolete_out_ranked_active_replacement": sum(
                    len(item["returned_superseded_ids"]) for item in case_outputs
                ),
                "no_result_relevant_returned": sum(
                    1
                    for item in case_outputs
                    if item["no_result_expected"]
                    and set(item["final_retrieved_ids"]) & set(item["expected_relevant_ids"])
                ),
                "no_result_nonempty": sum(
                    1
                    for item in case_outputs
                    if item["no_result_expected"] and item["final_retrieved_ids"]
                ),
                "user_scope_violations": sum(not item["ownership_ok"] for item in case_outputs),
            }
            metrics = {
                "cases": len(case_outputs),
                "positive_quality_cases": len(positive),
                "hnsw_recall_at_5": avg("hnsw_scores", "recall_at_5"),
                "hybrid_recall_at_5": avg("hybrid_scores", "recall_at_5"),
                "reranked_recall_at_5": avg("final_scores", "recall_at_5"),
                "final_mrr": avg("final_scores", "mrr"),
                "final_ndcg_at_5": avg("final_scores", "ndcg_at_5"),
                "final_top1_accuracy": final_top1,
                "latency_ms": {
                    "p50": percentile(latencies, 50),
                    "p95": percentile(latencies, 95),
                    "p99": percentile(latencies, 99),
                    "min": min(latencies),
                    "max": max(latencies),
                },
            }
            thresholds = {
                "cases": 20,
                "hybrid_recall_at_5": 0.90,
                "final_mrr": 0.85,
                "final_ndcg_at_5": 0.85,
                "final_top1_accuracy": 0.80,
                "cross_user_leakage": 0,
                "deleted_memory_retrieval": 0,
            }
            threshold_status = {
                "cases": metrics["cases"] >= thresholds["cases"],
                "hybrid_recall_at_5": metrics["hybrid_recall_at_5"]
                >= thresholds["hybrid_recall_at_5"],
                "final_mrr": metrics["final_mrr"] >= thresholds["final_mrr"],
                "final_ndcg_at_5": metrics["final_ndcg_at_5"] >= thresholds["final_ndcg_at_5"],
                "final_top1_accuracy": metrics["final_top1_accuracy"]
                >= thresholds["final_top1_accuracy"],
                "cross_user_leakage": safety["cross_user_leakage"] == 0,
                "deleted_memory_retrieval": safety["deleted_memory_retrieval"] == 0,
                "superseded_filter": safety["superseded_obsolete_out_ranked_active_replacement"]
                == 0,
                "no_result_safety": safety["no_result_relevant_returned"] == 0,
                "no_result_irrelevant_injection": safety["no_result_nonempty"] == 0,
                "user_scope": safety["user_scope_violations"] == 0,
            }
            failures = [
                {
                    "case_id": item["case_id"],
                    "query": item["query"],
                    "category": item["category"],
                    "expected_relevant_ids": item["expected_relevant_ids"],
                    "vector_candidates": item["vector_candidates"],
                    "lexical_candidates": item["lexical_candidates"],
                    "hybrid_candidates": item["hybrid_candidates"],
                    "reranked_candidates": item["reranked_candidates"],
                    "candidate_pools": item["candidate_pools"],
                    "fusion_contributions": item["fusion_contributions"],
                    "hybrid_retrieved_ids": item["hybrid_retrieved_ids"],
                    "final_retrieved_ids": item["final_retrieved_ids"],
                    "reason": "expected relevant memory was not present in hybrid top five",
                }
                for item in case_outputs
                if item["expected_relevant_ids"]
                and item["hybrid_scores"]["recall_at_5"] is not None
                and item["hybrid_scores"]["recall_at_5"] < 1
            ]
            return {
                "evaluation_id": f"phase6-retrieval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                "corpus_id": "phase6-retrieval-corpus-v1",
                "corpus_version": "1.0.0",
                "run_at": datetime.now(UTC).isoformat(),
                "configuration": {
                    "embedding_model": settings.memory_expected_embedding_model,
                    "reranker_model": settings.memory_expected_rerank_model,
                    "embedding_dimension": settings.memory_embedding_dimension,
                    "retrieval_mode": settings.memory_retrieval_mode,
                    "candidate_count": settings.memory_candidate_count,
                    "final_count": settings.memory_final_context_count,
                    "rrf_k": settings.memory_rrf_k,
                    "fixed_now": FIXED_NOW.isoformat(),
                    "evaluation_mode": "baseline" if baseline_mode else "remediated",
                },
                "index_evidence": index_evidence,
                "metrics": metrics,
                "thresholds": thresholds,
                "safety": safety,
                "threshold_status": threshold_status,
                "failures": failures,
                "status": "PASS" if all(threshold_status.values()) else "FAIL",
                "cases": case_outputs,
            }
    finally:
        async with factory() as cleanup_session:
            await cleanup_session.execute(delete(User).where(User.id.in_((owner_id, other_id))))
            await cleanup_session.commit()
        await embedding.close()
        await reranker.close()
        await engine.dispose()


def write_evidence(result: dict[str, Any]) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = (
        "phase6_retrieval_baseline_"
        if result["configuration"].get("evaluation_mode") == "baseline"
        else "phase6_"
    )
    cases = {
        "corpus_id": result["corpus_id"],
        "corpus_version": result["corpus_version"],
        "case_count": len(result["cases"]),
        "cases": result["cases"],
    }
    results = {
        "evaluation_id": result["evaluation_id"],
        "run_at": result["run_at"],
        "configuration": result["configuration"],
        "index_evidence": result["index_evidence"],
        "cases": result["cases"],
    }
    latency = {
        "evaluation_id": result["evaluation_id"],
        "case_latency_ms": [
            {"case_id": item["case_id"], **item["latency_ms"]} for item in result["cases"]
        ],
        "aggregate": result["metrics"]["latency_ms"],
    }
    for name, payload in (
        (f"{prefix}retrieval_cases.json", cases),
        (f"{prefix}retrieval_results.json", results),
        (f"{prefix}retrieval_latency.json", latency),
        (f"{prefix}retrieval_metrics.json", result),
        (
            f"{prefix}retrieval_failure_analysis.json",
            {
                "evaluation_id": result["evaluation_id"],
                "failures": result["failures"],
            },
        ),
        (
            f"{prefix}no_result_validation.json",
            {
                "evaluation_id": result["evaluation_id"],
                "cases": [
                    {
                        "case_id": item["case_id"],
                        "query": item["query"],
                        **item["no_result_validation"],
                    }
                    for item in result["cases"]
                    if item["no_result_expected"]
                ],
            },
        ),
    ):
        (EVIDENCE_DIR / name).write_text(
            json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
        )
    if prefix == "phase6_":
        baseline_path = EVIDENCE_DIR / "phase6_retrieval_baseline_retrieval_metrics.json"
        if baseline_path.exists():
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            (EVIDENCE_DIR / "phase6_hybrid_fusion_before_after.json").write_text(
                json.dumps(
                    {
                        "before": {
                            "evaluation_id": baseline["evaluation_id"],
                            "metrics": baseline["metrics"],
                            "threshold_status": baseline["threshold_status"],
                            "failures": baseline["failures"],
                        },
                        "after": {
                            "evaluation_id": result["evaluation_id"],
                            "metrics": result["metrics"],
                            "threshold_status": result["threshold_status"],
                            "failures": result["failures"],
                        },
                    },
                    indent=2,
                    default=str,
                )
                + "\n",
                encoding="utf-8",
            )
            (EVIDENCE_DIR / "phase6_retrieval_failure_analysis.json").write_text(
                json.dumps(
                    {
                        "before_evaluation_id": baseline["evaluation_id"],
                        "after_evaluation_id": result["evaluation_id"],
                        "before_failures": baseline["failures"],
                        "after_failures": result["failures"],
                    },
                    indent=2,
                    default=str,
                )
                + "\n",
                encoding="utf-8",
            )
    report_path = ROOT / "docs" / f"{timestamp}_phase6_retrieval_evaluation.md"
    metrics = result["metrics"]
    safety = result["safety"]
    configuration = result["configuration"]
    provider_path = (
        f"live configured `{configuration['embedding_model']}` embeddings, "
        f"pgvector/HNSW + PostgreSQL FTS + RRF, and live "
        f"`{configuration['reranker_model']}` reranking."
    )
    metric_table = "\n".join(
        [
            "| Metric | Result | Threshold | Status |",
            "|---|---:|---:|---|",
            (
                f"| Cases | {metrics['cases']} | >= 20 | "
                f"{'PASS' if metrics['cases'] >= 20 else 'FAIL'} |"
            ),
            f"| HNSW Recall@5 | {metrics['hnsw_recall_at_5']:.3f} | informational | recorded |",
            (
                f"| Hybrid Recall@5 | {metrics['hybrid_recall_at_5']:.3f} | "
                f">= 0.900 | {'PASS' if metrics['hybrid_recall_at_5'] >= 0.90 else 'FAIL'} |"
            ),
            (
                f"| Reranked Recall@5 | {metrics['reranked_recall_at_5']:.3f} | "
                "informational | recorded |"
            ),
            (
                f"| MRR | {metrics['final_mrr']:.3f} | >= 0.850 | "
                f"{'PASS' if metrics['final_mrr'] >= 0.85 else 'FAIL'} |"
            ),
            (
                f"| nDCG@5 | {metrics['final_ndcg_at_5']:.3f} | >= 0.850 | "
                f"{'PASS' if metrics['final_ndcg_at_5'] >= 0.85 else 'FAIL'} |"
            ),
            (
                f"| Top-1 accuracy | {metrics['final_top1_accuracy']:.3f} | >= 0.800 | "
                f"{'PASS' if metrics['final_top1_accuracy'] >= 0.80 else 'FAIL'} |"
            ),
            f"| P50 latency | {metrics['latency_ms']['p50']:.3f} ms | record | recorded |",
            f"| P95 latency | {metrics['latency_ms']['p95']:.3f} ms | record | recorded |",
            f"| P99 latency | {metrics['latency_ms']['p99']:.3f} ms | record | recorded |",
            (
                f"| Cross-user leakage | {safety['cross_user_leakage']} | 0 | "
                f"{'PASS' if safety['cross_user_leakage'] == 0 else 'FAIL'} |"
            ),
            (
                f"| Deleted-memory retrieval | {safety['deleted_memory_retrieval']} | 0 | "
                f"{'PASS' if safety['deleted_memory_retrieval'] == 0 else 'FAIL'} |"
            ),
            (
                f"| No-result irrelevant injection | {safety['no_result_nonempty']} | 0 | "
                f"{'PASS' if safety['no_result_nonempty'] == 0 else 'FAIL'} |"
            ),
        ]
    )
    superseded_result = safety["superseded_obsolete_out_ranked_active_replacement"]
    no_result_relevant = safety["no_result_relevant_returned"]
    no_result_nonempty = safety["no_result_nonempty"]
    no_result_note = "(informational; hard safety checks relevant leakage)"
    hnsw_validation_path = EVIDENCE_DIR / "phase6_hnsw_plan_validation.json"
    hnsw_note = "not run"
    if hnsw_validation_path.exists():
        hnsw_validation = json.loads(hnsw_validation_path.read_text(encoding="utf-8"))
        hnsw_note = (
            "PASS"
            if hnsw_validation.get("planner_selected_hnsw")
            and hnsw_validation.get("relevant_returned")
            else "FAIL"
        )
    report = f"""# Phase 6 retrieval evaluation

Run: `{result["evaluation_id"]}`  
Corpus: `{result["corpus_id"]}` / `{result["corpus_version"]}`  
Provider path: {provider_path}

{metric_table}

Additional integrity checks:

- Superseded obsolete memory outranking active replacement: `{superseded_result}`
- No-result cases returning a relevant memory: `{no_result_relevant}`
- No-result cases returning any memory: `{no_result_nonempty}` {no_result_note}
- User-scope violations: `{safety["user_scope_violations"]}`
- HNSW index present: `{result["index_evidence"]["index_present"]}`
- EXPLAIN plan uses HNSW index: `{result["index_evidence"]["explain_uses_hnsw"]}`
- Isolated large-corpus HNSW planner validation: `{hnsw_note}`

Evidence files are under `docs/evidence/phase6/`.

## Hybrid top-five failures

"""
    report += (
        "\n".join(
            f"- `{failure['case_id']}` ({failure['category']}): expected "
            f"{', '.join(failure['expected_relevant_ids'])}; hybrid returned "
            f"{', '.join(failure['hybrid_retrieved_ids'][:5]) or '(none)'}."
            for failure in result["failures"]
        )
        or "- None."
    )
    report += f"""

## Verdict

`PHASE 6 RETRIEVAL EVALUATION: {result["status"]}`
"""
    report_path.write_text(report, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "report": str(report_path),
                "evidence": str(EVIDENCE_DIR),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    outcome = asyncio.run(evaluate())
    write_evidence(outcome)
    if outcome["status"] != "PASS":
        raise SystemExit(1)
