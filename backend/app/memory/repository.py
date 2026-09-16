from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConversationTurn, MemoryItem, MemoryJob, Message, User, VoiceSession
from app.services.auth import AuthPrincipal


class MemoryRepositoryConflict(RuntimeError):
    """Raised when an idempotent Phase 6 write has different content."""


class MemoryRepository:
    """Persistence boundary whose public methods always require an owner ID."""

    async def get_owned_memory(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        include_inactive: bool = False,
    ) -> MemoryItem | None:
        query = select(MemoryItem).where(
            MemoryItem.id == memory_id,
            MemoryItem.user_id == user_id,
        )
        if not include_inactive:
            query = query.where(MemoryItem.status == "active")
        return await session.scalar(query)

    async def list_owned_memories(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        limit: int = 50,
        before: datetime | None = None,
    ) -> Sequence[MemoryItem]:
        if not 1 <= limit <= 100:
            raise ValueError("memory page limit must be between 1 and 100")
        query = (
            select(MemoryItem)
            .where(MemoryItem.user_id == user_id, MemoryItem.status == "active")
            .order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
            .limit(limit)
        )
        if before is not None:
            query = query.where(MemoryItem.created_at < before)
        return (await session.scalars(query)).all()

    async def persist_final_message(
        self,
        session: AsyncSession,
        principal: AuthPrincipal,
        *,
        turn_id: uuid.UUID,
        role: str,
        content: str,
        sequence_no: int = 0,
        content_json: dict | None = None,
        model: str | None = None,
    ) -> tuple[Message, bool]:
        """Insert or replay one final message under the authenticated owner.

        The unique turn/role/sequence key makes retries safe. A replay with
        different content is rejected instead of silently overwriting history.
        """

        if role not in {"user", "assistant", "tool", "system"}:
            raise ValueError("unsupported message role")
        if not content.strip():
            raise ValueError("final message content must not be blank")
        if sequence_no < 0:
            raise ValueError("message sequence_no must not be negative")

        owned_turn = await session.scalar(
            select(ConversationTurn)
            .join(
                VoiceSession,
                (VoiceSession.id == ConversationTurn.session_id)
                & (VoiceSession.user_id == ConversationTurn.user_id),
            )
            .where(
                ConversationTurn.id == turn_id,
                ConversationTurn.user_id == principal.user_id,
                VoiceSession.device_id == principal.device_id,
                VoiceSession.auth_session_id == principal.session_id,
            )
        )
        if owned_turn is None:
            raise LookupError("message turn is not owned by the authenticated principal")

        existing = await session.scalar(
            select(Message).where(
                Message.turn_id == turn_id,
                Message.user_id == principal.user_id,
                Message.role == role,
                Message.sequence_no == sequence_no,
            )
        )
        if existing is not None:
            if existing.content != content or existing.is_final is not True:
                raise MemoryRepositoryConflict("final message replay does not match the stored row")
            return existing, False

        message = Message(
            turn_id=turn_id,
            user_id=principal.user_id,
            role=role,
            content=content,
            content_json=content_json,
            is_final=True,
            model=model,
            sequence_no=sequence_no,
        )
        session.add(message)
        await session.flush()
        return message, True

    async def list_recent_session_messages(
        self,
        session: AsyncSession,
        principal: AuthPrincipal,
        *,
        session_id: uuid.UUID,
        exclude_turn_id: uuid.UUID | None = None,
        limit: int = 20,
    ) -> Sequence[Message]:
        """Load recent final conversation turns owned by this voice session.

        The gateway uses this only as ephemeral LLM context. The owner and
        device/auth-session predicates mirror ``persist_final_message`` so a
        session can never import another principal's conversation.
        """

        if not 1 <= limit <= 100:
            raise ValueError("conversation history limit must be between 1 and 100")
        query = (
            select(Message)
            .join(
                ConversationTurn,
                (ConversationTurn.id == Message.turn_id)
                & (ConversationTurn.user_id == Message.user_id),
            )
            .join(
                VoiceSession,
                (VoiceSession.id == ConversationTurn.session_id)
                & (VoiceSession.user_id == ConversationTurn.user_id),
            )
            .where(
                Message.user_id == principal.user_id,
                Message.role.in_(("user", "assistant")),
                Message.is_final.is_(True),
                Message.content.is_not(None),
                ConversationTurn.session_id == session_id,
                VoiceSession.user_id == principal.user_id,
                VoiceSession.device_id == principal.device_id,
                VoiceSession.auth_session_id == principal.session_id,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        if exclude_turn_id is not None:
            query = query.where(Message.turn_id != exclude_turn_id)
        messages = list((await session.scalars(query)).all())
        messages.reverse()
        return messages

    async def enqueue_extract_turn(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        source_message_id: uuid.UUID,
        source_turn_id: uuid.UUID,
        source_session_id: uuid.UUID,
        policy_version: str,
    ) -> tuple[MemoryJob, bool]:
        """Enqueue one extraction job with a deterministic replay key."""

        idempotency_key = f"extract_turn:{source_message_id}:{policy_version}"
        existing = await session.scalar(
            select(MemoryJob).where(
                MemoryJob.user_id == user_id,
                MemoryJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing, False

        job = MemoryJob(
            user_id=user_id,
            job_type="extract_turn",
            source_message_id=source_message_id,
            source_turn_id=source_turn_id,
            source_session_id=source_session_id,
            idempotency_key=idempotency_key,
            status="pending",
            attempts=0,
            available_at=datetime.now(UTC),
            policy_version=policy_version,
        )
        session.add(job)
        await session.flush()
        return job, True

    async def enqueue_graph_index(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        policy_version: str,
    ) -> tuple[MemoryJob, bool]:
        """Enqueue one graph index operation per owner, memory and policy version."""

        idempotency_key = f"graph:{user_id}:{memory_id}:{policy_version}"
        statement = (
            pg_insert(MemoryJob)
            .values(
                user_id=user_id,
                job_type="index_memory_graph",
                memory_id=memory_id,
                idempotency_key=idempotency_key,
                status="pending",
                attempts=0,
                available_at=datetime.now(UTC),
                policy_version=policy_version,
            )
            .on_conflict_do_nothing(constraint="uq_memory_jobs_user_idempotency")
            .returning(MemoryJob.id)
        )
        async with session.begin_nested():
            inserted_id = await session.scalar(statement)

        job = await session.scalar(
            select(MemoryJob).where(
                MemoryJob.user_id == user_id,
                MemoryJob.idempotency_key == idempotency_key,
            )
        )
        if job is None:
            raise MemoryRepositoryConflict("graph index job could not be read after enqueue")
        return job, inserted_id is not None

    async def enqueue_reembed_memory(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        model_version: str,
        policy_version: str,
    ) -> tuple[MemoryJob, bool]:
        """Enqueue one idempotent re-embedding operation for an owned memory."""

        owned_memory = await session.scalar(
            select(MemoryItem.id).where(
                MemoryItem.id == memory_id,
                MemoryItem.user_id == user_id,
                MemoryItem.status == "active",
            )
        )
        if owned_memory is None:
            raise MemoryRepositoryConflict("memory is not owned or active")
        idempotency_key = f"reembed_memory:{memory_id}:{model_version}"
        statement = (
            pg_insert(MemoryJob)
            .values(
                user_id=user_id,
                job_type="reembed_memory",
                memory_id=memory_id,
                idempotency_key=idempotency_key,
                status="pending",
                attempts=0,
                available_at=datetime.now(UTC),
                policy_version=policy_version,
                model_version=model_version,
            )
            .on_conflict_do_nothing(constraint="uq_memory_jobs_user_idempotency")
            .returning(MemoryJob.id)
        )
        async with session.begin_nested():
            inserted_id = await session.scalar(statement)
        job = await session.scalar(
            select(MemoryJob).where(
                MemoryJob.user_id == user_id,
                MemoryJob.idempotency_key == idempotency_key,
            )
        )
        if job is None:
            raise MemoryRepositoryConflict("reembed job could not be read after enqueue")
        return job, inserted_id is not None

    async def bump_memory_version(self, session: AsyncSession, *, user_id: uuid.UUID) -> None:
        """Advance the per-user invalidation version without loading content."""

        result = await session.execute(
            update(User).where(User.id == user_id).values(memory_version=User.memory_version + 1)
        )
        if result.rowcount != 1:
            raise LookupError("memory owner does not exist")
