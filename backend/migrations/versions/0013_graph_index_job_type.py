"""allow durable graph-index memory jobs

Revision ID: 0013_graph_index_job_type
Revises: 0012_graph_rag_foundation
Create Date: 2026-09-16 01:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_graph_index_job_type"
down_revision: str | None = "0012_graph_rag_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_JOB_TYPES = "'extract_turn', 'embed_memory', 'reembed_memory', 'purge_session'"
_NEW_JOB_TYPES = f"{_OLD_JOB_TYPES}, 'index_memory_graph'"


def upgrade() -> None:
    op.drop_constraint("ck_memory_jobs_type", "memory_jobs", type_="check")
    op.create_check_constraint(
        "ck_memory_jobs_type",
        "memory_jobs",
        sa.text(f"job_type IN ({_NEW_JOB_TYPES})"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    graph_jobs = bind.scalar(
        sa.text("SELECT count(*) FROM memory_jobs WHERE job_type = 'index_memory_graph'")
    )
    if graph_jobs:
        raise RuntimeError(
            "cannot downgrade graph job type while index_memory_graph jobs still exist"
        )

    op.drop_constraint("ck_memory_jobs_type", "memory_jobs", type_="check")
    op.create_check_constraint(
        "ck_memory_jobs_type",
        "memory_jobs",
        sa.text(f"job_type IN ({_OLD_JOB_TYPES})"),
    )
