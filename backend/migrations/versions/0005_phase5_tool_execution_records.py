"""Add durable Phase 5 tool execution idempotency records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase5_tool_exec"
down_revision: str | None = "0004_phase2_user_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_execution_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("tool_call_id", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="started"),
        sa.Column("result_content", sa.Text(), nullable=True),
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
        sa.UniqueConstraint(
            "user_id",
            "turn_id",
            "tool_name",
            "tool_call_id",
            name="uq_tool_execution_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('started', 'completed')",
            name="ck_tool_execution_status",
        ),
    )
    op.create_index(
        "ix_tool_execution_user_created",
        "tool_execution_records",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_execution_user_created", table_name="tool_execution_records")
    op.drop_table("tool_execution_records")
