from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeDisposition(StrEnum):
    DIRECT_ANSWER = "direct_answer"
    CONTINUE_WITH_EVIDENCE = "continue_with_evidence"
    NO_RESULT = "no_result"
    UNAVAILABLE = "unavailable"
    CONFLICT = "conflict"
    CANCELLED = "cancelled"


class KnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: uuid.UUID
    query: str = Field(min_length=1, max_length=2_000)
    now: datetime
    session_id: uuid.UUID | None = None
    memory_enabled: bool = True
    memory_excluded: bool = False
    cancellation_check: bool = False

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("knowledge query must not be blank")
        return normalized

    @field_validator("now")
    @classmethod
    def normalize_now(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class OkfConceptProposal(BaseModel):
    """Validated structured proposal grounded in one source memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_type: Literal["profile", "preference", "project", "decision", "relationship", "fact"]
    canonical_key: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=512)
    value_json: dict[str, Any]
    display_text: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source_memory_id: uuid.UUID
    policy_version: str = Field(min_length=1, max_length=64)
    supersedes_assertion_id: uuid.UUID | None = None


class OkfEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_id: uuid.UUID
    assertion_id: uuid.UUID
    concept_type: str
    canonical_key: str
    display_text: str
    status: Literal["active", "superseded", "retired"]
    source_memory_ids: tuple[uuid.UUID, ...]
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class OkfQueryPlan(BaseModel):
    """A deterministic, bounded set of canonical-key lookup constraints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_query: str = Field(min_length=1, max_length=2_000)
    intent: Literal["preference", "project", "relationship", "profile", "general"]
    key_terms: tuple[str, ...] = Field(max_length=8)
    concept_types: tuple[
        Literal["profile", "preference", "project", "decision", "relationship", "fact"], ...
    ] = Field(max_length=6)
    project_expansion: bool = False
    limit: int = Field(ge=1, le=100)


class KnowledgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    engine: Literal["okf"]
    status: KnowledgeDisposition
    evidence: tuple[OkfEvidence, ...] = ()
    conflicts: tuple[OkfEvidence, ...] = ()
    degraded_reason: str | None = None
    duration_ms: float = Field(ge=0)


class OkfSyncOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["created", "updated", "contested", "retired", "unchanged", "removed"]
    reason_code: str | None = None
    concept_ids: tuple[uuid.UUID, ...] = ()
    assertion_ids: tuple[uuid.UUID, ...] = ()
    source_memory_id: uuid.UUID | None = None
    policy_version: str
