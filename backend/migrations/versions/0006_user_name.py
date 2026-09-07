"""Add an optional display name to users."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_user_name"
down_revision: str | None = "0005_phase5_tool_exec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "name")
