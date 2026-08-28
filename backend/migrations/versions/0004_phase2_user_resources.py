"""Add ownership-scoped memory and task resources."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase2_user_resources"
down_revision: Union[str, None] = "0003_voice_gateway"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
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
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_memories_status"),
    )
    op.create_index("ix_memories_user_created", "memories", ["user_id", "created_at"])
    op.create_index("ix_memories_user_status", "memories", ["user_id", "status"])

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="ck_tasks_status",
        ),
    )
    op.create_index("ix_tasks_user_created", "tasks", ["user_id", "created_at"])
    op.create_index("ix_tasks_user_status", "tasks", ["user_id", "status"])
    op.create_index("ix_tasks_user_due", "tasks", ["user_id", "due_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_user_due", table_name="tasks")
    op.drop_index("ix_tasks_user_status", table_name="tasks")
    op.drop_index("ix_tasks_user_created", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_memories_user_status", table_name="memories")
    op.drop_index("ix_memories_user_created", table_name="memories")
    op.drop_table("memories")
