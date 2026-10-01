from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, REAL, TSVECTOR, UUID
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
            ["supersedes_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            ondelete="SET NULL",
            name="fk_memory_items_supersedes_user",
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
        CheckConstraint(
            "entity_type IN ('self', 'person', 'place', 'organization', 'project', "
            "'product', 'event', 'other')",
            name="ck_entities_type",
        ),
        Index(
            "uq_entities_user_self",
            "user_id",
            unique=True,
            postgresql_where=(entity_type == "self"),
        ),
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


class EntityAlias(Base):
    """User-owned exact alias and optional memory provenance for one entity."""

    __tablename__ = "entity_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    source_memory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["entity_id", "user_id"],
            ["entities.id", "entities.user_id"],
            ondelete="CASCADE",
            name="fk_entity_aliases_entity_user",
        ),
        ForeignKeyConstraint(
            ["source_memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            ondelete="CASCADE",
            name="fk_entity_aliases_source_memory_user",
        ),
        UniqueConstraint(
            "user_id",
            "entity_id",
            "normalized_alias",
            name="uq_entity_aliases_user_entity_normalized",
        ),
        CheckConstraint(
            "source_kind IN ('canonical', 'memory', 'manual', 'legacy')",
            name="ck_entity_aliases_source_kind",
        ),
        Index("ix_entity_aliases_user_normalized", "user_id", "normalized_alias"),
        Index("ix_entity_aliases_user_entity", "user_id", "entity_id"),
    )


class EntityRelationship(Base):
    """One user-owned, memory-supported entity-to-entity relationship."""

    __tablename__ = "entity_relationships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    extraction_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["source_entity_id", "user_id"],
            ["entities.id", "entities.user_id"],
            ondelete="CASCADE",
            name="fk_entity_relationships_source_entity_user",
        ),
        ForeignKeyConstraint(
            ["target_entity_id", "user_id"],
            ["entities.id", "entities.user_id"],
            ondelete="CASCADE",
            name="fk_entity_relationships_target_entity_user",
        ),
        ForeignKeyConstraint(
            ["source_memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            ondelete="CASCADE",
            name="fk_entity_relationships_source_memory_user",
        ),
        UniqueConstraint(
            "user_id",
            "source_entity_id",
            "relationship_type",
            "target_entity_id",
            "source_memory_id",
            name="uq_entity_relationships_evidence",
        ),
        CheckConstraint(
            "relationship_type IN ('COLLEAGUE_OF', 'FRIEND_OF', 'FAMILY_OF', 'WORKS_AT', "
            "'WORKS_ON', 'LIVES_IN', 'LOCATED_AT', 'OWNS', 'MEMBER_OF', 'MANAGES', "
            "'REPORTS_TO', 'DEPENDS_ON', 'RELATED_TO', 'RESPONSIBLE_FOR', 'TESTED_BY', "
            "'ASSIGNED_TO', 'BLOCKED_BY', 'DISCUSSED_WITH')",
            name="ck_entity_relationships_type",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded')",
            name="ck_entity_relationships_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_entity_relationships_confidence",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_entity_relationships_validity",
        ),
        CheckConstraint(
            "source_entity_id <> target_entity_id",
            name="ck_entity_relationships_no_self_edge",
        ),
        Index(
            "ix_entity_relationships_source_status",
            "user_id",
            "source_entity_id",
            "status",
        ),
        Index(
            "ix_entity_relationships_target_status",
            "user_id",
            "target_entity_id",
            "status",
        ),
        Index(
            "ix_entity_relationships_type_status",
            "user_id",
            "relationship_type",
            "status",
        ),
        Index(
            "ix_entity_relationships_user_memory",
            "user_id",
            "source_memory_id",
        ),
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
            "job_type IN ('extract_turn', 'embed_memory', 'reembed_memory', 'purge_session', "
            "'index_memory_graph')",
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


class OkfConcept(Base):
    """Stable, owner-scoped identity for one knowledge concept."""

    __tablename__ = "okf_concepts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_concept_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    concept_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["parent_concept_id", "user_id"],
            ["okf_concepts.id", "okf_concepts.user_id"],
            ondelete="SET NULL",
            name="fk_okf_concepts_parent_user",
        ),
        UniqueConstraint("id", "user_id", name="uq_okf_concepts_id_user_id"),
        UniqueConstraint("user_id", "canonical_key", name="uq_okf_concepts_user_key"),
        CheckConstraint(
            "concept_type IN ('profile', 'preference', 'project', 'decision', "
            "'relationship', 'fact')",
            name="ck_okf_concepts_type",
        ),
        CheckConstraint(
            "status IN ('active', 'contested', 'retired')",
            name="ck_okf_concepts_status",
        ),
        CheckConstraint("btrim(title) <> ''", name="ck_okf_concepts_title_nonblank"),
        CheckConstraint(
            "canonical_key ~ '^(profile|preferences|projects|relationships|facts)/"
            "[a-z0-9]+(-[a-z0-9]+)*(/[a-z0-9]+(-[a-z0-9]+)*){0,6}$'",
            name="ck_okf_concepts_canonical_key",
        ),
        CheckConstraint("btrim(policy_version) <> ''", name="ck_okf_concepts_policy_nonblank"),
        Index("ix_okf_concepts_user_type_status", "user_id", "concept_type", "status"),
    )


class OkfConceptAssertion(Base):
    """The current materialized value for an independently supported claim."""

    __tablename__ = "okf_concept_assertions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    concept_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False, default=1.0)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["concept_id", "user_id"],
            ["okf_concepts.id", "okf_concepts.user_id"],
            ondelete="CASCADE",
            name="fk_okf_assertions_concept_user",
        ),
        ForeignKeyConstraint(
            ["user_id", "id", "current_version"],
            [
                "okf_concept_versions.user_id",
                "okf_concept_versions.assertion_id",
                "okf_concept_versions.version",
            ],
            name="fk_okf_assertions_current_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        UniqueConstraint("id", "user_id", name="uq_okf_assertions_id_user_id"),
        CheckConstraint(
            "status IN ('active', 'superseded', 'retired')",
            name="ck_okf_assertions_status",
        ),
        CheckConstraint("current_version >= 1", name="ck_okf_assertions_current_version"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_okf_assertions_confidence"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_okf_assertions_validity",
        ),
        CheckConstraint("btrim(display_text) <> ''", name="ck_okf_assertions_display_nonblank"),
        CheckConstraint("btrim(policy_version) <> ''", name="ck_okf_assertions_policy_nonblank"),
        Index("ix_okf_assertions_user_concept_status", "user_id", "concept_id", "status"),
    )


class OkfConceptVersion(Base):
    """Immutable snapshot of an accepted assertion change."""

    __tablename__ = "okf_concept_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    concept_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assertion_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["concept_id", "user_id"],
            ["okf_concepts.id", "okf_concepts.user_id"],
            ondelete="CASCADE",
            name="fk_okf_versions_concept_user",
        ),
        ForeignKeyConstraint(
            ["assertion_id", "user_id"],
            ["okf_concept_assertions.id", "okf_concept_assertions.user_id"],
            ondelete="CASCADE",
            name="fk_okf_versions_assertion_user",
        ),
        UniqueConstraint("id", "user_id", name="uq_okf_versions_id_user_id"),
        UniqueConstraint(
            "user_id", "assertion_id", "version", name="uq_okf_versions_assertion_version"
        ),
        CheckConstraint("version >= 1", name="ck_okf_versions_version"),
        CheckConstraint(
            "status IN ('active', 'superseded', 'retired')", name="ck_okf_versions_status"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_okf_versions_confidence"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_okf_versions_validity",
        ),
        CheckConstraint(
            "change_kind IN ('create', 'update', 'supersede', 'retire', 'restore')",
            name="ck_okf_versions_change_kind",
        ),
        CheckConstraint("btrim(display_text) <> ''", name="ck_okf_versions_display_nonblank"),
        CheckConstraint("btrim(policy_version) <> ''", name="ck_okf_versions_policy_nonblank"),
    )


class OkfConceptSource(Base):
    """Content-free provenance linking a version to owned source evidence."""

    __tablename__ = "okf_concept_sources"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    concept_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "user_id",
            "concept_version_id",
            "memory_id",
            "evidence_role",
            name="pk_okf_concept_sources",
        ),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["concept_version_id", "user_id"],
            ["okf_concept_versions.id", "okf_concept_versions.user_id"],
            ondelete="CASCADE",
            name="fk_okf_sources_version_user",
        ),
        ForeignKeyConstraint(
            ["memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            ondelete="CASCADE",
            name="fk_okf_sources_memory_user",
        ),
        CheckConstraint(
            "evidence_role IN ('supports', 'contradicts', 'supersedes')",
            name="ck_okf_sources_evidence_role",
        ),
        Index("ix_okf_sources_user_memory", "user_id", "memory_id"),
    )


class OkfSyncJob(Base):
    """Content-free durable work item for asynchronous OKF reconciliation."""

    __tablename__ = "okf_sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    memory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_okf_jobs_user_idempotency"),
        CheckConstraint(
            "event_type IN ('upsert_memory', 'remove_memory', 'rebuild_user', 'purge_user')",
            name="ck_okf_jobs_event_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'retry_wait', 'completed', 'dead', 'cancelled')",
            name="ck_okf_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_okf_jobs_attempts"),
        CheckConstraint("memory_generation >= 0", name="ck_okf_jobs_memory_generation"),
        CheckConstraint(
            "((event_type IN ('upsert_memory', 'remove_memory') AND memory_id IS NOT NULL) OR "
            "(event_type IN ('rebuild_user', 'purge_user') AND memory_id IS NULL))",
            name="ck_okf_jobs_memory_scope",
        ),
        CheckConstraint("btrim(idempotency_key) <> ''", name="ck_okf_jobs_key_nonblank"),
        CheckConstraint("btrim(policy_version) <> ''", name="ck_okf_jobs_policy_nonblank"),
        Index("ix_okf_jobs_claim", "status", "available_at"),
        Index("ix_okf_jobs_user_status", "user_id", "status"),
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
    local_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    timezone_source: Mapped[str] = mapped_column(String(16), nullable=False, default="device")
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
    local_trigger_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone_source: Mapped[str] = mapped_column(String(16), nullable=False, default="device")
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
