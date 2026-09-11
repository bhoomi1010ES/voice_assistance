"""Safe, physical-device-only conversation log persistence.

Database message persistence remains the application source of truth. This
module writes a separate human-readable JSONL projection only after the
authenticated device/session ownership checks pass.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConversationTurn, Device, Message, VoiceSession

_LOGGER_LOCK: asyncio.Lock | None = None

TIMING_FIELDS = (
    "turn_started_at",
    "speech_started_at",
    "speech_ended_at",
    "stt_started_at",
    "stt_completed_at",
    "llm_started_at",
    "llm_first_token_at",
    "llm_completed_at",
    "tts_requested_at",
    "tts_first_audio_at",
    "tts_playback_started_at",
    "tts_playback_completed_at",
    "turn_completed_at",
)

LATENCY_FIELDS = (
    "speech_duration",
    "speech_end_to_stt_final",
    "stt_duration",
    "stt_final_to_llm_start",
    "llm_time_to_first_token",
    "llm_total",
    "llm_first_token_to_tts_request",
    "llm_complete_to_tts_request",
    "tts_request_to_first_audio",
    "tts_first_audio_to_playback",
    "tts_playback_duration",
    "speech_end_to_llm_first_token",
    "speech_end_to_tts_first_audio",
    "speech_end_to_tts_playback",
    "turn_total",
)


@dataclass(frozen=True, slots=True)
class TimingPoint:
    """A wall-clock display timestamp paired with a local monotonic instant."""

    wall: datetime
    monotonic: float


def build_timing_payload(
    points: Mapping[str, TimingPoint],
    *,
    tts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the safe JSON timing projection from captured event boundaries.

    Wall-clock values are only for display. Every latency is calculated from
    the paired monotonic values captured at the event boundary, never by
    subtracting serialized UTC timestamps.
    """

    timings = {
        field: _as_utc(points[field].wall) if field in points else None for field in TIMING_FIELDS
    }

    latency_pairs = {
        "speech_duration": ("speech_started_at", "speech_ended_at"),
        "speech_end_to_stt_final": ("speech_ended_at", "stt_completed_at"),
        "stt_duration": ("stt_started_at", "stt_completed_at"),
        "stt_final_to_llm_start": ("stt_completed_at", "llm_started_at"),
        "llm_time_to_first_token": ("llm_started_at", "llm_first_token_at"),
        "llm_total": ("llm_started_at", "llm_completed_at"),
        "llm_first_token_to_tts_request": (
            "llm_first_token_at",
            "tts_requested_at",
        ),
        "llm_complete_to_tts_request": ("llm_completed_at", "tts_requested_at"),
        "tts_request_to_first_audio": ("tts_requested_at", "tts_first_audio_at"),
        "tts_first_audio_to_playback": (
            "tts_first_audio_at",
            "tts_playback_started_at",
        ),
        "tts_playback_duration": (
            "tts_playback_started_at",
            "tts_playback_completed_at",
        ),
        "speech_end_to_llm_first_token": (
            "speech_ended_at",
            "llm_first_token_at",
        ),
        "speech_end_to_tts_first_audio": (
            "speech_ended_at",
            "tts_first_audio_at",
        ),
        "speech_end_to_tts_playback": (
            "speech_ended_at",
            "tts_playback_started_at",
        ),
        "turn_total": ("turn_started_at", "turn_completed_at"),
    }
    latency_ms = {
        field: _monotonic_delta_ms(points, *pair) if field in latency_pairs else None
        for field, pair in latency_pairs.items()
    }
    # Keep the public shape stable even if the metric list is extended later.
    latency_ms = {field: latency_ms.get(field) for field in LATENCY_FIELDS}

    payload: dict[str, Any] = {
        "timings": timings,
        "latency_ms": latency_ms,
    }
    if tts is not None:
        payload["tts"] = dict(tts)
    return payload


def _monotonic_delta_ms(
    points: Mapping[str, TimingPoint], start_name: str, end_name: str
) -> float | None:
    start = points.get(start_name)
    end = points.get(end_name)
    if start is None or end is None:
        return None
    return round(max(0.0, (end.monotonic - start.monotonic) * 1000), 3)


def _logger_lock() -> asyncio.Lock:
    global _LOGGER_LOCK
    if _LOGGER_LOCK is None:
        _LOGGER_LOCK = asyncio.Lock()
    return _LOGGER_LOCK


async def should_persist_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    session_id: uuid.UUID,
    enabled: bool,
) -> bool:
    """Return whether this authenticated voice session may create a log file."""

    if not enabled:
        return False

    ownership = await db.scalar(
        select(Device.id)
        .join(
            VoiceSession,
            (VoiceSession.device_id == Device.id) & (VoiceSession.user_id == Device.user_id),
        )
        .where(
            Device.id == device_id,
            Device.user_id == user_id,
            Device.device_kind == "physical",
            Device.revoked_at.is_(None),
            VoiceSession.id == session_id,
            VoiceSession.user_id == user_id,
            VoiceSession.device_id == device_id,
        )
    )
    return ownership is not None


class ConversationLogger:
    """Persist one idempotent JSONL record per authenticated voice turn."""

    def __init__(self, *, root_dir: str | Path) -> None:
        configured = Path(root_dir)
        self.root_dir = configured if configured.is_absolute() else Path.cwd() / configured

    async def persist_turn(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        enabled: bool,
        status: str | None = None,
        timing_payload: Mapping[str, Any] | None = None,
    ) -> bool:
        """Write or replace one turn record after durable DB commit.

        The file is keyed exclusively by server-owned UUIDs. Replaying this
        method for a turn replaces its existing record instead of appending a
        duplicate.
        """

        if not await should_persist_conversation(
            db,
            user_id=user_id,
            device_id=device_id,
            session_id=session_id,
            enabled=enabled,
        ):
            return False

        turn = await db.scalar(
            select(ConversationTurn)
            .join(
                VoiceSession,
                (VoiceSession.id == ConversationTurn.session_id)
                & (VoiceSession.user_id == ConversationTurn.user_id),
            )
            .where(
                ConversationTurn.id == turn_id,
                ConversationTurn.session_id == session_id,
                ConversationTurn.user_id == user_id,
                VoiceSession.device_id == device_id,
            )
        )
        if turn is None:
            return False

        messages = list(
            (
                await db.scalars(
                    select(Message)
                    .where(Message.turn_id == turn_id, Message.user_id == user_id)
                    .order_by(Message.sequence_no, Message.created_at, Message.id)
                )
            ).all()
        )
        user_messages = [message.content for message in messages if message.role == "user"]
        assistant_messages = [
            message.content for message in messages if message.role == "assistant"
        ]
        tool_messages = [message for message in messages if message.role == "tool"]

        record: dict[str, Any] = {
            "timestamp": _as_utc(turn.committed_at or turn.ended_at or turn.created_at),
            "user_id": str(user_id),
            "device_id": str(device_id),
            "session_id": str(session_id),
            "turn_id": str(turn_id),
            "user_text": "\n".join(text for text in user_messages if text),
            "status": status or _log_status(turn.status),
        }
        if assistant_messages:
            record["assistant_text"] = assistant_messages[-1]
        if tool_messages:
            tool_payload = tool_messages[-1].content_json or {}
            tool_name = tool_payload.get("tool_name")
            if isinstance(tool_name, str) and tool_name:
                record["tool_name"] = tool_name
            tool_success = tool_payload.get("success")
            if isinstance(tool_success, bool):
                record["tool_status"] = "success" if tool_success else "failed"
        if timing_payload is not None:
            record.update(
                {
                    "timings": dict(timing_payload.get("timings", {})),
                    "latency_ms": dict(timing_payload.get("latency_ms", {})),
                }
            )
            if "tts" in timing_payload and timing_payload["tts"] is not None:
                record["tts"] = dict(timing_payload["tts"])

        path = self.root_dir / str(user_id) / f"{session_id}.jsonl"
        async with _logger_lock():
            await asyncio.to_thread(self._upsert_record, path, record)
        return True

    @staticmethod
    def _upsert_record(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        previous_record: dict[str, Any] | None = None
        if path.exists():
            with path.open("r", encoding="utf-8") as existing:
                for line in existing:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    if item.get("turn_id") == record["turn_id"]:
                        previous_record = item
                    else:
                        records.append(item)
        if previous_record is not None:
            # Replays that arrive without a newer optional projection must
            # not erase timing/diagnostic fields already captured for the
            # same durable turn.
            merged_record = dict(previous_record)
            merged_record.update(record)
            record = merged_record
        records.append(record)

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temporary:
                for item in records:
                    temporary.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                    temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            Path(temporary_name).replace(path)
        finally:
            temporary_path = Path(temporary_name)
            if temporary_path.exists():
                temporary_path.unlink()


def _as_utc(value: datetime | None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _log_status(status: str) -> str:
    return {
        "committed": "completed",
        "cancelled": "cancelled",
        "failed": "failed",
    }.get(status, status)
