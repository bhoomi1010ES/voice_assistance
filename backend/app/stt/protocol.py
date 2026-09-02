"""Typed line-framed protocol for the local Windows STT worker."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

WorkerRequestType = Literal["START_TURN", "AUDIO", "COMMIT", "CANCEL", "SHUTDOWN"]
WorkerResponseType = Literal[
    "READY",
    "TURN_READY",
    "PARTIAL",
    "FINAL",
    "ERROR",
    "CANCELLED",
    "SHUTDOWN_ACK",
]


class WorkerProtocolError(ValueError):
    """Raised when a worker frame cannot be safely correlated or decoded."""


@dataclass(frozen=True)
class WorkerRequest:
    type: WorkerRequestType
    session_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    response_id: uuid.UUID | None = None
    generation: int | None = None
    language: str | None = None
    audio: bytes | None = None

    def to_line(self) -> bytes:
        payload: dict[str, Any] = {"type": self.type}
        for name, value in (
            ("session_id", self.session_id),
            ("turn_id", self.turn_id),
            ("response_id", self.response_id),
            ("generation", self.generation),
            ("language", self.language),
        ):
            if value is not None:
                payload[name] = str(value) if isinstance(value, uuid.UUID) else value
        if self.audio is not None:
            payload["audio_base64"] = base64.b64encode(self.audio).decode("ascii")
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"


@dataclass(frozen=True)
class WorkerResponse:
    type: WorkerResponseType
    session_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    response_id: uuid.UUID | None = None
    generation: int | None = None
    text: str | None = None
    language: str | None = None
    confidence: float | None = None
    audio_duration_ms: int | None = None
    timestamp_ms: int | None = None
    engine: str | None = None
    runtime: str | None = None
    recognizer_name: str | None = None
    languages: tuple[str, ...] = ()
    available: bool | None = None
    code: str | None = None
    message: str | None = None


def parse_response(line: bytes | str) -> WorkerResponse:
    try:
        raw = json.loads(line.decode("utf-8") if isinstance(line, bytes) else line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkerProtocolError("malformed worker JSON") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        raise WorkerProtocolError("worker response must contain a type")
    response_type = raw["type"]
    allowed = {"READY", "TURN_READY", "PARTIAL", "FINAL", "ERROR", "CANCELLED", "SHUTDOWN_ACK"}
    if response_type not in allowed:
        raise WorkerProtocolError(f"unknown worker response type: {response_type}")

    def optional_uuid(name: str) -> uuid.UUID | None:
        value = raw.get(name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise WorkerProtocolError(f"worker field {name} must be a UUID string")
        try:
            return uuid.UUID(value)
        except ValueError as error:
            raise WorkerProtocolError(f"worker field {name} is not a UUID") from error

    generation = raw.get("generation")
    if generation is not None and (isinstance(generation, bool) or not isinstance(generation, int)):
        raise WorkerProtocolError("worker generation must be an integer")

    languages = raw.get("languages", [])
    if not isinstance(languages, list) or not all(isinstance(item, str) for item in languages):
        raise WorkerProtocolError("worker languages must be a string list")

    confidence = raw.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, int | float)
    ):
        raise WorkerProtocolError("worker confidence must be numeric")

    return WorkerResponse(
        type=response_type,
        session_id=optional_uuid("session_id"),
        turn_id=optional_uuid("turn_id"),
        response_id=optional_uuid("response_id"),
        generation=generation,
        text=raw.get("text") if isinstance(raw.get("text"), str) else None,
        language=raw.get("language") if isinstance(raw.get("language"), str) else None,
        confidence=float(confidence) if confidence is not None else None,
        audio_duration_ms=(
            int(raw["audio_duration_ms"]) if isinstance(raw.get("audio_duration_ms"), int) else None
        ),
        timestamp_ms=int(raw["timestamp_ms"]) if isinstance(raw.get("timestamp_ms"), int) else None,
        engine=raw.get("engine") if isinstance(raw.get("engine"), str) else None,
        runtime=raw.get("runtime") if isinstance(raw.get("runtime"), str) else None,
        recognizer_name=(
            raw.get("recognizer_name") if isinstance(raw.get("recognizer_name"), str) else None
        ),
        languages=tuple(languages),
        available=raw.get("available") if isinstance(raw.get("available"), bool) else None,
        code=raw.get("code") if isinstance(raw.get("code"), str) else None,
        message=raw.get("message") if isinstance(raw.get("message"), str) else None,
    )


def decode_audio(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise WorkerProtocolError("worker audio is not valid base64") from error
