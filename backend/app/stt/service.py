"""Engine-independent STT orchestration for voice turns."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import Settings
from app.stt.base import (
    STTAudioError,
    STTCancelledError,
    STTConfigurationError,
    STTEngine,
    STTEnginePartial,
    STTEngineTurn,
    STTInferenceError,
    STTModelInfo,
)
from app.stt.diagnostic_capture import DiagnosticPcmCapture
from app.stt.remote_engine import RemoteTranscriptionEngine
from app.stt.whisper_engine import WhisperEngine
from app.stt.windows_engine import WindowsSpeechEngine

LOGGER = logging.getLogger("voice-assistance-backend")


@dataclass(frozen=True)
class STTTranscriptEvent:
    event_type: Literal["transcript.partial", "transcript.final"]
    session_id: uuid.UUID
    turn_id: uuid.UUID
    response_id: uuid.UUID
    text: str
    final: bool
    transcript_sequence: int
    timestamp_ms: int
    language: str | None
    audio_duration_ms: int
    metrics: dict[str, float | int | None]


@dataclass(frozen=True)
class STTTranscriptResult:
    event: STTTranscriptEvent
    metrics: dict[str, float | int | None]


# The normalized language contract remains compatible with the old Whisper
# service, while the Windows engine resolves ``en`` to an installed English
# culture such as ``en-US``.
SUPPORTED_LANGUAGE_CODES = frozenset(
    {
        "af",
        "ar",
        "hy",
        "az",
        "be",
        "bg",
        "bn",
        "bs",
        "ca",
        "cs",
        "cy",
        "da",
        "de",
        "el",
        "en",
        "es",
        "et",
        "eu",
        "fa",
        "fi",
        "fr",
        "gl",
        "gu",
        "ha",
        "he",
        "hi",
        "hr",
        "hu",
        "id",
        "is",
        "it",
        "ja",
        "jw",
        "ka",
        "kk",
        "km",
        "kn",
        "ko",
        "la",
        "lb",
        "ln",
        "lo",
        "lt",
        "lv",
        "mg",
        "mi",
        "mk",
        "ml",
        "mn",
        "mr",
        "ms",
        "mt",
        "my",
        "ne",
        "nl",
        "nn",
        "no",
        "oc",
        "pa",
        "pl",
        "ps",
        "pt",
        "ro",
        "ru",
        "sa",
        "sd",
        "si",
        "sk",
        "sl",
        "sn",
        "so",
        "sq",
        "sr",
        "su",
        "sv",
        "sw",
        "ta",
        "te",
        "th",
        "tk",
        "tl",
        "tr",
        "tt",
        "uk",
        "ur",
        "uz",
        "vi",
        "yi",
        "yo",
        "zh",
    }
)


class STTService:
    """Own engine selection and bounded, correlation-safe STT turns."""

    _REQUIRED_MODEL_FILES = WhisperEngine.REQUIRED_MODEL_FILES

    def __init__(
        self,
        settings: Settings,
        *,
        engine: STTEngine | None = None,
        model_factory: Any | None = None,
    ) -> None:
        self.settings = settings
        self.engine = engine or self._select_engine(model_factory=model_factory)
        self._model_info: STTModelInfo | None = None
        self._turns: dict[tuple[uuid.UUID, uuid.UUID], STTTurn] = {}
        self._turns_lock = asyncio.Lock()
        self._initialize_lock = asyncio.Lock()
        self._closed = False
        self._diagnostic_capture_claimed = False

    @property
    def model_info(self) -> STTModelInfo | None:
        """Compatibility property for diagnostics; it now describes any engine."""

        return self._model_info

    @property
    def active_turn_count(self) -> int:
        return len(self._turns)

    async def initialize(self) -> STTModelInfo:
        if self._closed:
            raise STTConfigurationError("STT service is closed")
        if self._model_info is not None:
            return self._model_info
        async with self._initialize_lock:
            if self._model_info is None:
                self._model_info = await self.engine.initialize()
        return self._model_info

    async def start_turn(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        language: str | None = None,
    ) -> STTTurn:
        await self.initialize()
        selected_language = self.normalize_language(
            language or self.settings.stt_language,
            preserve_culture=isinstance(self.engine, WindowsSpeechEngine),
        )
        key = (session_id, turn_id)
        async with self._turns_lock:
            if self._closed:
                raise STTConfigurationError("STT service is closed")
            if key in self._turns:
                raise STTConfigurationError("STT turn is already active")
            if len(self._turns) >= self.settings.stt_max_active_turns:
                raise STTConfigurationError("maximum active STT turns reached")

            diagnostic_capture = self._claim_diagnostic_capture(
                session_id=session_id,
                turn_id=turn_id,
            )
            turn = STTTurn(
                service=self,
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                language=selected_language,
                diagnostic_capture=diagnostic_capture,
            )
            engine_turn = await self.engine.start_turn(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                generation=turn.generation,
                language=selected_language,
                on_partial=turn._on_engine_partial,
            )
            turn.engine_turn = engine_turn
            self._turns[key] = turn
            return turn

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for turn in list(self._turns.values()):
            await turn.cancel()
        await self.engine.close()

    async def _release_turn(self, turn: STTTurn) -> None:
        async with self._turns_lock:
            self._turns.pop((turn.session_id, turn.turn_id), None)

    def _claim_diagnostic_capture(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
    ) -> DiagnosticPcmCapture | None:
        if not self.settings.stt_diagnostic_capture_enabled or self._diagnostic_capture_claimed:
            return None
        self._diagnostic_capture_claimed = True
        return DiagnosticPcmCapture(
            self.settings.stt_diagnostic_capture_dir_resolved,
            session_id=str(session_id),
            turn_id=str(turn_id),
            max_seconds=self.settings.stt_max_audio_seconds,
        )

    def _select_engine(self, *, model_factory: Any | None) -> STTEngine:
        # A model factory is an explicit dependency-injection hook retained for
        # legacy tests and benchmarks.  It is not runtime fallback behavior.
        if model_factory is not None:
            return WhisperEngine(self.settings, model_factory=model_factory)
        if self.settings.stt_engine == "windows":
            raise STTConfigurationError(
                "STT_ENGINE=windows is retired for Phase 4; configure STT_ENGINE=remote"
            )
        if self.settings.stt_engine == "remote":
            return RemoteTranscriptionEngine(self.settings)
        if self.settings.stt_engine == "whisper":
            return WhisperEngine(self.settings)
        raise STTConfigurationError(f"unsupported STT_ENGINE: {self.settings.stt_engine}")

    @staticmethod
    def normalize_language(
        language: str | None,
        *,
        preserve_culture: bool = False,
    ) -> str | None:
        if language is None or not language.strip():
            return None
        normalized = language.strip().replace("_", "-")
        code = normalized.lower().split("-", 1)[0]
        if code not in SUPPORTED_LANGUAGE_CODES:
            raise STTConfigurationError(f"unsupported STT language: {language}")
        return normalized if preserve_culture else code


class STTTurn:
    """Bounded per-turn orchestration shared by all STT engines."""

    def __init__(
        self,
        *,
        service: STTService,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        language: str | None,
        diagnostic_capture: DiagnosticPcmCapture | None = None,
    ) -> None:
        self.service = service
        self.session_id = session_id
        self.turn_id = turn_id
        self.response_id = response_id
        self.language = language
        self._diagnostic_capture = diagnostic_capture
        self.engine_turn: STTEngineTurn | None = None
        self.events: asyncio.Queue[STTTranscriptEvent | None] = asyncio.Queue(maxsize=8)
        self._audio: bytearray | None = None
        self._audio_samples = 0
        self._generation = 0
        self._transcript_sequence = 0
        self._last_partial_text: str | None = None
        self._audio_start: float | None = None
        self._speech_start: float | None = None
        self._speech_end: float | None = None
        self._first_partial: float | None = None
        self._cancel_requested = False
        self._finalized = False
        self._events_closed = False

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def audio_duration_ms(self) -> int:
        return round(self._audio_samples / self.service.settings.voice_sample_rate_hz * 1000)

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    async def accept_audio(self, pcm_bytes: bytes) -> None:
        if self._finalized or self._cancel_requested:
            raise STTCancelledError("STT turn is no longer accepting audio")
        if not pcm_bytes:
            return
        if len(pcm_bytes) % 2:
            raise STTAudioError("PCM16 audio must contain an even number of bytes")
        max_bytes = (
            self.service.settings.stt_max_audio_seconds
            * self.service.settings.voice_sample_rate_hz
            * 2
        )
        if (self._audio_samples * 2) + len(pcm_bytes) > max_bytes:
            raise STTAudioError("STT turn audio limit exceeded")
        if self._audio_start is None:
            self._audio_start = time.monotonic()
            self._speech_start = self._audio_start
            LOGGER.info(
                "STT audio started",
                extra={
                    "event": "STT_AUDIO_RECEIVED",
                    "session_id": str(self.session_id),
                    "turn_id": str(self.turn_id),
                    "response_id": str(self.response_id),
                    "generation": self._generation,
                    "audio_start_timestamp_ms": int(time.time() * 1000),
                    "audio_start_monotonic_ms": round(self._audio_start * 1000, 1),
                },
            )
        self._audio_samples += len(pcm_bytes) // 2
        if self.service.engine.buffers_audio:
            if self._audio is None:
                self._audio = bytearray()
            self._audio.extend(pcm_bytes)
        if self.engine_turn is None:
            raise STTConfigurationError("STT engine turn is not initialized")
        if self._diagnostic_capture is not None:
            self._diagnostic_capture.append(pcm_bytes)
        await self.service.engine.push_audio(
            self.engine_turn,
            pcm_bytes,
            generation=self._generation,
        )

    async def finalize(self) -> STTTranscriptResult:
        if self._cancel_requested:
            raise STTCancelledError("STT turn was cancelled")
        if self._finalized:
            raise STTConfigurationError("STT turn is already finalized")
        if self.engine_turn is None:
            raise STTConfigurationError("STT engine turn is not initialized")
        self._finalized = True
        self._generation += 1
        self._speech_end = time.monotonic()
        LOGGER.info(
            "STT finalization requested",
            extra={
                "event": "STT_COMMIT_RECEIVED",
                "session_id": str(self.session_id),
                "turn_id": str(self.turn_id),
                "response_id": str(self.response_id),
                "generation": self._generation,
                "audio_duration_ms": self.audio_duration_ms,
                "commit_received_timestamp_ms": int(time.time() * 1000),
                "commit_received_monotonic_ms": round(time.monotonic() * 1000, 1),
                "speech_end_timestamp_ms": int(time.time() * 1000),
                "speech_end_monotonic_ms": round(self._speech_end * 1000, 1),
            },
        )
        self._clear_pending_events()
        try:
            result = await self.service.engine.finish_turn(
                self.engine_turn,
                generation=self._generation,
            )
        except asyncio.CancelledError as error:
            if self._cancel_requested:
                raise STTCancelledError("STT turn was cancelled") from error
            raise
        except Exception as error:
            self._finish_diagnostic_capture(
                status="error",
                error=f"{type(error).__name__}: {error}",
            )
            raise
        self._finish_diagnostic_capture(status="final", hypothesis_raw=result.text)
        if (
            result.session_id != self.session_id
            or result.turn_id != self.turn_id
            or result.response_id != self.response_id
            or result.generation != self._generation
        ):
            raise STTInferenceError("stale or mismatched STT final result")
        if self._cancel_requested:
            raise STTCancelledError("STT turn was cancelled")

        final_at = max(time.monotonic(), result.monotonic_timestamp)
        speech_end = self._speech_end or final_at
        audio_start = self._audio_start or speech_end
        metrics: dict[str, float | int | None] = {
            "audio_duration_ms": self.audio_duration_ms,
            "inference_duration_ms": result.inference_duration_ms,
            "confidence": result.confidence,
            "first_partial_latency_ms": (
                round((self._first_partial - audio_start) * 1000, 1)
                if self._first_partial is not None
                else None
            ),
            "speech_end_to_final_transcript_ms": round((final_at - speech_end) * 1000, 1),
            "commit_to_final_transcript_ms": round((final_at - speech_end) * 1000, 1),
            "real_time_factor": (
                round(result.inference_duration_ms / self.audio_duration_ms, 4)
                if self.audio_duration_ms and result.inference_duration_ms
                else None
            ),
            "monotonic_audio_start_ms": round(audio_start * 1000, 1),
            "monotonic_speech_end_ms": round(speech_end * 1000, 1),
            "monotonic_final_ms": round(final_at * 1000, 1),
        }
        self._transcript_sequence += 1
        event = STTTranscriptEvent(
            event_type="transcript.final",
            session_id=self.session_id,
            turn_id=self.turn_id,
            response_id=self.response_id,
            text=result.text,
            final=True,
            transcript_sequence=self._transcript_sequence,
            timestamp_ms=result.timestamp_ms,
            language=result.language,
            audio_duration_ms=self.audio_duration_ms,
            metrics=metrics,
        )
        LOGGER.info(
            "STT final transcript ready",
            extra={
                "event": "STT_FINAL",
                "session_id": str(self.session_id),
                "turn_id": str(self.turn_id),
                "response_id": str(self.response_id),
                "generation": self._generation,
                "text": event.text,
                "language": event.language,
                "final_transcript_timestamp_ms": event.timestamp_ms,
                "final_transcript_monotonic_ms": round(final_at * 1000, 1),
                "metrics": metrics,
            },
        )
        return STTTranscriptResult(event=event, metrics=metrics)

    async def _on_engine_partial(self, partial: STTEnginePartial) -> None:
        if (
            self._cancel_requested
            or self._finalized
            or partial.generation != self._generation
            or partial.session_id != self.session_id
            or partial.turn_id != self.turn_id
            or partial.response_id != self.response_id
        ):
            LOGGER.debug(
                "Ignoring stale STT partial",
                extra={
                    "event": "STT_STALE_RESULT_IGNORED",
                    "session_id": str(partial.session_id),
                    "turn_id": str(partial.turn_id),
                    "response_id": str(partial.response_id),
                    "generation": partial.generation,
                },
            )
            return
        text = partial.text.strip()
        if not text or text == self._last_partial_text:
            return
        self._last_partial_text = text
        self._transcript_sequence += 1
        now = partial.monotonic_timestamp
        if self._first_partial is None:
            self._first_partial = now
        audio_start = self._audio_start or now
        event = STTTranscriptEvent(
            event_type="transcript.partial",
            session_id=self.session_id,
            turn_id=self.turn_id,
            response_id=self.response_id,
            text=text,
            final=False,
            transcript_sequence=self._transcript_sequence,
            timestamp_ms=partial.timestamp_ms,
            language=partial.language,
            audio_duration_ms=partial.audio_duration_ms,
            metrics={
                "first_partial_latency_ms": round((now - audio_start) * 1000, 1),
                "confidence": partial.confidence,
            },
        )
        self._offer_event(event)
        LOGGER.info(
            "STT partial transcript emitted",
            extra={
                "event": "STT_PARTIAL",
                "session_id": str(self.session_id),
                "turn_id": str(self.turn_id),
                "response_id": str(self.response_id),
                "generation": partial.generation,
                "text": event.text,
                "language": event.language,
                "timestamp_ms": event.timestamp_ms,
                "monotonic_ms": round(now * 1000, 1),
                "metrics": event.metrics,
            },
        )

    async def cancel(self) -> None:
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self._generation += 1
        if self.engine_turn is not None:
            await self.service.engine.cancel_turn(
                self.engine_turn,
                generation=self._generation,
            )
        if self._audio is not None:
            self._audio.clear()
        self._finish_diagnostic_capture(status="cancelled", error="turn_cancelled")
        await self._close_events()
        await self.service._release_turn(self)

    async def close(self) -> None:
        if self.engine_turn is not None and not self._cancel_requested:
            await self.service.engine.close_turn(self.engine_turn)
        if self._audio is not None:
            self._audio.clear()
        self._finish_diagnostic_capture(status="closed", error="turn_closed_before_finalize")
        await self._close_events()
        await self.service._release_turn(self)

    def _offer_event(self, event: STTTranscriptEvent) -> None:
        if self._events_closed:
            return
        if self.events.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.events.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self.events.put_nowait(event)

    def _clear_pending_events(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def _close_events(self) -> None:
        if self._events_closed:
            return
        self._events_closed = True
        self._clear_pending_events()
        await self.events.put(None)

    def _finish_diagnostic_capture(
        self,
        *,
        status: str,
        hypothesis_raw: str | None = None,
        error: str | None = None,
    ) -> None:
        if self._diagnostic_capture is None:
            return
        self._diagnostic_capture.finalize(
            status=status,
            hypothesis_raw=hypothesis_raw,
            error=error,
        )
        self._diagnostic_capture = None
