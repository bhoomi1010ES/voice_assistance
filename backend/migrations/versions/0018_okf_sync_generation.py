"""Fence OKF sync work by the owner's memory generation.

Revision ID: 0018_okf_sync_generation
Revises: 0017_okf_configuration_schema
Create Date: 2026-09-29 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_okf_sync_generation"
down_revision: str | None = "0017_okf_configuration_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "okf_sync_jobs",
        sa.Column(
            "memory_generation", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_check_constraint(
        "ck_okf_jobs_memory_generation", "okf_sync_jobs", "memory_generation >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_okf_jobs_memory_generation", "okf_sync_jobs", type_="check")
    op.drop_column("okf_sync_jobs", "memory_generation")
