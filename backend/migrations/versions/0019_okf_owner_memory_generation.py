"""Separate the OKF privacy fence from the public memory version.

Revision ID: 0019_okf_owner_memory_generation
Revises: 0018_okf_sync_generation
Create Date: 2026-09-29 16:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0019_okf_owner_memory_generation"
down_revision: str | None = "0018_okf_sync_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("memory_generation", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    # Existing queued OKF jobs were stamped from memory_version. Preserve their
    # generation comparisons while moving future privacy fences to a dedicated counter.
    op.execute("UPDATE users SET memory_generation = memory_version")
    op.create_check_constraint(
        "ck_users_memory_generation", "users", "memory_generation >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_memory_generation", "users", type_="check")
    op.drop_column("users", "memory_generation")
