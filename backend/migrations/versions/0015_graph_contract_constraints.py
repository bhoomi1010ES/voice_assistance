"""formalize graph entity and relationship vocabularies

Revision ID: 0015_graph_contract_constraints
Revises: 0014_memory_supersession_owner
Create Date: 2026-09-22 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_graph_contract_constraints"
down_revision: str | None = "0014_memory_supersession_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENTITY_TYPES = (
    "'self', 'person', 'place', 'organization', 'project', 'product', 'event', 'other'"
)
_RELATIONSHIP_TYPES = (
    "'COLLEAGUE_OF', 'FRIEND_OF', 'FAMILY_OF', 'WORKS_AT', 'WORKS_ON', "
    "'LIVES_IN', 'LOCATED_AT', 'OWNS', 'MEMBER_OF', 'MANAGES', 'REPORTS_TO', "
    "'DEPENDS_ON', 'RELATED_TO', 'RESPONSIBLE_FOR', 'TESTED_BY', 'ASSIGNED_TO', "
    "'BLOCKED_BY', 'DISCUSSED_WITH'"
)


def upgrade() -> None:
    bind = op.get_bind()

    # The pre-graph writer used ``subject`` for every memory-linked entity.
    # Preserve those rows as intentionally untyped graph entities rather than
    # guessing a person/place/etc. during a schema migration.
    op.execute(
        sa.text(
            """
            UPDATE entities
            SET entity_type = 'other'
            WHERE entity_type NOT IN (
                'self', 'person', 'place', 'organization',
                'project', 'product', 'event', 'other'
            )
            """
        )
    )

    unknown_relationships = bind.scalar(
        sa.text(
            f"""
            SELECT count(*)
            FROM entity_relationships
            WHERE relationship_type NOT IN ({_RELATIONSHIP_TYPES})
            """
        )
    )
    if unknown_relationships:
        raise RuntimeError(
            "cannot constrain graph relationships while unsupported relationship types exist: "
            f"{unknown_relationships} row(s)"
        )

    op.create_check_constraint(
        "ck_entities_type",
        "entities",
        sa.text(f"entity_type IN ({_ENTITY_TYPES})"),
    )
    op.create_check_constraint(
        "ck_entity_relationships_type",
        "entity_relationships",
        sa.text(f"relationship_type IN ({_RELATIONSHIP_TYPES})"),
    )


def downgrade() -> None:
    op.drop_constraint("ck_entity_relationships_type", "entity_relationships", type_="check")
    op.drop_constraint("ck_entities_type", "entities", type_="check")
