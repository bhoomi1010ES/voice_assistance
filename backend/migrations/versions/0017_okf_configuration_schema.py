"""add the opt-in OKF knowledge foundation schema

Revision ID: 0017_okf_configuration_schema
Revises: 0016_one_self_entity_per_owner
Create Date: 2026-09-29 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_okf_configuration_schema"
down_revision: str | None = "0016_one_self_entity_per_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OKF_TABLES = (
    "okf_concept_sources",
    "okf_concept_versions",
    "okf_concept_assertions",
    "okf_concepts",
    "okf_sync_jobs",
)


def upgrade() -> None:
    op.create_table(
        "okf_concepts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_concept_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("concept_type", sa.String(length=32), nullable=False),
        sa.Column("canonical_key", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_okf_concepts_id_user_id"),
        sa.UniqueConstraint("user_id", "canonical_key", name="uq_okf_concepts_user_key"),
        sa.CheckConstraint(
            "concept_type IN ('profile', 'preference', 'project', 'decision', "
            "'relationship', 'fact')",
            name="ck_okf_concepts_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'contested', 'retired')",
            name="ck_okf_concepts_status",
        ),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_okf_concepts_title_nonblank"),
        sa.CheckConstraint(
            "canonical_key ~ '^(profile|preferences|projects|relationships|facts)/"
            "[a-z0-9]+(-[a-z0-9]+)*(/[a-z0-9]+(-[a-z0-9]+)*){0,6}$'",
            name="ck_okf_concepts_canonical_key",
        ),
        sa.CheckConstraint("btrim(policy_version) <> ''", name="ck_okf_concepts_policy_nonblank"),
    )
    # PostgreSQL's column-list SET NULL keeps the non-null owner while
    # detaching children from a deleted parent concept.
    op.execute(
        sa.text(
            "ALTER TABLE okf_concepts ADD CONSTRAINT fk_okf_concepts_parent_user "
            "FOREIGN KEY (parent_concept_id, user_id) "
            "REFERENCES okf_concepts (id, user_id) "
            "ON DELETE SET NULL (parent_concept_id)"
        )
    )
    op.create_index(
        "ix_okf_concepts_user_type_status",
        "okf_concepts",
        ["user_id", "concept_type", "status"],
    )

    op.create_table(
        "okf_concept_assertions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confidence", sa.REAL(), nullable=False, server_default="1.0"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
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
            ["concept_id", "user_id"],
            ["okf_concepts.id", "okf_concepts.user_id"],
            name="fk_okf_assertions_concept_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_okf_assertions_id_user_id"),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'retired')",
            name="ck_okf_assertions_status",
        ),
        sa.CheckConstraint("current_version >= 1", name="ck_okf_assertions_current_version"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_okf_assertions_confidence"
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_okf_assertions_validity",
        ),
        sa.CheckConstraint("btrim(display_text) <> ''", name="ck_okf_assertions_display_nonblank"),
        sa.CheckConstraint("btrim(policy_version) <> ''", name="ck_okf_assertions_policy_nonblank"),
    )
    op.create_index(
        "ix_okf_assertions_user_concept_status",
        "okf_concept_assertions",
        ["user_id", "concept_id", "status"],
    )

    op.create_table(
        "okf_concept_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assertion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_kind", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["concept_id", "user_id"],
            ["okf_concepts.id", "okf_concepts.user_id"],
            name="fk_okf_versions_concept_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assertion_id", "user_id"],
            ["okf_concept_assertions.id", "okf_concept_assertions.user_id"],
            name="fk_okf_versions_assertion_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_okf_versions_id_user_id"),
        sa.UniqueConstraint(
            "user_id", "assertion_id", "version", name="uq_okf_versions_assertion_version"
        ),
        sa.CheckConstraint("version >= 1", name="ck_okf_versions_version"),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'retired')", name="ck_okf_versions_status"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_okf_versions_confidence"
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_okf_versions_validity",
        ),
        sa.CheckConstraint(
            "change_kind IN ('create', 'update', 'supersede', 'retire', 'restore')",
            name="ck_okf_versions_change_kind",
        ),
        sa.CheckConstraint("btrim(display_text) <> ''", name="ck_okf_versions_display_nonblank"),
        sa.CheckConstraint("btrim(policy_version) <> ''", name="ck_okf_versions_policy_nonblank"),
    )

    # This closes the assertion/version cycle without weakening the invariant:
    # both rows can be inserted in one transaction, but commit requires that
    # the materialized assertion points at an immutable matching version.
    op.create_foreign_key(
        "fk_okf_assertions_current_version",
        "okf_concept_assertions",
        "okf_concept_versions",
        ["user_id", "id", "current_version"],
        ["user_id", "assertion_id", "version"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "okf_concept_sources",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_role", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["concept_version_id", "user_id"],
            ["okf_concept_versions.id", "okf_concept_versions.user_id"],
            name="fk_okf_sources_version_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "user_id"],
            ["memory_items.id", "memory_items.user_id"],
            name="fk_okf_sources_memory_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            "concept_version_id",
            "memory_id",
            "evidence_role",
            name="pk_okf_concept_sources",
        ),
        sa.CheckConstraint(
            "evidence_role IN ('supports', 'contradicts', 'supersedes')",
            name="ck_okf_sources_evidence_role",
        ),
    )
    op.create_index(
        "ix_okf_sources_user_memory",
        "okf_concept_sources",
        ["user_id", "memory_id"],
    )

    op.create_table(
        "okf_sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Deliberately not an FK: remove events must survive source deletion.
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
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
        sa.Column("policy_version", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_okf_jobs_user_idempotency"),
        sa.CheckConstraint(
            "event_type IN ('upsert_memory', 'remove_memory', 'rebuild_user', 'purge_user')",
            name="ck_okf_jobs_event_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retry_wait', 'completed', 'dead', 'cancelled')",
            name="ck_okf_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_okf_jobs_attempts"),
        sa.CheckConstraint(
            "((event_type IN ('upsert_memory', 'remove_memory') AND memory_id IS NOT NULL) OR "
            "(event_type IN ('rebuild_user', 'purge_user') AND memory_id IS NULL))",
            name="ck_okf_jobs_memory_scope",
        ),
        sa.CheckConstraint("btrim(idempotency_key) <> ''", name="ck_okf_jobs_key_nonblank"),
        sa.CheckConstraint("btrim(policy_version) <> ''", name="ck_okf_jobs_policy_nonblank"),
    )
    op.create_index("ix_okf_jobs_claim", "okf_sync_jobs", ["status", "available_at"])
    op.create_index("ix_okf_jobs_user_status", "okf_sync_jobs", ["user_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    live_rows = {
        table_name: int(
            bind.scalar(sa.text(f'SELECT count(*) FROM "{table_name}"'))  # noqa: S608
            or 0
        )
        for table_name in _OKF_TABLES
    }
    populated = {name: count for name, count in live_rows.items() if count}
    if populated:
        detail = ", ".join(f"{name}={count}" for name, count in sorted(populated.items()))
        raise RuntimeError(f"cannot downgrade OKF schema while live OKF rows exist: {detail}")

    op.drop_table("okf_sync_jobs")
    op.drop_table("okf_concept_sources")
    op.drop_constraint(
        "fk_okf_assertions_current_version",
        "okf_concept_assertions",
        type_="foreignkey",
    )
    op.drop_table("okf_concept_versions")
    op.drop_table("okf_concept_assertions")
    op.drop_table("okf_concepts")
