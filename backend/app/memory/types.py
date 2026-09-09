from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryType(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    EVENT = "event"
    RELATIONSHIP = "relationship"
    ROUTINE = "routine"
    PROJECT = "project"
    SUMMARY = "summary"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class MemorySourceKind(StrEnum):
    AUTOMATIC = "automatic"
    EXPLICIT_TOOL = "explicit_tool"
    MANUAL_API = "manual_api"
    LEGACY = "legacy"


class MemoryJobType(StrEnum):
    EXTRACT_TURN = "extract_turn"
    EMBED_MEMORY = "embed_memory"
    REEMBED_MEMORY = "reembed_memory"
    PURGE_SESSION = "purge_session"


class MemoryJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    DEAD = "dead"
    CANCELLED = "cancelled"


class MemoryIntent(StrEnum):
    GENERAL = "general"
    LATEST = "latest"
    OLDEST = "oldest"
    TIME_RANGE = "time_range"
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"
    FACT = "fact"


class MemoryQueryPlan(BaseModel):
    """Bounded, provider-neutral representation of a memory request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_query: str = Field(min_length=1, max_length=2_000)
    intent: MemoryIntent = MemoryIntent.GENERAL
    search_terms: tuple[str, ...] = ()
    memory_types: tuple[MemoryType, ...] = ()
    subject: str | None = Field(default=None, max_length=512)
    predicate: str | None = Field(default=None, max_length=128)
    start_at: datetime | None = None
    end_at: datetime | None = None
    limit: int = Field(default=8, ge=1, le=32)

    @field_validator("normalized_query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: uuid.UUID
    user_id: uuid.UUID
    content: str = Field(min_length=1, max_length=100_000)
    source: Literal["structured", "fts", "dense"]
    source_rank: int = Field(ge=1)
    score: float = 0.0
    created_at: datetime
    memory_type: MemoryType
    subject: str | None = None


class FusedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: uuid.UUID
    user_id: uuid.UUID
    content: str = Field(min_length=1, max_length=100_000)
    score: float = 0.0
    rank: int = Field(ge=1)
    sources: tuple[str, ...] = ()
    created_at: datetime
    memory_type: MemoryType


class MemoryRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["disabled", "ready", "degraded"]
    plan: MemoryQueryPlan
    memories: tuple[FusedMemory, ...] = ()
    provider_error: str | None = None


def build_memory_query_plan(
    query: str,
    *,
    now: datetime | None = None,
    limit: int = 8,
) -> MemoryQueryPlan:
    """Create a deterministic plan without sending the query to an LLM."""

    normalized = " ".join(query.split())
    if not normalized:
        raise ValueError("memory query must not be blank")
    if len(normalized) > 2_000:
        raise ValueError("memory query is too long")
    current = now or datetime.now(UTC)
    lowered = normalized.casefold()
    intent = MemoryIntent.GENERAL
    memory_types: tuple[MemoryType, ...] = ()
    if any(token in lowered for token in ("latest", "most recent", "last thing", "recent")):
        intent = MemoryIntent.LATEST
    elif any(token in lowered for token in ("oldest", "first thing", "earliest")):
        intent = MemoryIntent.OLDEST
    if any(token in lowered for token in ("prefer", "preference", "favorite", "favourite", "like")):
        intent = MemoryIntent.PREFERENCE
        memory_types = (MemoryType.PREFERENCE,)
    elif any(token in lowered for token in ("relationship", "friend", "family", "colleague")):
        intent = MemoryIntent.RELATIONSHIP
        memory_types = (MemoryType.RELATIONSHIP,)
    elif any(token in lowered for token in ("remember", "know", "fact", "when did")):
        intent = MemoryIntent.FACT if intent == MemoryIntent.GENERAL else intent

    start_at: datetime | None = None
    end_at: datetime | None = None
    if "today" in lowered:
        start_at = datetime.combine(current.date(), time.min, tzinfo=current.tzinfo or UTC)
        end_at = start_at + timedelta(days=1)
        intent = MemoryIntent.TIME_RANGE
    elif "yesterday" in lowered:
        end_at = datetime.combine(current.date(), time.min, tzinfo=current.tzinfo or UTC)
        start_at = end_at - timedelta(days=1)
        intent = MemoryIntent.TIME_RANGE

    terms = tuple(dict.fromkeys(word for word in normalized.split() if len(word) > 1))
    return MemoryQueryPlan(
        normalized_query=normalized,
        intent=intent,
        search_terms=terms[:32],
        memory_types=memory_types,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )


def normalize_memory_text(value: str) -> str:
    """Normalize user text for deterministic keys without altering stored text."""

    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.casefold().split())


def stable_dedupe_key(
    *,
    memory_type: MemoryType | str,
    subject: str | None,
    predicate: str | None,
    object_json: dict[str, Any] | None,
    content: str,
    time_bucket: str | None = None,
) -> str:
    """Return a stable, non-secret exact-dedupe key for one memory candidate."""

    payload = {
        "memory_type": str(memory_type),
        "subject": normalize_memory_text(subject or ""),
        "predicate": normalize_memory_text(predicate or ""),
        "object": object_json or {},
        "content": normalize_memory_text(content),
        "time_bucket": normalize_memory_text(time_bucket or ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
