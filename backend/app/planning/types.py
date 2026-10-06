"""Bounded model proposals. All identity and dispositions are server supplied."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.planning.policy import PlanningConsent

EXTRACTOR_VERSION = "plan-extract-v9"
MAX_TRANSCRIPT_CHARS = 8192
MAX_OUTPUT_CHARS = 16384


class Span(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    start: int = Field(ge=0, le=MAX_TRANSCRIPT_CHARS)
    length: int = Field(ge=1, le=MAX_TRANSCRIPT_CHARS)
    text: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARS)


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    operation: str = Field(min_length=1, max_length=64)
    classification: Literal[
        "INFORMATION",
        "REFERENCE",
        "INTENTION",
        "COMMITMENT",
        "DEADLINE",
        "REMINDER",
        "PROJECT",
        "CORRECTION",
        "PROGRESS",
        "NEGATIVE",
        "QUESTION",
        "HYPOTHETICAL",
    ]
    title: str = Field(min_length=1, max_length=255)
    actor: Literal["user", "team", "third_party", "context"]
    source: Span
    temporal: Span | None = None
    recurrence: str | None = Field(default=None, max_length=512)
    recurrence_source: Span | None = None
    plan_mention: str | None = Field(default=None, min_length=1, max_length=255)
    target_mention: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=2048)
    confidence: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    actions: list[Proposal] = Field(max_length=8)
    overflow: bool = False


@dataclass(frozen=True)
class Target:
    id: UUID
    kind: str
    title: str
    plan_id: UUID | None
    revision: int
    scheduled_at: datetime | None = None
    recurrence_rule: str | None = None


@dataclass(frozen=True)
class PlanningSnapshot:
    consent: PlanningConsent
    now_utc: datetime
    timezone: str
    active_plan_id: UUID | None = None
    plans: tuple[Target, ...] = ()
    targets: tuple[Target, ...] = ()
    context: tuple[str, ...] = ()
    complete: bool = True
    recent_receipt: PlanningReceipt | None = None


@dataclass(frozen=True)
class Decision:
    proposal: Proposal
    disposition: str
    reason: str
    plan_id: UUID | None = None
    plan_ordinal: int | None = None
    target_id: UUID | None = None
    target_revision: int | None = None
    scheduled_at: datetime | None = None
    timezone: str | None = None

    def outcome(self, disposition: str, reason: str) -> Decision:
        return replace(self, disposition=disposition, reason=reason)


@dataclass(frozen=True)
class PlanningReceipt:
    batch_id: UUID
    plan_id: UUID | None = None
    plan_name: str | None = None
    saved_actions: tuple[dict[str, Any], ...] = ()
    duplicate_actions: tuple[dict[str, Any], ...] = ()
    pending_confirmations: tuple[dict[str, Any], ...] = ()
    failed_actions: tuple[dict[str, Any], ...] = ()
    clarifications: tuple[dict[str, Any], ...] = ()
    has_changes: bool = False
    text_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": str(self.batch_id),
            "plan_id": str(self.plan_id) if self.plan_id else None,
            "plan_name": self.plan_name,
            "saved_actions": list(self.saved_actions),
            "duplicate_actions": list(self.duplicate_actions),
            "pending_confirmations": list(self.pending_confirmations),
            "failed_actions": list(self.failed_actions),
            "clarifications": list(self.clarifications),
            "has_changes": self.has_changes,
            "text_summary": self.text_summary,
        }

