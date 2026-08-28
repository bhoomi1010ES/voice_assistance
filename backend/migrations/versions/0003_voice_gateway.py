"""Create durable voice session and turn metadata tables."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_voice_gateway"
down_revision: Union[str, None] = "0002_auth_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_auth_sessions_id_user_id",
        "auth_sessions",
        ["id", "user_id"],
    )

    op.create_table(
        "voice_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auth_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("client_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_code", sa.Integer(), nullable=True),
        sa.Column("close_reason", sa.String(length=128), nullable=True),
        sa.Column("total_turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_frames", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_voice_sessions_device_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["auth_session_id", "user_id"],
            ["auth_sessions.id", "auth_sessions.user_id"],
            name="fk_voice_sessions_auth_session_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_voice_sessions_id_user_id"),
        sa.CheckConstraint(
            "status IN ('active', 'disconnected', 'completed', 'timed_out', 'failed')",
            name="ck_voice_sessions_status",
        ),
    )
    op.create_index(
        "ix_voice_sessions_user_started",
        "voice_sessions",
        ["user_id", "started_at"],
    )
    op.create_index(
        "ix_voice_sessions_device_started",
        "voice_sessions",
        ["device_id", "started_at"],
    )
    op.create_index(
        "ix_voice_sessions_status_activity",
        "voice_sessions",
        ["status", "last_activity_at"],
    )

    op.create_table(
        "conversation_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("response_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declared_duration_ms", sa.Integer(), nullable=True),
        sa.Column("observed_duration_ms", sa.Integer(), nullable=True),
        sa.Column("first_sequence", sa.Integer(), nullable=True),
        sa.Column("last_sequence", sa.Integer(), nullable=True),
        sa.Column("frame_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("byte_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["voice_sessions.id", "voice_sessions.user_id"],
            name="fk_conversation_turns_session_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "turn_number", name="uq_conversation_turns_session_number"),
        sa.CheckConstraint(
            "status IN ('active', 'committed', 'cancelled', 'disconnected', 'timed_out', 'failed')",
            name="ck_conversation_turns_status",
        ),
    )
    op.create_index(
        "ix_conversation_turns_user_started",
        "conversation_turns",
        ["user_id", "started_at"],
    )
    op.create_index(
        "ix_conversation_turns_session_number",
        "conversation_turns",
        ["session_id", "turn_number"],
    )
    op.create_index(
        "ix_conversation_turns_response_id",
        "conversation_turns",
        ["response_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_turns_response_id", table_name="conversation_turns")
    op.drop_index("ix_conversation_turns_session_number", table_name="conversation_turns")
    op.drop_index("ix_conversation_turns_user_started", table_name="conversation_turns")
    op.drop_table("conversation_turns")

    op.drop_index("ix_voice_sessions_status_activity", table_name="voice_sessions")
    op.drop_index("ix_voice_sessions_device_started", table_name="voice_sessions")
    op.drop_index("ix_voice_sessions_user_started", table_name="voice_sessions")
    op.drop_table("voice_sessions")

    op.drop_constraint("uq_auth_sessions_id_user_id", "auth_sessions", type_="unique")
