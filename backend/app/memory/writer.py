from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.graph.policy import GRAPH_INDEX_POLICY_VERSION, derive_relationship_spec
from app.models import (
    Entity,
    EntityRelationship,
    MemoryChunk,
    MemoryEntity,
    MemoryItem,
    MemoryJob,
    User,
    VoiceSession,
)

from .chunking import chunk_text
from .policy import ExtractionCandidate, validate_safe_json
from .repository import MemoryRepository
from .types import MemorySourceKind, MemoryStatus, MemoryType, normalize_memory_text

LOGGER = logging.getLogger("voice-assistance-backend")


class MemoryWriteConflict(RuntimeError):
    """A manual or automatic memory write could not be reconciled safely."""


class MemoryWriter:
    def __init__(self, settings: Settings, repository: MemoryRepository | None = None) -> None:
        self.settings = settings
        self.repository = repository or MemoryRepository()

    async def write_candidate(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        candidate: ExtractionCandidate,
        source_kind: MemorySourceKind,
        source_message_id: uuid.UUID | None = None,
        source_turn_id: uuid.UUID | None = None,
        source_session_id: uuid.UUID | None = None,
        extraction_policy_version: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> tuple[MemoryItem, bool]:
        if candidate.confidence < self.settings.memory_min_confidence:
            raise MemoryWriteConflict("memory_candidate_low_confidence")
        if candidate.salience < self.settings.memory_min_salience:
            raise MemoryWriteConflict("memory_candidate_low_salience")
        validate_safe_json(candidate.object_json)
        validate_safe_json(metadata_json)
        existing = await session.scalar(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.dedupe_key == candidate.dedupe_key,
                MemoryItem.status == MemoryStatus.ACTIVE,
            )
        )
        if existing is not None:
            return existing, False

        supersedes_id: uuid.UUID | None = None
        if candidate.memory_type == MemoryType.PREFERENCE and candidate.subject:
            old = await session.scalar(
                select(MemoryItem)
                .where(
                    MemoryItem.user_id == user_id,
                    MemoryItem.memory_type == MemoryType.PREFERENCE,
                    MemoryItem.subject == candidate.subject,
                    MemoryItem.predicate == candidate.predicate,
                    MemoryItem.status == MemoryStatus.ACTIVE,
                )
                .order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
            )
            if old is not None:
                old.status = MemoryStatus.SUPERSEDED
                supersedes_id = old.id
                if self.settings.graph_write_enabled:
                    await session.execute(
                        update(EntityRelationship)
                        .where(
                            EntityRelationship.user_id == user_id,
                            EntityRelationship.source_memory_id == old.id,
                            EntityRelationship.status == "active",
                        )
                        .values(status="superseded")
                    )

        item = MemoryItem(
            user_id=user_id,
            content=candidate.content,
            metadata_json=(
                {**metadata_json, "object": candidate.object_json}
                if metadata_json is not None and candidate.object_json
                else (
                    metadata_json
                    or ({"object": candidate.object_json} if candidate.object_json else None)
                )
            ),
            memory_type=candidate.memory_type,
            subject=candidate.subject,
            predicate=candidate.predicate,
            object_json=candidate.object_json,
            confidence=candidate.confidence,
            salience=candidate.salience,
            source_message_id=source_message_id,
            source_turn_id=source_turn_id,
            source_session_id=source_session_id,
            source_kind=source_kind,
            supersedes_id=supersedes_id,
            dedupe_key=candidate.dedupe_key,
            extraction_policy_version=extraction_policy_version
            or self.settings.memory_policy_version,
            status=MemoryStatus.ACTIVE,
        )
        session.add(item)
        await session.flush()
        chunks = chunk_text(
            candidate.content,
            max_chars=self.settings.memory_chunk_max_chars,
            overlap_chars=min(
                self.settings.memory_chunk_overlap_chars,
                self.settings.memory_chunk_max_chars - 1,
            ),
        )
        for chunk_no, content in enumerate(chunks):
            session.add(
                MemoryChunk(
                    memory_id=item.id,
                    user_id=user_id,
                    chunk_no=chunk_no,
                    content=content,
                    token_count=max(1, len(content.split())),
                    embedding_model=self.settings.memory_expected_embedding_model,
                )
            )
        if candidate.subject:
            normalized_subject = normalize_memory_text(candidate.subject)
            entity = await session.scalar(
                select(Entity).where(
                    Entity.user_id == user_id,
                    Entity.entity_type == "other",
                    Entity.normalized_name == normalized_subject,
                )
            )
            if entity is None:
                entity = Entity(
                    user_id=user_id,
                    # Legacy memory subjects are not reliably typed. Keep
                    # them as ``other`` until deterministic graph indexing
                    # establishes endpoint semantics.
                    entity_type="other",
                    canonical_name=candidate.subject,
                    normalized_name=normalized_subject,
                )
                session.add(entity)
                await session.flush()
            session.add(
                MemoryEntity(
                    memory_id=item.id,
                    entity_id=entity.id,
                    user_id=user_id,
                    relation=candidate.predicate,
                )
            )
        session.add(
            # The memory ID makes this job idempotent across worker retries.
            MemoryJob(
                user_id=user_id,
                job_type="embed_memory",
                memory_id=item.id,
                idempotency_key=(
                    f"embed_memory:{item.id}:{self.settings.memory_expected_embedding_model}"
                ),
                status="pending",
                policy_version=extraction_policy_version or self.settings.memory_policy_version,
                model_version=self.settings.memory_expected_embedding_model,
                available_at=datetime.now(UTC),
            )
        )
        if self.settings.graph_write_enabled and source_kind != MemorySourceKind.LEGACY:
            graph_spec, _reason = derive_relationship_spec(
                memory_type=item.memory_type,
                subject=item.subject,
                predicate=item.predicate,
                object_json=item.object_json,
            )
            if graph_spec is not None:
                owner_memory_enabled = await session.scalar(
                    select(User.memory_enabled).where(User.id == user_id)
                )
                session_included = True
                if source_session_id is not None:
                    source_session = await session.scalar(
                        select(VoiceSession).where(
                            VoiceSession.id == source_session_id,
                            VoiceSession.user_id == user_id,
                        )
                    )
                    if (
                        source_session
                        and (source_session.client_metadata or {}).get("memory_excluded") is True
                    ):
                        session_included = False
                if owner_memory_enabled and session_included:
                    graph_job, enqueued = await self.repository.enqueue_graph_index(
                        session,
                        user_id=user_id,
                        memory_id=item.id,
                        policy_version=GRAPH_INDEX_POLICY_VERSION,
                    )
                    if enqueued:
                        LOGGER.info(
                            "memory graph index job enqueued",
                            extra={
                                "event": "memory.graph.job_enqueued",
                                "job_id": _short_id(graph_job.id),
                                "memory_id": _short_id(item.id),
                                "user_id": _short_id(user_id),
                                "policy_version": GRAPH_INDEX_POLICY_VERSION,
                            },
                        )
        await self.repository.bump_memory_version(session, user_id=user_id)
        return item, True

    async def write_many(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        candidates: Iterable[ExtractionCandidate],
        source_kind: MemorySourceKind,
        source_message_id: uuid.UUID | None = None,
        source_turn_id: uuid.UUID | None = None,
        source_session_id: uuid.UUID | None = None,
    ) -> list[MemoryItem]:
        written: list[MemoryItem] = []
        for candidate in candidates:
            item, _ = await self.write_candidate(
                session,
                user_id=user_id,
                candidate=candidate,
                source_kind=source_kind,
                source_message_id=source_message_id,
                source_turn_id=source_turn_id,
                source_session_id=source_session_id,
            )
            written.append(item)
        return written


def _short_id(value: object) -> str:
    return str(value).replace("-", "")[:12]
