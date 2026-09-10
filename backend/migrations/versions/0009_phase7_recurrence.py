"""Add bounded durable recurrence occurrence accounting."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_phase7_recurrence"
down_revision: str | None = "0008_phase7_tasks_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("reminders", "occurrence_count", server_default=None)
    op.create_check_constraint(
        "ck_reminders_occurrence_count",
        "reminders",
        "occurrence_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_reminders_occurrence_count", "reminders", type_="check")
    op.drop_column("reminders", "occurrence_count")
