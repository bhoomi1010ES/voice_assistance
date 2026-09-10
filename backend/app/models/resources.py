from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryItem(Base):
    __tablename__ = "memory_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, default="summary")
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    predicate: Mapped[str | None] = mapped_column(String(128), nullable=True)
    object_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    occurred_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    occurred_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    salience: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="legacy")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extraction_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(content, ''))", persisted=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["source_message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            ondelete="CASCADE",
            name="fk_memory_items_source_message_user",
        ),
        ForeignKeyConstraint(
            ["source_turn_id", "user_id"],
            ["conversation_turns.id", "conversation_turns.user_id"],
            ondelete="CASCADE",
            name="fk_memory_items_source_turn_user",
        ),
        ForeignKeyConstraint(
            ["source_session_id", "user_id"],
            ["voice_sessions.id", "voice_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_memory_items_source_session_user",
        ),
        ForeignKeyConstraint(
            ["supersedes_id"],
            ["memory_items.id"],
            ondelete="SET NULL",
            name="fk_memory_items_supersedes",
        ),
        UniqueConstraint("id", "user_id", name="uq_memory_items_id_user_id"),
        CheckConstraint(
            "memory_type IN ('fact', 'preference', 'event', 'relationship', 'routine', "
            "'project', 'summary')",
            name="ck_memory_items_type",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memory_items_confidence"),
        CheckConstraint("salience >= 0 AND salience <= 1", name="ck_memory_items_salience"),
        CheckConstraint(
            "source_kind IN ('automatic', 'explicit_tool', 'manual_api', 'legacy')",
            name="ck_memory_items_source_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'deleted')",
            name="ck_memory_items_status",
        ),
        Index("ix_memory_items_user_created", "user_id", "created_at"),
        Index("ix_memory_items_user_status_type", "user_id", "status", "memory_type"),
        Index("ix_memory_items_user_occurred", "user_id", "occurred_start_at"),
        Index("ix_memory_items_source_message", "user_id", "source_message_id"),
        Index("ix_memory_items_search_tsv", "search_tsv", postgresql_using="gin"),
        Index(
            "uq_memory_items_user_dedupe_active",
            "user_id",
            "dedupe_key",
            unique=True,
            postgresql_where=(status == "active") & (dedupe_key.is_not(None)),
        ),
    )


class Message(Base):
    """A final, ownership-scoped conversation message.

    Streaming deltas remain ephemeral. Only final user/assistant/tool/system
    messages belong here, which gives Phase 6 provenance a stable source row.
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["turn_id", "user_id"],
            ["conversation_turns.id", "conversation_turns.user_id"],
            ondelete="CASCADE",
            name="fk_messages_turn_user",
        ),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        UniqueConstraint("id", "user_id", name="uq_messages_id_user_id"),
        UniqueConstraint(
            "turn_id",
            "role",
            "sequence_no",
            name="uq_messages_turn_role_sequence",
        ),
        CheckConstraint("role IN ('user', 'assistant', 'tool', 'system')", name="ck_messages_role"),
        CheckConstraint("sequence_no >= 0", name="ck_messages_sequence_no"),
        Index("ix_messages_user_created", "user_id", "created_at"),
        Index("ix_messages_turn_sequence", "turn_id", "sequence_no"),
    )


class MemoryChunk(Base):
    __tablename__ = "memory_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1024), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False, default="BAAI/bge-m3")
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(content, ''))", persisted=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            ondelete="CASCADE",
            name="fk_memory_chunks_memory_user",
        ),
        UniqueConstraint("memory_id", "chunk_no", name="uq_memory_chunks_memory_chunk"),
        CheckConstraint("chunk_no >= 0", name="ck_memory_chunks_chunk_no"),
        Index("ix_memory_chunks_user_memory", "user_id", "memory_id"),
        Index("ix_memory_chunks_search_tsv", "search_tsv", postgresql_using="gin"),
        Index(
            "ix_memory_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        UniqueConstraint(
            "user_id",
            "entity_type",
            "normalized_name",
            name="uq_entities_user_type_name",
        ),
        UniqueConstraint("id", "user_id", name="uq_entities_id_user_id"),
        Index("ix_entities_user_name", "user_id", "normalized_name"),
    )


class MemoryEntity(Base):
    __tablename__ = "memory_entities"

    memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            ondelete="CASCADE",
            name="fk_memory_entities_memory_user",
        ),
        ForeignKeyConstraint(
            ["entity_id", "user_id"],
            ["entities.id", "entities.user_id"],
            ondelete="CASCADE",
            name="fk_memory_entities_entity_user",
        ),
        Index("ix_memory_entities_user", "user_id"),
    )


class MemoryJob(Base):
    __tablename__ = "memory_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    memory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["source_message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            ondelete="CASCADE",
            name="fk_memory_jobs_source_message_user",
        ),
        ForeignKeyConstraint(
            ["source_turn_id", "user_id"],
            ["conversation_turns.id", "conversation_turns.user_id"],
            ondelete="CASCADE",
            name="fk_memory_jobs_source_turn_user",
        ),
        ForeignKeyConstraint(
            ["source_session_id", "user_id"],
            ["voice_sessions.id", "voice_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_memory_jobs_source_session_user",
        ),
        ForeignKeyConstraint(
            ["memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            ondelete="CASCADE",
            name="fk_memory_jobs_memory_user",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_memory_jobs_user_idempotency"),
        CheckConstraint(
            "job_type IN ('extract_turn', 'embed_memory', 'reembed_memory', 'purge_session')",
            name="ck_memory_jobs_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'retry_wait', 'completed', 'dead', 'cancelled')",
            name="ck_memory_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_memory_jobs_attempts"),
        Index("ix_memory_jobs_claim", "status", "available_at"),
        Index("ix_memory_jobs_user_status", "user_id", "status"),
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    source_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["source_turn_id", "user_id"],
            ["conversation_turns.id", "conversation_turns.user_id"],
            ondelete="SET NULL",
            name="fk_tasks_source_turn_user",
        ),
        UniqueConstraint("id", "user_id", name="uq_tasks_id_user_id"),
        Index("ix_tasks_user_created", "user_id", "created_at"),
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_user_due", "user_id", "due_at"),
        Index("ix_tasks_user_status_due", "user_id", "status", "due_at"),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_tasks_priority",
        ),
    )


class Reminder(Base):
    """Durable one-shot or recurring reminder delivery state."""

    __tablename__ = "reminders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    recurrence_rule: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    delivery_channel: Mapped[str] = mapped_column(String(32), nullable=False, default="push")
    delivery_id: Mapped[str] = mapped_column(
        String(255), nullable=False, default=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["tasks.id", "tasks.user_id"],
            ondelete="SET NULL",
            name="fk_reminders_task_user",
        ),
        UniqueConstraint("id", "user_id", name="uq_reminders_id_user_id"),
        UniqueConstraint("delivery_id", name="uq_reminders_delivery_id"),
        Index("ix_reminders_user_created", "user_id", "created_at"),
        Index("ix_reminders_user_status_trigger", "user_id", "status", "trigger_at"),
        Index("ix_reminders_claim", "status", "trigger_at", "next_attempt_at"),
        Index("ix_reminders_lease", "status", "lease_expires_at"),
        Index("ix_reminders_user_task", "user_id", "task_id"),
        CheckConstraint(
            "status IN ('scheduled', 'processing', 'retry_wait', 'sent', 'failed', 'cancelled')",
            name="ck_reminders_status",
        ),
        CheckConstraint("delivery_channel IN ('push')", name="ck_reminders_delivery_channel"),
        CheckConstraint("attempt_count >= 0", name="ck_reminders_attempt_count"),
        CheckConstraint("occurrence_count >= 0", name="ck_reminders_occurrence_count"),
    )


class ToolExecutionRecord(Base):
    """Durable idempotency and result record for a server-side tool call."""

    __tablename__ = "tool_execution_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    arguments_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    result_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        UniqueConstraint(
            "user_id",
            "turn_id",
            "tool_name",
            "tool_call_id",
            name="uq_tool_execution_idempotency",
        ),
        Index("ix_tool_execution_user_created", "user_id", "created_at"),
        CheckConstraint(
            "status IN ('started', 'completed')",
            name="ck_tool_execution_status",
        ),
    )
