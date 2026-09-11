"""Add explicit physical/synthetic device classification for conversation logging."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_conv_log_device_kind"
down_revision: str | None = "0009_phase7_recurrence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows are deliberately treated as synthetic. They have no
    # trusted registration marker from which physical status can be inferred.
    op.add_column(
        "devices",
        sa.Column("device_kind", sa.String(length=16), nullable=False, server_default="synthetic"),
    )
    op.alter_column("devices", "device_kind", server_default=None)
    op.create_check_constraint(
        "ck_devices_device_kind",
        "devices",
        "device_kind IN ('physical', 'synthetic')",
    )
    op.create_index(
        "ix_devices_user_kind_revoked",
        "devices",
        ["user_id", "device_kind", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_devices_user_kind_revoked", table_name="devices")
    op.drop_constraint("ck_devices_device_kind", "devices", type_="check")
    op.drop_column("devices", "device_kind")
