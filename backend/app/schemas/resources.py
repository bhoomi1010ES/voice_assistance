from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.auth import StrictSchema


class MemoryCreateRequest(StrictSchema):
    content: str = Field(min_length=1, max_length=100_000)
    metadata: dict[str, Any] | None = None
    memory_type: Literal[
        "fact", "preference", "event", "relationship", "routine", "project", "summary"
    ] = "fact"
    subject: str | None = Field(default=None, max_length=512)
    predicate: str | None = Field(default=None, max_length=128)
    object_json: dict[str, Any] | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    salience: float = Field(default=0.8, ge=0, le=1)


class MemoryUpdateRequest(StrictSchema):
    content: str | None = Field(default=None, min_length=1, max_length=100_000)
    metadata: dict[str, Any] | None = None
    memory_type: (
        Literal["fact", "preference", "event", "relationship", "routine", "project", "summary"]
        | None
    ) = None
    subject: str | None = Field(default=None, max_length=512)
    predicate: str | None = Field(default=None, max_length=128)
    object_json: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    salience: float | None = Field(default=None, ge=0, le=1)


class MemorySearchRequest(StrictSchema):
    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=8, ge=1, le=32)


class MemorySettingsUpdateRequest(StrictSchema):
    enabled: bool | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    locale: str | None = Field(default=None, min_length=2, max_length=16)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value


class MemorySettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = Field(validation_alias="memory_enabled")
    timezone: str
    locale: str
    version: int = Field(validation_alias="memory_version")


class MemoryDeleteAllRequest(StrictSchema):
    confirmation: Literal["DELETE_ALL_MEMORY"]


class MemoryExclusionRequest(StrictSchema):
    excluded: bool


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    content: str
    metadata: dict[str, Any] | None = Field(validation_alias="metadata_json")
    memory_type: str
    subject: str | None
    predicate: str | None
    object_json: dict[str, Any] | None
    confidence: float
    salience: float
    supersedes_id: uuid.UUID | None
    status: str
    created_at: datetime
    updated_at: datetime


TaskStatus = Literal["pending", "in_progress", "completed", "cancelled"]
TaskPriority = Literal["low", "normal", "high", "urgent"]


class TaskCreateRequest(StrictSchema):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=100_000)
    due_at: datetime | None = None
    priority: TaskPriority = "normal"
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("title", "timezone")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value


class TaskUpdateRequest(StrictSchema):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=100_000)
    status: TaskStatus | None = None
    due_at: datetime | None = None
    priority: TaskPriority | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("title", "timezone")
    @classmethod
    def reject_blank_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    status: str
    priority: str
    due_at: datetime | None
    local_due_at: datetime | None
    timezone: str
    timezone_source: str
    source_turn_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


ReminderStatus = Literal["scheduled", "sent", "failed", "cancelled"]


class ReminderCreateRequest(StrictSchema):
    title: str = Field(min_length=1, max_length=255)
    body: str | None = Field(default=None, max_length=100_000)
    trigger_at: datetime
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    recurrence_rule: str | None = Field(default=None, max_length=512)
    task_id: uuid.UUID | None = None
    delivery_channel: Literal["push"] = "push"

    @field_validator("title", "timezone")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value


class ReminderUpdateRequest(StrictSchema):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    body: str | None = Field(default=None, max_length=100_000)
    trigger_at: datetime | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    recurrence_rule: str | None = Field(default=None, max_length=512)
    status: Literal["scheduled", "cancelled"] | None = None
    task_id: uuid.UUID | None = None

    @field_validator("title", "timezone")
    @classmethod
    def reject_blank_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return value


class ReminderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID | None
    title: str
    body: str | None
    trigger_at: datetime
    local_trigger_at: datetime | None
    timezone: str
    timezone_source: str
    recurrence_rule: str | None
    status: str
    delivery_channel: str
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None
    failure_code: str | None = None


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
