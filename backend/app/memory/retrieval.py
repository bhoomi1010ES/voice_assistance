from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import MemoryChunk, MemoryItem

from .providers import MemoryProviderError, RemoteEmbeddingProvider, RemoteReranker
from .types import (
    FusedMemory,
    MemoryCandidate,
    MemoryIntent,
    MemoryQueryPlan,
    MemoryRetrievalResult,
    MemoryStatus,
    MemoryType,
    build_memory_query_plan,
)

_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "and",
        "are",
        "did",
        "do",
        "for",
        "how",
        "i",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "the",
        "to",
        "use",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
    }
)


def _base_memory_query(user_id: uuid.UUID, plan: MemoryQueryPlan) -> Select:
    query = select(MemoryItem).where(
        MemoryItem.user_id == user_id,
        MemoryItem.status == MemoryStatus.ACTIVE,
    )
    if plan.memory_types:
        query = query.where(MemoryItem.memory_type.in_(plan.memory_types))
    if plan.subject:
        query = query.where(MemoryItem.subject.ilike(plan.subject))
    if plan.start_at is not None:
        query = query.where(
            func.coalesce(
                MemoryItem.occurred_end_at, MemoryItem.occurred_start_at, MemoryItem.created_at
            )
            >= plan.start_at
        )
    if plan.end_at is not None:
        query = query.where(
            func.coalesce(MemoryItem.occurred_start_at, MemoryItem.created_at) < plan.end_at
        )
    return query


async def structured_retrieve(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    plan: MemoryQueryPlan,
    limit: int,
    trace: Callable[[str, float, dict[str, Any]], None] | None = None,
) -> list[MemoryCandidate]:
    query = _base_memory_query(user_id, plan)
    if plan.intent == MemoryIntent.LATEST:
        query = query.order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
    elif plan.intent == MemoryIntent.OLDEST:
        query = query.order_by(MemoryItem.created_at.asc(), MemoryItem.id.asc())
    elif plan.predicate:
        query = query.where(MemoryItem.predicate == plan.predicate).order_by(
            MemoryItem.created_at.desc(), MemoryItem.id.desc()
        )
    else:
        query = query.order_by(
            MemoryItem.salience.desc(), MemoryItem.created_at.desc(), MemoryItem.id.desc()
        )
    started_ns = time.perf_counter_ns()
    rows = list((await session.scalars(query.limit(limit))).all())
    if trace is not None:
        trace(
            "structured_db_query",
            (time.perf_counter_ns() - started_ns) / 1_000_000,
            {"started_monotonic_ns": started_ns},
        )
    return [
        MemoryCandidate(
            memory_id=row.id,
            user_id=row.user_id,
            content=row.content,
            source="structured",
            source_rank=index,
            score=float(row.salience),
            created_at=row.created_at,
            memory_type=MemoryType(row.memory_type),
            subject=row.subject,
        )
        for index, row in enumerate(rows, start=1)
    ]


async def fts_retrieve(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    plan: MemoryQueryPlan,
    limit: int,
    trace: Callable[[str, float, dict[str, Any]], None] | None = None,
) -> list[MemoryCandidate]:
    # Use a phrase over meaningful terms. This avoids treating generic words
    # such as "favorite" as sufficient evidence while retaining exact lexical
    # matches such as "saved instructions" and "work remotely".
    terms = tuple(
        term
        for term in plan.search_terms
        if len(term) >= 3 and term.casefold() not in _QUERY_STOPWORDS
    )
    if not terms:
        return []
    tsquery = func.websearch_to_tsquery("simple", f'"{" ".join(terms)}"')
    query = _base_memory_query(user_id, plan).where(MemoryItem.search_tsv.op("@@")(tsquery))
    rank = func.ts_rank_cd(MemoryItem.search_tsv, tsquery)
    started_ns = time.perf_counter_ns()
    rows = list(
        (
            await session.execute(
                query.with_only_columns(MemoryItem, rank.label("rank_score"))
                .order_by(rank.desc(), MemoryItem.id.asc())
                .limit(limit)
            )
        ).all()
    )
    if trace is not None:
        trace(
            "fts_db_query",
            (time.perf_counter_ns() - started_ns) / 1_000_000,
            {"started_monotonic_ns": started_ns},
        )
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


def should_run_structured_retrieval(plan: MemoryQueryPlan) -> bool:
    """Run structured ordering only when the query expresses a structured need.

    A broad structured query is an ordered list of all active memories, not a
    relevance-ranked candidate source. Feeding that list into RRF makes recent
    or salient distractors compete with genuine semantic/lexical matches.
    """

    return bool(
        plan.intent not in {MemoryIntent.GENERAL, MemoryIntent.FACT}
        or plan.memory_types
        or plan.subject
        or plan.predicate
        or plan.start_at is not None
        or plan.end_at is not None
    )


async def dense_retrieve(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    plan: MemoryQueryPlan,
    provider: RemoteEmbeddingProvider,
    limit: int,
    trace: Callable[[str, float, dict[str, Any]], None] | None = None,
) -> list[MemoryCandidate]:
    embedding_started_ns = time.perf_counter_ns()
    embedding = (await provider.embed((plan.normalized_query,))).vectors[0]
    if trace is not None:
        trace(
            "embedding",
            (time.perf_counter_ns() - embedding_started_ns) / 1_000_000,
            {"started_monotonic_ns": embedding_started_ns},
        )
    distance = MemoryChunk.embedding.cosine_distance(list(embedding))
    minimum_distance = func.min(distance).label("distance")
    query = (
        select(MemoryItem, minimum_distance)
        .join(
            MemoryChunk,
            (MemoryChunk.memory_id == MemoryItem.id) & (MemoryChunk.user_id == MemoryItem.user_id),
        )
        .where(
            MemoryItem.user_id == user_id,
            MemoryItem.status == MemoryStatus.ACTIVE,
            MemoryChunk.embedding.is_not(None),
        )
        .group_by(MemoryItem.id)
        .order_by(minimum_distance.asc(), MemoryItem.id.asc())
        .limit(limit)
    )
    query_started_ns = time.perf_counter_ns()
    rows = list((await session.execute(query)).all())
    if trace is not None:
        trace(
            "vector_db_query",
            (time.perf_counter_ns() - query_started_ns) / 1_000_000,
            {"started_monotonic_ns": query_started_ns},
        )
    return [
        MemoryCandidate(
            memory_id=row[0].id,
            user_id=row[0].user_id,
            content=row[0].content,
            source="dense",
            source_rank=index,
            score=max(0.0, 1.0 - float(row[1] or 1.0)),
            created_at=row[0].created_at,
            memory_type=MemoryType(row[0].memory_type),
            subject=row[0].subject,
        )
        for index, row in enumerate(rows, start=1)
    ]


def fuse_candidates(
    sources: Sequence[Sequence[MemoryCandidate]],
    *,
    k: int = 60,
    limit: int = 8,
) -> tuple[FusedMemory, ...]:
    """Collapse chunks/duplicate source hits and fuse ranks deterministically."""

    by_id: dict[uuid.UUID, dict[str, object]] = {}
    for source_candidates in sources:
        for candidate in source_candidates:
            current = by_id.setdefault(
                candidate.memory_id,
                {
                    "candidate": candidate,
                    "score": 0.0,
                    "sources": set(),
                },
            )
            current["score"] = float(current["score"]) + 1.0 / (k + candidate.source_rank)
            current["sources"].add(candidate.source)  # type: ignore[union-attr]
            if candidate.score > current["candidate"].score:  # type: ignore[union-attr]
                current["candidate"] = candidate
    ordered = sorted(
        by_id.values(),
        key=lambda item: (-float(item["score"]), str(item["candidate"].memory_id)),  # type: ignore[union-attr]
    )
    result: list[FusedMemory] = []
    for rank, item in enumerate(ordered[:limit], start=1):
        candidate = item["candidate"]
        result.append(
            FusedMemory(
                memory_id=candidate.memory_id,
                user_id=candidate.user_id,
                content=candidate.content,
                score=float(item["score"]),
                rank=rank,
                sources=tuple(sorted(item["sources"])),  # type: ignore[arg-type]
                created_at=candidate.created_at,
                memory_type=candidate.memory_type,
            )
        )
    return tuple(result)


async def rerank_fused(
    fused: Sequence[FusedMemory],
    *,
    query: str,
    provider: RemoteReranker,
    limit: int,
) -> tuple[FusedMemory, ...]:
    if not fused:
        return ()
    results = await provider.rerank(query, [item.content for item in fused])
    by_index = {result.index: result.score for result in results}
    ordered = sorted(
        ((item, by_index[index]) for index, item in enumerate(fused) if index in by_index),
        key=lambda pair: (-pair[1], str(pair[0].memory_id)),
    )
    return tuple(
        item.model_copy(update={"rank": rank, "score": score})
        for rank, (item, score) in enumerate(ordered[:limit], 1)
    )


def apply_relevance_boundary(
    memories: Sequence[FusedMemory],
    *,
    minimum_score: float,
    trusted_memory_ids: set[uuid.UUID] | frozenset[uuid.UUID] = frozenset(),
) -> tuple[FusedMemory, ...]:
    """Keep reranked evidence that clears the floor or has exact evidence.

    A reranker score is calibrated over the submitted candidate set. A direct
    lexical hit or a bounded temporal predicate is therefore allowed through
    even when the provider's normalized score is small; ungrounded nearest
    neighbors still have to clear the confidence floor.
    """

    return tuple(
        memory
        for memory in memories
        if memory.score >= minimum_score or memory.memory_id in trusted_memory_ids
    )


class MemoryRetrievalService:
    def __init__(
        self,
        settings: Settings,
        *,
        embedding_provider: RemoteEmbeddingProvider | None = None,
        reranker: RemoteReranker | None = None,
    ) -> None:
        self.settings = settings
        self.embedding_provider = embedding_provider
        self.reranker = reranker

    async def readiness(self) -> dict[str, object]:
        if self.settings.memory_retrieval_mode == "off" and not self.settings.memory_write_enabled:
            return {"enabled": False, "status": "disabled"}
        providers: dict[str, object] = {}
        if self.embedding_provider is not None:
            try:
                providers["embedding"] = {
                    "status": "ok" if await self.embedding_provider.health() else "error"
                }
            except MemoryProviderError as error:
                providers["embedding"] = {"status": "error", "error": error.code}
        if self.reranker is not None:
            try:
                providers["reranker"] = {
                    "status": "ok" if await self.reranker.health() else "error"
                }
            except MemoryProviderError as error:
                providers["reranker"] = {"status": "error", "error": error.code}
        status = (
            "ready"
            if all(item.get("status") == "ok" for item in providers.values())
            else "not_ready"
        )
        return {"enabled": True, "status": status, "providers": providers}

    async def retrieve(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        query: str,
        now: datetime | None = None,
        limit: int | None = None,
        trace: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> MemoryRetrievalResult:
        requested_limit = self.settings.memory_final_context_count
        if limit is not None:
            requested_limit = min(
                self.settings.memory_final_context_count,
                max(1, int(limit)),
            )
        plan = build_memory_query_plan(
            query,
            now=now,
            limit=requested_limit,
        )
        if self.settings.memory_retrieval_mode == "off":
            return MemoryRetrievalResult(status="disabled", plan=plan)

        # Retrieval is optional context. Keep all database reads inside a
        # savepoint so a provider/database failure cannot poison the gateway's
        # transaction that is persisting the voice turn.
        try:
            async with session.begin_nested():
                return await self._retrieve_in_savepoint(
                    session,
                    user_id=user_id,
                    plan=plan,
                    trace=trace,
                )
        except SQLAlchemyError:
            return MemoryRetrievalResult(
                status="degraded",
                plan=plan,
                provider_error="memory_database_error",
            )

    async def _retrieve_in_savepoint(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        plan: MemoryQueryPlan,
        trace: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> MemoryRetrievalResult:
        # An AsyncSession is intentionally not used concurrently. This keeps
        # the retrieval boundary safe for the gateway's request transaction;
        # providers can still run independently when they are enabled.
        structured: list[MemoryCandidate] = []
        if should_run_structured_retrieval(plan):
            structured_started_ns = time.perf_counter_ns()
            structured = await structured_retrieve(
                session,
                user_id=user_id,
                plan=plan,
                limit=self.settings.memory_candidate_count,
                trace=trace,
            )
            if trace is not None:
                trace(
                    "structured",
                    (time.perf_counter_ns() - structured_started_ns) / 1_000_000,
                    {"count": len(structured), "started_monotonic_ns": structured_started_ns},
                )
        fts_started_ns = time.perf_counter_ns()
        fts = await fts_retrieve(
            session,
            user_id=user_id,
            plan=plan,
            limit=self.settings.memory_candidate_count,
            trace=trace,
        )
        if trace is not None:
            trace(
                "fts",
                (time.perf_counter_ns() - fts_started_ns) / 1_000_000,
                {"count": len(fts), "started_monotonic_ns": fts_started_ns},
            )
        sources: list[Sequence[MemoryCandidate]] = [fts]
        if structured:
            sources.insert(0, structured)
        provider_error: str | None = None
        if self.embedding_provider is not None:
            try:
                vector_started_ns = time.perf_counter_ns()
                sources.append(
                    await dense_retrieve(
                        session,
                        user_id=user_id,
                        plan=plan,
                        provider=self.embedding_provider,
                        limit=self.settings.memory_candidate_count,
                        trace=trace,
                    )
                )
                if trace is not None:
                    trace(
                        "vector_search",
                        (time.perf_counter_ns() - vector_started_ns) / 1_000_000,
                        {
                            "count": len(sources[-1]),
                            "started_monotonic_ns": vector_started_ns,
                        },
                    )
            except MemoryProviderError as error:
                provider_error = error.code
        rrf_started_ns = time.perf_counter_ns()
        fused = fuse_candidates(
            sources,
            k=self.settings.memory_rrf_k,
            limit=self.settings.memory_candidate_count,
        )
        if trace is not None:
            trace(
                "rrf",
                (time.perf_counter_ns() - rrf_started_ns) / 1_000_000,
                {"count": len(fused), "started_monotonic_ns": rrf_started_ns},
            )
        if self.reranker is not None and fused:
            fallback_memory_ids = {candidate.memory_id for candidate in fts} | {
                candidate.memory_id for candidate in structured
            }
            boundary_trusted_memory_ids = {candidate.memory_id for candidate in fts} | (
                {candidate.memory_id for candidate in structured}
                if plan.intent == MemoryIntent.TIME_RANGE
                else set()
            )
            try:
                rerank_started_ns = time.perf_counter_ns()
                fused = await rerank_fused(
                    fused,
                    query=plan.normalized_query,
                    provider=self.reranker,
                    limit=self.settings.memory_final_context_count,
                )
                if trace is not None:
                    trace(
                        "rerank",
                        (time.perf_counter_ns() - rerank_started_ns) / 1_000_000,
                        {"count": len(fused), "started_monotonic_ns": rerank_started_ns},
                    )
                fused = apply_relevance_boundary(
                    fused,
                    minimum_score=self.settings.memory_min_rerank_score,
                    trusted_memory_ids=boundary_trusted_memory_ids,
                )
            except MemoryProviderError as error:
                provider_error = provider_error or error.code
                # RRF scores are not reranker scores, so applying the
                # relevance threshold here would be meaningless. Preserve
                # only lexical/structured evidence and never expose a
                # dense-only nearest neighbour when reranking is unavailable.
                fused = tuple(item for item in fused if item.memory_id in fallback_memory_ids)
        return MemoryRetrievalResult(
            status="degraded" if provider_error else "ready",
            plan=plan,
            memories=tuple(fused[: plan.limit]),
            provider_error=provider_error,
        )
