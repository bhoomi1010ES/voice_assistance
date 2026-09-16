"""graph rag foundation

Revision ID: 0012_graph_rag_foundation
Revises: 0011_device_aware_task_times
Create Date: 2026-09-15 16:50:02.927526

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_graph_rag_foundation"
down_revision: str | None = "0011_device_aware_task_times"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("source_memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["entity_id", "user_id"],
            ["entities.id", "entities.user_id"],
            name="fk_entity_aliases_entity_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            name="fk_entity_aliases_source_memory_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "entity_id",
            "normalized_alias",
            name="uq_entity_aliases_user_entity_normalized",
        ),
        sa.CheckConstraint(
            "source_kind IN ('canonical', 'memory', 'manual', 'legacy')",
            name="ck_entity_aliases_source_kind",
        ),
    )
    op.create_index(
        "ix_entity_aliases_user_normalized",
        "entity_aliases",
        ["user_id", "normalized_alias"],
    )
    op.create_index(
        "ix_entity_aliases_user_entity",
        "entity_aliases",
        ["user_id", "entity_id"],
    )

    op.create_table(
        "entity_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column("target_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confidence", postgresql.REAL(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("extraction_policy_version", sa.String(length=64), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["source_entity_id", "user_id"],
            ["entities.id", "entities.user_id"],
            name="fk_entity_relationships_source_entity_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_entity_id", "user_id"],
            ["entities.id", "entities.user_id"],
            name="fk_entity_relationships_target_entity_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            name="fk_entity_relationships_source_memory_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source_entity_id",
            "relationship_type",
            "target_entity_id",
            "source_memory_id",
            name="uq_entity_relationships_evidence",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded')",
            name="ck_entity_relationships_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_entity_relationships_confidence",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_entity_relationships_validity",
        ),
        sa.CheckConstraint(
            "source_entity_id <> target_entity_id",
            name="ck_entity_relationships_no_self_edge",
        ),
    )
    op.alter_column("entity_relationships", "status", server_default=None)
    op.create_index(
        "ix_entity_relationships_source_status",
        "entity_relationships",
        ["user_id", "source_entity_id", "status"],
    )
    op.create_index(
        "ix_entity_relationships_target_status",
        "entity_relationships",
        ["user_id", "target_entity_id", "status"],
    )
    op.create_index(
        "ix_entity_relationships_type_status",
        "entity_relationships",
        ["user_id", "relationship_type", "status"],
    )
    op.create_index(
        "ix_entity_relationships_user_memory",
        "entity_relationships",
        ["user_id", "source_memory_id"],
    )


def downgrade() -> None:
    op.drop_table("entity_relationships")
    op.drop_table("entity_aliases")
