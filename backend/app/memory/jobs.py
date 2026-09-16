from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.graph.indexing import GraphIndexingService
from app.models import MemoryChunk, MemoryItem, MemoryJob, Message, User, VoiceSession

from .extraction import extract_explicit_candidates
from .policy import validate_candidate
from .providers import RemoteEmbeddingProvider
from .types import MemorySourceKind
from .writer import MemoryWriter

LOGGER = logging.getLogger("voice-assistance-backend")


class MemoryJobHandler(Protocol):
    async def __call__(self, session: AsyncSession, job: MemoryJob) -> None: ...


class MemoryJobRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def claim_next(self, session: AsyncSession) -> MemoryJob | None:
        now = datetime.now(UTC)
        query = (
            select(MemoryJob)
            .where(
                or_(
                    MemoryJob.status.in_(("pending", "retry_wait"))
                    & (MemoryJob.available_at <= now),
                    (MemoryJob.status == "running")
                    & (
                        MemoryJob.locked_at
                        <= now - timedelta(seconds=self.settings.memory_job_lease_seconds)
                    ),
                )
            )
            .order_by(MemoryJob.available_at.asc(), MemoryJob.created_at.asc(), MemoryJob.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = await session.scalar(query)
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_at = now
        await session.flush()
        return job

    async def complete(self, session: AsyncSession, job: MemoryJob) -> None:
        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        job.locked_at = None
        await session.flush()

    async def fail(self, session: AsyncSession, job: MemoryJob, code: str) -> None:
        job.last_error_code = code[:128]
        job.locked_at = None
        if job.attempts >= self.settings.memory_job_max_attempts:
            job.status = "dead"
        else:
            job.status = "retry_wait"
            job.available_at = datetime.now(UTC) + timedelta(seconds=min(300, 2**job.attempts))
        await session.flush()


class MemoryJobWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        embedding_provider: RemoteEmbeddingProvider | None = None,
        writer: MemoryWriter | None = None,
        graph_indexer: GraphIndexingService | None = None,
    ) -> None:
        self.settings = settings
        self.repository = MemoryJobRepository(settings)
        self.embedding_provider = embedding_provider
        self.writer = writer or MemoryWriter(settings)
        self.graph_indexer = graph_indexer or GraphIndexingService(settings)

    async def run_once(self, session: AsyncSession) -> bool:
        job = await self.repository.claim_next(session)
        if job is None:
            return False
        started = time.perf_counter()
        job_uuid = job.id
        job_id = _short_id(job.id)
        user_id = _short_id(job.user_id)
        memory_id = _short_id(job.memory_id) if job.memory_id else None
        job_type = job.job_type
        attempted_count = job.attempts
        LOGGER.info(
            "memory worker job received",
            extra={
                "event": "memory.worker.job_received",
                "job_id": job_id,
                "user_id": user_id,
                "job_type": job_type,
                "attempt": attempted_count,
            },
        )
        try:
            if job.job_type == "extract_turn":
                await self._extract_turn(session, job)
            elif job.job_type == "embed_memory":
                await self._embed_memory(session, job)
            elif job.job_type == "purge_session":
                await self._purge_session(session, job)
            elif job.job_type == "index_memory_graph":
                await self._index_memory_graph(session, job)
            else:
                raise ValueError("memory_job_type_unsupported")
            await self.repository.complete(session, job)
            await session.commit()
            LOGGER.info(
                "memory worker job completed",
                extra={
                    "event": "memory.worker.job_completed",
                    "job_id": job_id,
                    "user_id": user_id,
                    "job_type": job_type,
                    "attempt": attempted_count,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
        except asyncio.CancelledError:
            await session.rollback()
            LOGGER.info(
                "memory worker job cancelled",
                extra={
                    "event": "memory.worker.job_cancelled",
                    "job_id": job_id,
                    "user_id": user_id,
                    "job_type": job_type,
                    "attempt": attempted_count,
                },
            )
            raise
        except Exception as error:  # noqa: BLE001 - bounded retry/dead-letter boundary
            await session.rollback()
            # The rollback expires the claimed object, so reload it by ID before updating.
            current = datetime.now(UTC)
            status = (
                "dead" if attempted_count >= self.settings.memory_job_max_attempts else "retry_wait"
            )
            await session.execute(
                update(MemoryJob)
                .where(MemoryJob.id == job_uuid)
                .values(
                    status=status,
                    attempts=attempted_count,
                    locked_at=None,
                    last_error_code=_error_code(error),
                    available_at=(
                        current
                        if status == "dead"
                        else current + timedelta(seconds=min(300, 2**attempted_count))
                    ),
                )
            )
            await session.commit()
            LOGGER.error(
                "memory worker job failed",
                extra={
                    "event": "memory.worker.job_failed",
                    "job_id": job_id,
                    "user_id": user_id,
                    "job_type": job_type,
                    "attempt": attempted_count,
                    "status": status,
                    "error_code": _error_code(error),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            if job_type == "index_memory_graph":
                LOGGER.warning(
                    "memory graph index job retry scheduled"
                    if status == "retry_wait"
                    else "memory graph index job dead-lettered",
                    extra={
                        "event": (
                            "memory.graph.job_retry"
                            if status == "retry_wait"
                            else "memory.graph.job_dead"
                        ),
                        "job_id": job_id,
                        "memory_id": memory_id,
                        "user_id": user_id,
                        "attempt": attempted_count,
                        "status": status,
                        "error_code": _error_code(error),
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    },
                )
        return True

    async def _index_memory_graph(self, session: AsyncSession, job: MemoryJob) -> None:
        started = time.perf_counter()
        job_id = _short_id(job.id)
        memory_id = _short_id(job.memory_id) if job.memory_id else None
        user_id = _short_id(job.user_id)
        LOGGER.info(
            "memory graph index job started",
            extra={
                "event": "memory.graph.job_started",
                "job_id": job_id,
                "memory_id": memory_id,
                "user_id": user_id,
                "policy_version": job.policy_version,
                "attempt": job.attempts,
            },
        )
        if job.memory_id is None:
            LOGGER.info(
                "memory graph index job skipped",
                extra={
                    "event": "memory.graph.job_skipped",
                    "job_id": job_id,
                    "memory_id": None,
                    "user_id": user_id,
                    "reason_code": "memory_missing",
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            return

        result = await self.graph_indexer.index_memory(
            session,
            user_id=job.user_id,
            memory_id=job.memory_id,
            policy_version=job.policy_version,
        )
        event = (
            "memory.graph.job_skipped"
            if result.status == "skipped"
            else "memory.graph.job_completed"
        )
        LOGGER.info(
            "memory graph index job %s",
            result.status,
            extra={
                "event": event,
                "job_id": job_id,
                "memory_id": memory_id,
                "user_id": user_id,
                "status": result.status,
                "reason_code": result.reason_code,
                "entity_count": result.entities_created + result.entities_reused,
                "entities_created": result.entities_created,
                "entities_reused": result.entities_reused,
                "edge_count": result.edges_created,
                "alias_count": result.aliases_created,
                "policy_version": job.policy_version,
                "timings_ms": dict(result.timings_ms),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )

    async def _extract_turn(self, session: AsyncSession, job: MemoryJob) -> None:
        if job.source_message_id is None:
            raise ValueError("memory_source_message_missing")
        message = await session.scalar(
            select(Message).where(
                Message.id == job.source_message_id, Message.user_id == job.user_id
            )
        )
        if message is None or message.role != "user" or not message.is_final or not message.content:
            raise ValueError("memory_source_message_invalid")
        user = await session.get(User, job.user_id)
        if user is None or not user.memory_enabled:
            return
        if job.source_session_id is not None:
            voice_session = await session.scalar(
                select(VoiceSession).where(
                    VoiceSession.id == job.source_session_id,
                    VoiceSession.user_id == job.user_id,
                )
            )
            if (
                voice_session
                and (voice_session.client_metadata or {}).get("memory_excluded") is True
            ):
                return
        candidates = extract_explicit_candidates(message.content)
        for candidate in candidates:
            safe = validate_candidate(candidate, message.content)
            await self.writer.write_candidate(
                session,
                user_id=job.user_id,
                candidate=safe,
                source_kind=MemorySourceKind.AUTOMATIC,
                source_message_id=message.id,
                source_turn_id=job.source_turn_id,
                source_session_id=job.source_session_id,
                extraction_policy_version=job.policy_version,
            )

    async def _embed_memory(self, session: AsyncSession, job: MemoryJob) -> None:
        if job.memory_id is None or self.embedding_provider is None:
            raise ValueError("memory_embedding_provider_unavailable")
        user = await session.get(User, job.user_id)
        if user is None or not user.memory_enabled:
            return
        chunks = list(
            (
                await session.scalars(
                    select(MemoryChunk)
                    .where(
                        MemoryChunk.memory_id == job.memory_id, MemoryChunk.user_id == job.user_id
                    )
                    .order_by(MemoryChunk.chunk_no.asc())
                )
            ).all()
        )
        if not chunks:
            raise ValueError("memory_chunks_missing")
        started = time.perf_counter()
        LOGGER.info(
            "memory embedding started",
            extra={
                "event": "memory.worker.embedding_started",
                "job_id": _short_id(job.id),
                "memory_id": _short_id(job.memory_id),
                "user_id": _short_id(job.user_id),
                "chunk_count": len(chunks),
            },
        )
        response = await self.embedding_provider.embed(tuple(chunk.content for chunk in chunks))
        now = datetime.now(UTC)
        for chunk, vector in zip(chunks, response.vectors, strict=True):
            chunk.embedding = list(vector)
            chunk.embedding_model = response.model
            chunk.embedded_at = now
        LOGGER.info(
            "memory embedding completed",
            extra={
                "event": "memory.worker.embedding_completed",
                "job_id": _short_id(job.id),
                "memory_id": _short_id(job.memory_id),
                "user_id": _short_id(job.user_id),
                "embedding_dimension": self.settings.memory_embedding_dimension,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )

    async def _purge_session(self, session: AsyncSession, job: MemoryJob) -> None:
        if job.source_session_id is None:
            raise ValueError("memory_source_session_missing")
        await session.execute(
            MemoryItem.__table__.delete().where(
                MemoryItem.user_id == job.user_id,
                MemoryItem.source_session_id == job.source_session_id,
            )
        )


def _error_code(error: Exception) -> str:
    return str(getattr(error, "code", "memory_job_failed"))[:128]


def _short_id(value: object) -> str:
    return str(value).replace("-", "")[:12]
