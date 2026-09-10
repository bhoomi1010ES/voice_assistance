"""Complete Phase 7 task contract and add durable reminder delivery state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_phase7_tasks_reminders"
down_revision: str | None = "0007_phase6_memory_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_execution_records",
        sa.Column("arguments_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
    )
    op.add_column(
        "tasks",
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
    )
    op.add_column(
        "tasks", sa.Column("source_turn_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("tasks", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("tasks", "priority", server_default=None)
    op.alter_column("tasks", "timezone", server_default=None)
    op.create_foreign_key(
        "fk_tasks_source_turn_user",
        "tasks",
        "conversation_turns",
        ["source_turn_id", "user_id"],
        ["id", "user_id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_tasks_id_user_id", "tasks", ["id", "user_id"])
    op.create_index("ix_tasks_user_status_due", "tasks", ["user_id", "status", "due_at"])
    op.create_check_constraint(
        "ck_tasks_priority",
        "tasks",
        "priority IN ('low', 'normal', 'high', 'urgent')",
    )

    op.add_column("devices", sa.Column("push_token", sa.String(length=4096), nullable=True))
    op.add_column(
        "devices",
        sa.Column("push_token_revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_devices_push_token_active",
        "devices",
        ["push_token"],
        unique=True,
        postgresql_where=sa.text("push_token IS NOT NULL AND push_token_revoked_at IS NULL"),
    )

    op.create_table(
        "reminders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("recurrence_rule", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("delivery_channel", sa.String(length=32), nullable=False, server_default="push"),
        sa.Column("delivery_id", sa.String(length=255), nullable=False),
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
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_reason", sa.String(length=512), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["tasks.id", "tasks.user_id"],
            name="fk_reminders_task_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_reminders_id_user_id"),
        sa.UniqueConstraint("delivery_id", name="uq_reminders_delivery_id"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'processing', 'retry_wait', 'sent', 'failed', 'cancelled')",
            name="ck_reminders_status",
        ),
        sa.CheckConstraint("delivery_channel IN ('push')", name="ck_reminders_delivery_channel"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_reminders_attempt_count"),
    )
    for column in ("status", "delivery_channel", "attempt_count"):
        op.alter_column("reminders", column, server_default=None)
    op.create_index("ix_reminders_user_created", "reminders", ["user_id", "created_at"])
    op.create_index(
        "ix_reminders_user_status_trigger",
        "reminders",
        ["user_id", "status", "trigger_at"],
    )
    op.create_index(
        "ix_reminders_claim",
        "reminders",
        ["status", "trigger_at", "next_attempt_at"],
    )
    op.create_index("ix_reminders_lease", "reminders", ["status", "lease_expires_at"])
    op.create_index("ix_reminders_user_task", "reminders", ["user_id", "task_id"])


def downgrade() -> None:
    op.drop_index("ix_reminders_user_task", table_name="reminders")
    op.drop_index("ix_reminders_lease", table_name="reminders")
    op.drop_index("ix_reminders_claim", table_name="reminders")
    op.drop_index("ix_reminders_user_status_trigger", table_name="reminders")
    op.drop_index("ix_reminders_user_created", table_name="reminders")
    op.drop_table("reminders")

    op.drop_column("tool_execution_records", "arguments_json")

    op.drop_index("ix_devices_push_token_active", table_name="devices")
    op.drop_column("devices", "push_token_revoked_at")
    op.drop_column("devices", "push_token")

    op.drop_constraint("ck_tasks_priority", "tasks", type_="check")
    op.drop_index("ix_tasks_user_status_due", table_name="tasks")
    op.drop_constraint("uq_tasks_id_user_id", "tasks", type_="unique")
    op.drop_constraint("fk_tasks_source_turn_user", "tasks", type_="foreignkey")
    op.drop_column("tasks", "completed_at")
    op.drop_column("tasks", "source_turn_id")
    op.drop_column("tasks", "timezone")
    op.drop_column("tasks", "priority")
