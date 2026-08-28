from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import StrictSchema


class MemoryCreateRequest(StrictSchema):
    content: str = Field(min_length=1, max_length=100_000)
    metadata: dict[str, Any] | None = None


class MemoryUpdateRequest(StrictSchema):
    content: str | None = Field(default=None, min_length=1, max_length=100_000)
    metadata: dict[str, Any] | None = None


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    content: str
    metadata: dict[str, Any] | None = Field(validation_alias="metadata_json")
    status: str
    created_at: datetime
    updated_at: datetime


TaskStatus = Literal["pending", "in_progress", "completed", "cancelled"]


class TaskCreateRequest(StrictSchema):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=100_000)
    due_at: datetime | None = None


class TaskUpdateRequest(StrictSchema):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=100_000)
    status: TaskStatus | None = None
    due_at: datetime | None = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    status: str
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SessionUpdateRequest(StrictSchema):
    client_metadata: dict[str, Any] | None = None


class VoiceSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    protocol_version: int
    client_metadata: dict[str, Any] | None
    status: str
    started_at: datetime
    last_activity_at: datetime
    ended_at: datetime | None
    close_code: int | None
    close_reason: str | None
    total_turns: int
    total_frames: int
    total_bytes: int
    error_count: int
    created_at: datetime
