from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class VoiceSession(Base):
    __tablename__ = "voice_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    auth_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    protocol_version: Mapped[int] = mapped_column(Integer, nullable=False)
    client_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    total_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            ondelete="CASCADE",
            name="fk_voice_sessions_device_user",
        ),
        ForeignKeyConstraint(
            ["auth_session_id", "user_id"],
            ["auth_sessions.id", "auth_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_voice_sessions_auth_session_user",
        ),
        UniqueConstraint("id", "user_id", name="uq_voice_sessions_id_user_id"),
        Index("ix_voice_sessions_user_started", "user_id", "started_at"),
        Index("ix_voice_sessions_device_started", "device_id", "started_at"),
        Index("ix_voice_sessions_status_activity", "status", "last_activity_at"),
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    response_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    declared_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["voice_sessions.id", "voice_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_conversation_turns_session_user",
        ),
        UniqueConstraint("session_id", "turn_number", name="uq_conversation_turns_session_number"),
        Index("ix_conversation_turns_user_started", "user_id", "started_at"),
        Index("ix_conversation_turns_session_number", "session_id", "turn_number"),
        Index("ix_conversation_turns_response_id", "response_id"),
    )
