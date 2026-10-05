from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.memory.evaluation import (
    MemoryEvaluationRoute,
    evaluate_memory_result,
)
from app.memory.retrieval import MemoryRetrievalService
from app.okf.retrieval import OkfRetrievalService
from app.okf.types import KnowledgeDisposition
from app.okf.types import KnowledgeRequest as OkfKnowledgeRequest

from .types import KnowledgeEngineResult, KnowledgeFact


class KnowledgeRequest(Protocol):
    user_id: uuid.UUID
    query: str
    now: datetime
    session_id: uuid.UUID | None
    memory_enabled: bool
    memory_excluded: bool


class KnowledgeEngine(Protocol):
    name: str

    async def retrieve(
        self, session: AsyncSession, request: KnowledgeRequest
    ) -> KnowledgeEngineResult:
        """Read evidence using the engine's own ownership and privacy checks."""


class UnavailableKnowledgeEngine:
    """Typed placeholder when an optional engine is not configured at runtime."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    async def retrieve(
        self, _session: AsyncSession, _request: KnowledgeRequest
    ) -> KnowledgeEngineResult:
        return KnowledgeEngineResult(
            engine=self.name,
            disposition=KnowledgeDisposition.UNAVAILABLE,
            reason=self.reason,
        )


class RagKnowledgeEngine:
    name = "rag"

    def __init__(self, service: MemoryRetrievalService, *, timeout_ms: int) -> None:
        self.service = service
        self.timeout_ms = timeout_ms

    async def retrieve(
        self, session: AsyncSession, request: KnowledgeRequest
    ) -> KnowledgeEngineResult:
        if not request.memory_enabled or request.memory_excluded:
            return KnowledgeEngineResult(
                engine=self.name,
                disposition=KnowledgeDisposition.UNAVAILABLE,
                reason="memory_unavailable",
            )
        async with asyncio.timeout(self.timeout_ms / 1_000):
            result = await self.service.retrieve(
                session,
                user_id=request.user_id,
                query=request.query,
                now=request.now,
            )
        evaluation = evaluate_memory_result(
            result,
            user_id=request.user_id,
            query=request.query,
            now=request.now,
        )
        disposition = {
            MemoryEvaluationRoute.DIRECT_RAG: KnowledgeDisposition.DIRECT_ANSWER,
            MemoryEvaluationRoute.RAG_PLUS_LLM: (
                KnowledgeDisposition.CONFLICT
                if evaluation.reason == "conflicting_evidence"
                else KnowledgeDisposition.CONTINUE_WITH_EVIDENCE
            ),
            MemoryEvaluationRoute.NO_RESULT: KnowledgeDisposition.NO_RESULT,
            MemoryEvaluationRoute.UNAVAILABLE: KnowledgeDisposition.UNAVAILABLE,
        }[evaluation.route]
        facts = tuple(
            KnowledgeFact(
                key="/".join(
                    part.strip().casefold()
                    for part in (item.subject or "", item.predicate or "")
                    if part.strip()
                ),
                text=item.content,
                source_memory_ids=(item.memory_id,),
                kind=item.memory_type,
            )
            for item in evaluation.evidence
        )
        return KnowledgeEngineResult(
            engine="rag",
            disposition=disposition,
            reason=evaluation.reason,
            facts=facts,
            direct_text=evaluation.direct_text,
            evidence_ids=evaluation.evidence_ids,
            rag_result=result,
            rag_evaluation=evaluation,
        )


class OkfKnowledgeEngine:
    name = "okf"

    def __init__(self, service: OkfRetrievalService) -> None:
        self.service = service

    async def retrieve(
        self, session: AsyncSession, request: KnowledgeRequest
    ) -> KnowledgeEngineResult:
        result = await self.service.retrieve(
            session,
            OkfKnowledgeRequest(
                user_id=request.user_id,
                query=request.query,
                now=request.now,
                session_id=request.session_id,
                memory_enabled=request.memory_enabled,
                memory_excluded=request.memory_excluded,
            ),
        )
        facts = tuple(
            KnowledgeFact(
                key=item.canonical_key,
                text=item.display_text,
                source_memory_ids=item.source_memory_ids,
                kind=item.concept_type,
            )
            for item in result.evidence
        )
        evidence_ids = tuple(
            sorted(
                {memory_id for fact in facts for memory_id in fact.source_memory_ids},
                key=str,
            )
        )
        direct_text = None
        if result.status == KnowledgeDisposition.DIRECT_ANSWER and facts:
            direct_text = f"I have this saved: {facts[0].text}"
        return KnowledgeEngineResult(
            engine="okf",
            disposition=result.status,
            reason=result.degraded_reason or result.status.value,
            facts=facts,
            direct_text=direct_text,
            evidence_ids=evidence_ids,
            okf_result=result,
        )


def configured_engines(
    settings: Settings,
    *,
    rag_service: MemoryRetrievalService | None,
    okf_service: OkfRetrievalService,
    knowledge_mode: str | None = None,
) -> tuple[KnowledgeEngine, ...]:
    """Build engines for the configured default or an owner-selected mode."""

    selected_mode = knowledge_mode or settings.knowledge_mode
    if selected_mode == "rag":
        if rag_service is None:
            raise ValueError("rag_engine_missing")
        return (RagKnowledgeEngine(rag_service, timeout_ms=settings.knowledge_rag_timeout_ms),)
    if selected_mode == "okf":
        return (OkfKnowledgeEngine(okf_service),)
    if rag_service is None:
        return (
            UnavailableKnowledgeEngine("rag", "engine_not_configured"),
            OkfKnowledgeEngine(okf_service),
        )
    return (
        RagKnowledgeEngine(rag_service, timeout_ms=settings.knowledge_rag_timeout_ms),
        OkfKnowledgeEngine(okf_service),
    )
