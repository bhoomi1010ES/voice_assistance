from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(StrictSchema):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(StrictSchema):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    device_identifier: str = Field(min_length=1, max_length=255)
    platform: str = Field(min_length=1, max_length=32)
    device_name: str | None = Field(default=None, max_length=255)
    device_metadata: dict[str, Any] | None = None

    @field_validator("device_identifier", "platform")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class RefreshRequest(StrictSchema):
    refresh_token: str = Field(min_length=1, max_length=512)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    status: str
    created_at: datetime
    updated_at: datetime


class DeviceRegisterRequest(StrictSchema):
    device_identifier: str = Field(min_length=1, max_length=255)
    platform: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] | None = None

    @field_validator("device_identifier", "platform")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_identifier: str
    platform: str
    name: str | None
    metadata: dict[str, Any] | None = Field(validation_alias="device_metadata")
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None


class AuthSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class TokenResponse(StrictSchema):
    token_type: str
    access_token: str
    refresh_token: str
    expires_in: int
    user: UserResponse
    device: DeviceResponse
    session: AuthSessionResponse


class LogoutResponse(StrictSchema):
    status: str
