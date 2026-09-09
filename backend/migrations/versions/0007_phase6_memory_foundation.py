"""Add the Phase 6 memory and final-message persistence foundation.

This migration evolves the Phase 2 ``memories`` table in place so existing
rows and IDs survive the Phase 6 rollout. Retrieval and automatic writes are
still application-gated and remain disabled by default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "0007_phase6_memory_foundation"
down_revision: str | None = "0006_user_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "users",
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
    )
    op.add_column(
        "users",
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="en"),
    )
    op.add_column(
        "users",
        sa.Column("memory_version", sa.BigInteger(), nullable=False, server_default="0"),
    )
    for column in ("memory_enabled", "timezone", "locale", "memory_version"):
        op.alter_column("users", column, server_default=None)

    op.create_unique_constraint(
        "uq_conversation_turns_id_user_id",
        "conversation_turns",
        ["id", "user_id"],
    )

    op.drop_index("ix_memories_user_status", table_name="memories")
    op.drop_index("ix_memories_user_created", table_name="memories")
    op.drop_constraint("ck_memories_status", "memories", type_="check")
    op.rename_table("memories", "memory_items")

    op.add_column(
        "memory_items",
        sa.Column("memory_type", sa.String(length=32), nullable=False, server_default="summary"),
    )
    op.add_column("memory_items", sa.Column("subject", sa.String(length=512), nullable=True))
    op.add_column("memory_items", sa.Column("predicate", sa.String(length=128), nullable=True))
    op.add_column("memory_items", sa.Column("object_json", postgresql.JSONB(), nullable=True))
    op.add_column(
        "memory_items",
        sa.Column("occurred_start_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_items",
        sa.Column("occurred_end_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memory_items", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("memory_items", sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "memory_items",
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "memory_items",
        sa.Column("salience", sa.Float(), nullable=False, server_default="0.5"),
    )
    op.add_column(
        "memory_items",
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "memory_items",
        sa.Column("source_turn_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "memory_items",
        sa.Column("source_session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "memory_items",
        sa.Column("source_kind", sa.String(length=32), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "memory_items",
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("memory_items", sa.Column("dedupe_key", sa.String(length=512), nullable=True))
    op.add_column(
        "memory_items",
        sa.Column("extraction_policy_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "memory_items",
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', coalesce(content, ''))", persisted=True),
            nullable=False,
        ),
    )
    op.alter_column("memory_items", "memory_type", server_default=None)
    op.alter_column("memory_items", "confidence", server_default=None)
    op.alter_column("memory_items", "salience", server_default=None)
    op.alter_column("memory_items", "source_kind", server_default=None)

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_json", postgresql.JSONB(), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["turn_id", "user_id"],
            ["conversation_turns.id", "conversation_turns.user_id"],
            name="fk_messages_turn_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_messages_id_user_id"),
        sa.UniqueConstraint(
            "turn_id",
            "role",
            "sequence_no",
            name="uq_messages_turn_role_sequence",
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'tool', 'system')", name="ck_messages_role"
        ),
        sa.CheckConstraint("sequence_no >= 0", name="ck_messages_sequence_no"),
    )
    op.alter_column("messages", "is_final", server_default=None)
    op.alter_column("messages", "sequence_no", server_default=None)
    op.create_index("ix_messages_user_created", "messages", ["user_id", "created_at"])
    op.create_index("ix_messages_turn_sequence", "messages", ["turn_id", "sequence_no"])

    op.create_foreign_key(
        "fk_memory_items_source_message_user",
        "memory_items",
        "messages",
        ["source_message_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_memory_items_source_turn_user",
        "memory_items",
        "conversation_turns",
        ["source_turn_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_memory_items_source_session_user",
        "memory_items",
        "voice_sessions",
        ["source_session_id", "user_id"],
        ["id", "user_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_memory_items_supersedes",
        "memory_items",
        "memory_items",
        ["supersedes_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_memory_items_id_user_id", "memory_items", ["id", "user_id"])
    op.create_check_constraint(
        "ck_memory_items_type",
        "memory_items",
        "memory_type IN ('fact', 'preference', 'event', 'relationship', 'routine', 'project', 'summary')",
    )
    op.create_check_constraint(
        "ck_memory_items_confidence",
        "memory_items",
        "confidence >= 0 AND confidence <= 1",
    )
    op.create_check_constraint(
        "ck_memory_items_salience",
        "memory_items",
        "salience >= 0 AND salience <= 1",
    )
    op.create_check_constraint(
        "ck_memory_items_source_kind",
        "memory_items",
        "source_kind IN ('automatic', 'explicit_tool', 'manual_api', 'legacy')",
    )
    op.create_check_constraint(
        "ck_memory_items_status",
        "memory_items",
        "status IN ('active', 'superseded', 'deleted')",
    )
    op.create_index("ix_memory_items_user_created", "memory_items", ["user_id", "created_at"])
    op.create_index(
        "ix_memory_items_user_status_type",
        "memory_items",
        ["user_id", "status", "memory_type"],
    )
    op.create_index(
        "ix_memory_items_user_occurred",
        "memory_items",
        ["user_id", "occurred_start_at"],
    )
    op.create_index(
        "ix_memory_items_source_message",
        "memory_items",
        ["user_id", "source_message_id"],
    )
    op.create_index(
        "ix_memory_items_search_tsv",
        "memory_items",
        ["search_tsv"],
        postgresql_using="gin",
    )
    op.create_index(
        "uq_memory_items_user_dedupe_active",
        "memory_items",
        ["user_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND dedupe_key IS NOT NULL"),
    )

    op.create_table(
        "memory_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", VECTOR(1024), nullable=True),
        sa.Column(
            "embedding_model", sa.String(length=255), nullable=False, server_default="BAAI/bge-m3"
        ),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', coalesce(content, ''))", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            name="fk_memory_chunks_memory_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("memory_id", "chunk_no", name="uq_memory_chunks_memory_chunk"),
        sa.CheckConstraint("chunk_no >= 0", name="ck_memory_chunks_chunk_no"),
    )
    op.alter_column("memory_chunks", "embedding_model", server_default=None)
    op.create_index("ix_memory_chunks_user_memory", "memory_chunks", ["user_id", "memory_id"])
    op.create_index(
        "ix_memory_chunks_search_tsv",
        "memory_chunks",
        ["search_tsv"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_memory_chunks_embedding_hnsw",
        "memory_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("canonical_name", sa.String(length=512), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_entities_id_user_id"),
        sa.UniqueConstraint(
            "user_id",
            "entity_type",
            "normalized_name",
            name="uq_entities_user_type_name",
        ),
    )
    op.alter_column("entities", "metadata", server_default=None)
    op.create_index("ix_entities_user_name", "entities", ["user_id", "normalized_name"])

    op.create_table(
        "memory_entities",
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            name="fk_memory_entities_memory_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id", "user_id"],
            ["entities.id", "entities.user_id"],
            name="fk_memory_entities_entity_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("memory_id", "entity_id"),
    )
    op.create_index("ix_memory_entities_user", "memory_entities", ["user_id"])

    op.create_table(
        "memory_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_turn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=True),
        sa.Column("model_version", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_message_id", "user_id"],
            ["messages.id", "messages.user_id"],
            name="fk_memory_jobs_source_message_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_turn_id", "user_id"],
            ["conversation_turns.id", "conversation_turns.user_id"],
            name="fk_memory_jobs_source_turn_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_session_id", "user_id"],
            ["voice_sessions.id", "voice_sessions.user_id"],
            name="fk_memory_jobs_source_session_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            name="fk_memory_jobs_memory_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_memory_jobs_user_idempotency"),
        sa.CheckConstraint(
            "job_type IN ('extract_turn', 'embed_memory', 'reembed_memory', 'purge_session')",
            name="ck_memory_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retry_wait', 'completed', 'dead', 'cancelled')",
            name="ck_memory_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_memory_jobs_attempts"),
    )
    for column in ("status", "attempts", "available_at"):
        op.alter_column("memory_jobs", column, server_default=None)
    op.create_index("ix_memory_jobs_claim", "memory_jobs", ["status", "available_at"])
    op.create_index("ix_memory_jobs_user_status", "memory_jobs", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_memory_jobs_user_status", table_name="memory_jobs")
    op.drop_index("ix_memory_jobs_claim", table_name="memory_jobs")
    op.drop_table("memory_jobs")

    op.drop_index("ix_memory_entities_user", table_name="memory_entities")
    op.drop_table("memory_entities")

    op.drop_index("ix_entities_user_name", table_name="entities")
    op.drop_table("entities")

    op.drop_index("ix_memory_chunks_embedding_hnsw", table_name="memory_chunks")
    op.drop_index("ix_memory_chunks_search_tsv", table_name="memory_chunks")
    op.drop_index("ix_memory_chunks_user_memory", table_name="memory_chunks")
    op.drop_table("memory_chunks")

    for index_name in (
        "uq_memory_items_user_dedupe_active",
        "ix_memory_items_search_tsv",
        "ix_memory_items_source_message",
        "ix_memory_items_user_occurred",
        "ix_memory_items_user_status_type",
        "ix_memory_items_user_created",
    ):
        op.drop_index(index_name, table_name="memory_items")
    for constraint_name in (
        "ck_memory_items_status",
        "ck_memory_items_source_kind",
        "ck_memory_items_salience",
        "ck_memory_items_confidence",
        "ck_memory_items_type",
        "uq_memory_items_id_user_id",
        "fk_memory_items_supersedes",
        "fk_memory_items_source_session_user",
        "fk_memory_items_source_turn_user",
        "fk_memory_items_source_message_user",
    ):
        constraint_type = (
            "unique"
            if constraint_name == "uq_memory_items_id_user_id"
            else ("foreignkey" if constraint_name.startswith("fk_") else "check")
        )
        op.drop_constraint(constraint_name, "memory_items", type_=constraint_type)
    op.drop_column("memory_items", "search_tsv")
    for column in (
        "extraction_policy_version",
        "dedupe_key",
        "supersedes_id",
        "source_kind",
        "source_session_id",
        "source_turn_id",
        "source_message_id",
        "salience",
        "confidence",
        "valid_to",
        "valid_from",
        "occurred_end_at",
        "occurred_start_at",
        "object_json",
        "predicate",
        "subject",
        "memory_type",
    ):
        op.drop_column("memory_items", column)
    op.rename_table("memory_items", "memories")
    op.create_check_constraint(
        "ck_memories_status",
        "memories",
        "status IN ('active', 'deleted')",
    )
    op.create_index("ix_memories_user_created", "memories", ["user_id", "created_at"])
    op.create_index("ix_memories_user_status", "memories", ["user_id", "status"])

    op.drop_index("ix_messages_turn_sequence", table_name="messages")
    op.drop_index("ix_messages_user_created", table_name="messages")
    op.drop_table("messages")
    op.drop_constraint("uq_conversation_turns_id_user_id", "conversation_turns", type_="unique")

    for column in ("memory_version", "locale", "timezone", "memory_enabled"):
        op.drop_column("users", column)
