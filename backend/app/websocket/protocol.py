from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
)


class ProtocolError(ValueError):
    """Raised when a client message does not conform to protocol v1."""


class ControlMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)


class AudioContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_rate_hz: int = Field(gt=0)
    channels: int = Field(gt=0)
    frame_samples: int = Field(gt=0)
    frame_bytes: int = Field(gt=0)


class SttSessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    language: str | None = Field(default=None, min_length=2, max_length=20)


class DeviceTimeContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_epoch_ms: StrictInt = Field(ge=0)
    timezone_id: StrictStr = Field(min_length=1, max_length=64)
    utc_offset: StrictStr = Field(min_length=1, max_length=16)
    locale: StrictStr = Field(min_length=1, max_length=32)


class SessionStartMessage(ControlMessage):
    type: Literal["client.session.start"]
    protocol_version: int = Field(gt=0)
    audio: AudioContract
    client_metadata: dict[str, Any] = Field(default_factory=dict)
    resume_session_id: uuid.UUID | None = None
    stt: SttSessionConfig | None = None
    device_time_context: DeviceTimeContextPayload | None = None


class TurnStartMessage(ControlMessage):
    type: Literal["client.turn.start"]
    client_turn_id: uuid.UUID | None = None
    device_time_context: DeviceTimeContextPayload | None = None


class AudioCommitMessage(ControlMessage):
    type: Literal["client.audio.commit"]
    last_sequence_no: int = Field(ge=0)
    frame_count: int = Field(gt=0)
    byte_count: int = Field(gt=0)
    duration_ms: int = Field(ge=0)


class ResponseCancelMessage(ControlMessage):
    type: Literal["client.response.cancel"]
    response_id: uuid.UUID
    reason: str = Field(default="client_requested", max_length=128)


class ResponseAbortAllMessage(ControlMessage):
    type: Literal["client.response.abort_all"]
    reason: str = Field(default="abort_all", max_length=128)


class ConversationResetMessage(ControlMessage):
    type: Literal["client.conversation.reset"]


class ResponseRetryMessage(ControlMessage):
    type: Literal["client.response.retry"]
    turn_id: uuid.UUID
    original_response_id: uuid.UUID
    transcript: str = Field(min_length=1, max_length=16_384)


class ConfirmationResolveMessage(ControlMessage):
    type: Literal["client.confirmation.resolve"]
    confirmation_id: uuid.UUID
    tool_call_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "deny"]


class ClientPingMessage(ControlMessage):
    type: Literal["client.ping"]
    client_timestamp_ms: int = Field(ge=0)


class SessionEndMessage(ControlMessage):
    type: Literal["client.session.end"]
    reason: str = Field(default="client_requested", max_length=128)


ControlMessageType = Annotated[
    SessionStartMessage
    | TurnStartMessage
    | AudioCommitMessage
    | ResponseCancelMessage
    | ResponseAbortAllMessage
    | ConversationResetMessage
    | ResponseRetryMessage
    | ConfirmationResolveMessage
    | ClientPingMessage
    | SessionEndMessage,
    Field(discriminator="type"),
]

CONTROL_MESSAGE_ADAPTER = TypeAdapter(ControlMessageType)


def parse_control_message(raw: str, *, max_bytes: int) -> ControlMessageType:
    if len(raw.encode("utf-8")) > max_bytes:
        raise ProtocolError("control_message_too_large")
    try:
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ProtocolError("control_message_must_be_object")
        return CONTROL_MESSAGE_ADAPTER.validate_python(decoded)
    except ProtocolError:
        raise
    except (json.JSONDecodeError, ValidationError, TypeError) as error:
        raise ProtocolError("invalid_control_message") from error


def server_event(
    event_type: str,
    *,
    session_id: uuid.UUID | None = None,
    turn_id: uuid.UUID | None = None,
    response_id: uuid.UUID | None = None,
    **payload: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": event_type,
        "event_id": str(uuid.uuid4()),
        "protocol_version": 1,
        "timestamp_ms": int(time.time() * 1000),
    }
    if session_id is not None:
        event["session_id"] = str(session_id)
    if turn_id is not None:
        event["turn_id"] = str(turn_id)
    if response_id is not None:
        event["response_id"] = str(response_id)
    event.update({key: _json_safe(value) for key, value in payload.items()})
    return event


def _json_safe(value: Any) -> Any:
    """Convert trusted event fields before they reach Starlette's JSON encoder."""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    raise TypeError(f"Unsupported server event value: {type(value).__name__}")
