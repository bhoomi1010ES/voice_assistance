"""Owned planning storage. PM-1 stores controls only, never inferred actions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.resources import utc_now


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    __mapper_args__ = {"version_id_col": revision}
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["source_session_id", "user_id"],
            ["voice_sessions.id", "voice_sessions.user_id"],
            ondelete="SET NULL (source_session_id)",
        ),
        ForeignKeyConstraint(
            ["source_turn_id", "user_id"],
            ["conversation_turns.id", "conversation_turns.user_id"],
            ondelete="SET NULL (source_turn_id)",
        ),
        UniqueConstraint("id", "user_id", name="uq_plans_id_user_id"),
        CheckConstraint("status IN ('active', 'completed', 'archived')", name="ck_plans_status"),
        CheckConstraint("revision > 0", name="ck_plans_revision"),
        Index("ix_plans_user_created", "user_id", "created_at"),
    )


class PlanContextItem(Base):
    __tablename__ = "plan_context_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="note", server_default="note")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_span_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    __mapper_args__ = {"version_id_col": revision}
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["plan_id", "user_id"], ["plans.id", "plans.user_id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["source_turn_id", "user_id"],
            ["conversation_turns.id", "conversation_turns.user_id"],
            ondelete="SET NULL (source_turn_id)",
        ),
        UniqueConstraint("id", "user_id", name="uq_plan_context_id_user"),
        UniqueConstraint("plan_id", "user_id", "dedupe_key", name="uq_plan_context_dedupe"),
        CheckConstraint("revision > 0", name="ck_plan_context_revision"),
        CheckConstraint("status IN ('active', 'deleted')", name="ck_plan_context_status"),
    )


class PlanningSession(Base):
    __tablename__ = "planning_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="normal", server_default="normal")
    active_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    state_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    policy_version: Mapped[str] = mapped_column(
        String(64), default="plan-v1", server_default="plan-v1"
    )
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["voice_sessions.id", "voice_sessions.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["active_plan_id", "user_id"],
            ["plans.id", "plans.user_id"],
            ondelete="SET NULL (active_plan_id)",
        ),
        UniqueConstraint("session_id", "user_id", name="uq_planning_sessions_session_user"),
        CheckConstraint("mode IN ('normal', 'plan')", name="ck_planning_sessions_mode"),
        CheckConstraint("state_version > 0", name="ck_planning_sessions_version"),
    )


class PlanningBatch(Base):
    __tablename__ = "planning_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["planning_sessions.session_id", "planning_sessions.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["turn_id", "session_id", "user_id"],
            [
                "conversation_turns.id",
                "conversation_turns.session_id",
                "conversation_turns.user_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "user_id", name="uq_planning_batches_id_user"),
        UniqueConstraint("turn_id", "user_id", name="uq_planning_batches_turn_user"),
        CheckConstraint(
            "status IN ('pending', 'validated', 'completed', 'failed', 'cancelled')",
            name="ck_planning_batches_status",
        ),
        CheckConstraint(
            "state_version > 0 AND retry_count >= 0", name="ck_planning_batches_versions"
        ),
    )


class PlanningAction(Base):
    __tablename__ = "planning_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_spans_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_revision: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1", nullable=False
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["batch_id", "user_id"],
            ["planning_batches.id", "planning_batches.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["plan_id", "user_id"], ["plans.id", "plans.user_id"], ondelete="SET NULL (plan_id)"
        ),
        UniqueConstraint("id", "user_id", name="uq_planning_actions_id_user"),
        UniqueConstraint("batch_id", "ordinal", name="uq_planning_actions_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_planning_actions_ordinal"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_planning_actions_confidence"
        ),
        CheckConstraint(
            "target_revision IS NULL OR target_revision > 0",
            name="ck_planning_actions_target_revision",
        ),
        CheckConstraint(
            "disposition IN ('AUTO', 'CONFIRM', 'CLARIFY', 'NO_ACTION', 'DENY')",
            name="ck_planning_actions_disposition",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'cancelled', 'duplicate', 'skipped')",
            name="ck_planning_actions_status",
        ),
        Index("ix_planning_actions_user_plan_created", "user_id", "plan_id", "created_at"),
    )
