"""Persist normalized local task/reminder time metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_device_aware_task_times"
down_revision: str | None = "0010_conv_log_device_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("local_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("timezone_source", sa.String(length=16), nullable=False, server_default="device"),
    )
    op.alter_column("tasks", "timezone_source", server_default=None)
    op.add_column(
        "reminders", sa.Column("local_trigger_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "reminders",
        sa.Column("timezone_source", sa.String(length=16), nullable=False, server_default="device"),
    )
    op.alter_column("reminders", "timezone_source", server_default=None)


def downgrade() -> None:
    op.drop_column("reminders", "timezone_source")
    op.drop_column("reminders", "local_trigger_at")
    op.drop_column("tasks", "timezone_source")
    op.drop_column("tasks", "local_due_at")
