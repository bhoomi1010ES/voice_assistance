"""scope memory supersession references by owner

Revision ID: 0014_memory_supersession_owner
Revises: 0013_graph_index_job_type
Create Date: 2026-09-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_memory_supersession_owner"
down_revision: str | None = "0013_graph_index_job_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_CONSTRAINT = "fk_memory_items_supersedes"
_NEW_CONSTRAINT = "fk_memory_items_supersedes_user"


def upgrade() -> None:
    bind = op.get_bind()
    cross_owner_rows = bind.scalar(
        sa.text(
            """
            SELECT count(*)
            FROM memory_items AS child
            JOIN memory_items AS parent ON parent.id = child.supersedes_id
            WHERE child.supersedes_id IS NOT NULL
              AND child.user_id <> parent.user_id
            """
        )
    )
    if cross_owner_rows:
        raise RuntimeError(
            "cannot scope memory supersession while cross-owner references exist: "
            f"{cross_owner_rows} row(s)"
        )

    op.drop_constraint(_OLD_CONSTRAINT, "memory_items", type_="foreignkey")
    # PostgreSQL's column-list form preserves the existing delete behavior:
    # deleting an older source clears only supersedes_id while retaining the
    # non-null owner column required by the composite key.
    op.execute(
        sa.text(
            f"""
            ALTER TABLE memory_items
            ADD CONSTRAINT {_NEW_CONSTRAINT}
            FOREIGN KEY (supersedes_id, user_id)
            REFERENCES memory_items (id, user_id)
            ON DELETE SET NULL (supersedes_id)
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint(_NEW_CONSTRAINT, "memory_items", type_="foreignkey")
    op.create_foreign_key(
        _OLD_CONSTRAINT,
        "memory_items",
        "memory_items",
        ["supersedes_id"],
        ["id"],
        ondelete="SET NULL",
    )
