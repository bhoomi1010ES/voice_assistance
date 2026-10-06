from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    goal: str | None = Field(default=None, max_length=10_000)
    deadline_at: datetime | None = None
    timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("invalid IANA timezone") from error
        return value

    @field_validator("deadline_at")
    @classmethod
    def aware_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("deadline must include a UTC offset")
        return value


class PlanUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    goal: str | None = Field(default=None, max_length=10_000)
    status: Literal["active", "completed", "archived"] | None = None
    deadline_at: datetime | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("name", "timezone", "status")
    @classmethod
    def nonnull_fields(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("field cannot be null")
        return value

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return PlanCreateRequest.nonblank(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return PlanCreateRequest.valid_timezone(value)

    @field_validator("deadline_at")
    @classmethod
    def aware_deadline(cls, value: datetime | None) -> datetime | None:
        return PlanCreateRequest.aware_deadline(value)


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    goal: str | None
    status: str
    deadline_at: datetime | None
    timezone: str
    revision: int
    created_at: datetime
    updated_at: datetime


class PlanContextResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    content: str
    revision: int


class PlanningReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    batch_id: uuid.UUID
    ordinal: int
    action_type: str
    disposition: str
    status: str
    reason: str | None
    target_id: uuid.UUID | None
    target_revision: int | None
    result_json: dict | None
    created_at: datetime
