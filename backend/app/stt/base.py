"""Shared contracts for local speech-to-text engines.

The FastAPI/WebSocket layer depends on these contracts rather than on a
specific recognizer implementation.  Windows-specific process handling and
the optional legacy Whisper implementation live in separate modules.
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal


class STTError(RuntimeError):
    """Base error for local STT operations."""

    code = "stt_error"


class STTConfigurationError(STTError):
    code = "stt_configuration_error"


class STTAudioError(STTError):
    code = "stt_invalid_audio"


class STTCancelledError(STTError):
    code = "stt_cancelled"


class STTTimeoutError(STTError):
    code = "stt_timeout"


class STTInferenceError(STTError):
    code = "stt_inference_error"


@dataclass(frozen=True)
class STTEngineInfo:
    """Safe runtime diagnostics returned after engine initialization."""

    engine: str
    runtime: str
    available: bool
    language: str | None = None
    recognizer_name: str | None = None
    model_path: str | None = None
    model_format: str | None = None
    faster_whisper_version: str | None = None
    ctranslate2_version: str | None = None
    device: str | None = None
    compute_type: str | None = None
    load_time_ms: float = 0.0


# Kept as a compatibility name for callers and historical tests.
STTModelInfo = STTEngineInfo


@dataclass(frozen=True)
class STTEnginePartial:
    """A non-final hypothesis produced by an engine."""

    session_id: uuid.UUID
    turn_id: uuid.UUID
    response_id: uuid.UUID
    generation: int
    text: str
    language: str | None
    confidence: float | None
    audio_duration_ms: int
    timestamp_ms: int
    monotonic_timestamp: float


@dataclass(frozen=True)
class STTEngineFinal:
    """The final recognition result returned by an engine."""

    session_id: uuid.UUID
    turn_id: uuid.UUID
    response_id: uuid.UUID
    generation: int
    text: str
    language: str | None
    confidence: float | None
    timestamp_ms: int
    monotonic_timestamp: float
    inference_duration_ms: float


EnginePartialCallback = Callable[[STTEnginePartial], Awaitable[None]]


@dataclass
class STTEngineTurn:
    """Engine-owned correlation context for one session turn."""

    session_id: uuid.UUID
    turn_id: uuid.UUID
    response_id: uuid.UUID
    generation: int
    language: str | None
    on_partial: EnginePartialCallback
    state: Any = None


class STTEngine(ABC):
    """Async engine contract used by :class:`app.stt.service.STTService`."""

    name = "unknown"
    # Legacy batch engines need a bounded turn buffer.  Streaming engines do
    # not; their own IPC/audio boundary owns the bounded queue instead.
    buffers_audio = False

    @abstractmethod
    async def initialize(self) -> STTEngineInfo:
        """Validate dependencies and make the engine ready for turns."""

    @abstractmethod
    async def start_turn(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        generation: int,
        language: str | None,
        on_partial: EnginePartialCallback,
    ) -> STTEngineTurn:
        """Allocate isolated state for one turn."""

    @abstractmethod
    async def push_audio(
        self,
        turn: STTEngineTurn,
        pcm_bytes: bytes,
        *,
        generation: int,
    ) -> None:
        """Forward one validated PCM chunk to the recognizer."""

    @abstractmethod
    async def finish_turn(self, turn: STTEngineTurn, *, generation: int) -> STTEngineFinal:
        """Commit recognition and return one final result."""

    @abstractmethod
    async def cancel_turn(self, turn: STTEngineTurn, *, generation: int) -> None:
        """Cancel recognition and invalidate callbacks for the turn."""

    async def close_turn(self, turn: STTEngineTurn) -> None:  # noqa: B027
        """Release completed turn state, if the engine has any."""

    @abstractmethod
    async def close(self) -> None:
        """Release worker/model resources."""


def put_bounded_event(
    queue: asyncio.Queue[STTEnginePartial | None], event: STTEnginePartial
) -> None:
    """Offer an event without allowing a slow client to grow memory forever."""

    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        # A caller that cannot consume hypotheses does not get to grow the
        # process.  The newest event is intentionally dropped in this rare
        # race; final recognition is independent of this queue.
        pass


EventType = Literal["partial", "final"]
