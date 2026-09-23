"""enforce one owner-scoped self entity

Revision ID: 0016_one_self_entity_per_owner
Revises: 0015_graph_contract_constraints
Create Date: 2026-09-22 18:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_one_self_entity_per_owner"
down_revision: str | None = "0015_graph_contract_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate_owners = bind.scalar(
        sa.text(
            """
            SELECT count(*)
            FROM (
                SELECT user_id
                FROM entities
                WHERE entity_type = 'self'
                GROUP BY user_id
                HAVING count(*) > 1
            ) AS duplicate_owners
            """
        )
    )
    if duplicate_owners:
        raise RuntimeError(
            "cannot enforce one self entity per owner while duplicate self rows exist: "
            f"{duplicate_owners} owner(s)"
        )
    op.create_index(
        "uq_entities_user_self",
        "entities",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("entity_type = 'self'"),
    )


def downgrade() -> None:
    op.drop_index("uq_entities_user_self", table_name="entities")

