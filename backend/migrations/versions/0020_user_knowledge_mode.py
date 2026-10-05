"""Add an owner-scoped memory knowledge-engine preference.

Revision ID: 0020_user_knowledge_mode
Revises: 0019_okf_owner_memory_generation
Create Date: 2026-10-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_user_knowledge_mode"
down_revision: str | None = "0019_okf_owner_memory_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "knowledge_mode",
            sa.String(length=16),
            server_default="rag",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_users_knowledge_mode", "users", "knowledge_mode IN ('rag', 'okf')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_knowledge_mode", "users", type_="check")
    op.drop_column("users", "knowledge_mode")
