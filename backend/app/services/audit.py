from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "authorization",
    "secret",
    "credential",
    "api_key",
    "private_key",
    "client_key",
    "hash",
)


def safe_tool_payload(value: Any) -> Any:
    """Redact credential-like tool fields before durable audit storage."""

    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS)
            else safe_tool_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_tool_payload(item) for item in value]
    if isinstance(value, str):
        return value[:10_000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def safe_tool_result_content(content: str) -> dict[str, Any] | str:
    try:
        return safe_tool_payload(json.loads(content))
    except (TypeError, ValueError):
        return safe_tool_payload(content)


def _safe_metadata_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_metadata_value(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_metadata_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _request_metadata(request: Request | WebSocket | None) -> dict[str, str | None]:
    if request is None:
        return {"request_id": None, "client_ip": None, "user_agent": None}
    headers = request.headers
    client = request.client
    return {
        "request_id": headers.get("x-request-id"),
        "client_ip": client.host if client else None,
        "user_agent": headers.get("user-agent"),
    }


def record_audit(
    session: AsyncSession,
    event_type: str,
    *,
    user_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    metadata: Mapping[str, Any] | None = None,
    request: Request | WebSocket | None = None,
) -> AuditLog:
    """Add a structured security event without accepting secret fields."""

    request_metadata = _request_metadata(request)
    event = AuditLog(
        user_id=user_id,
        device_id=device_id,
        event_type=event_type,
        audit_metadata=_safe_metadata_value(metadata or {}),
        request_id=request_metadata["request_id"],
        client_ip=request_metadata["client_ip"],
        user_agent=request_metadata["user_agent"],
    )
    session.add(event)
    return event
