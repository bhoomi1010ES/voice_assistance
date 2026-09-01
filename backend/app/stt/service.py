from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import ctranslate2
import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.tokenizer import _LANGUAGE_CODES

from app.core.config import Settings

LOGGER = logging.getLogger("voice-assistance-backend")


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
class STTModelInfo:
    model_path: str
    model_format: str
    faster_whisper_version: str
    ctranslate2_version: str
    device: str
    compute_type: str
    load_time_ms: float


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


@dataclass(frozen=True)
class _InferenceResult:
    text: str
    language: str | None
    inference_duration_ms: float


class STTService:
    """Own one reusable CPU Whisper model and bounded turn workers.

    The model is initialized lazily once per service. Inference runs in a
    bounded ThreadPoolExecutor so the asyncio/WebSocket event loop remains
    responsive. Each turn owns its own bounded raw PCM buffer and transcript
    event queue; no audio or transcript state is shared between turns.
    """

    _REQUIRED_MODEL_FILES = (
        "model.bin",
        "config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    )
    _SILENCE_RMS_THRESHOLD = 0.005

    def __init__(
        self,
        settings: Settings,
        *,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._model_factory = model_factory or WhisperModel
        self._model: Any | None = None
        self._final_model: Any | None = None
        self._partial_model: Any | None = None
        self._model_info: STTModelInfo | None = None
        self._model_lock = asyncio.Lock()
        self._final_executor = ThreadPoolExecutor(
            max_workers=settings.stt_workers,
            thread_name_prefix="local-stt-final",
        )
        self._partial_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="local-stt-partial",
        )
        self._turns: dict[tuple[uuid.UUID, uuid.UUID], STTTurn] = {}
        self._turns_lock = asyncio.Lock()
        self._closed = False

    @property
    def model_info(self) -> STTModelInfo | None:
        return self._model_info

    @property
    def active_turn_count(self) -> int:
        return len(self._turns)

    async def initialize(self) -> STTModelInfo:
        """Load the configured CT2 model once and return its measured metadata."""

        if self._closed:
            raise STTConfigurationError("STT service is closed")
        if self._model_info is not None:
            return self._model_info

        async with self._model_lock:
            if self._model_info is not None:
                return self._model_info
            self._validate_runtime_configuration()
            model_path = self.settings.stt_model_dir
            missing = [
                filename
                for filename in self._REQUIRED_MODEL_FILES
                if not (model_path / filename).is_file()
            ]
            if missing:
                raise STTConfigurationError(
                    f"CTranslate2 model is missing required files: {', '.join(missing)}"
                )

            loop = asyncio.get_running_loop()
            started = time.perf_counter()
            LOGGER.info(
                "STT model initialization started",
                extra={
                    "event": "stt.model.load.started",
                    "model_path": str(model_path),
                    "model_format": "CTranslate2",
                    "device": self.settings.stt_device,
                    "compute_type": self.settings.stt_compute_type,
                    "cpu_threads": self.settings.stt_threads,
                    "workers": self.settings.stt_workers,
                },
            )
            try:
                final_model = await loop.run_in_executor(
                    self._final_executor,
                    self._load_model,
                    model_path,
                    self.settings.stt_threads,
                )
                partial_model = await loop.run_in_executor(
                    self._partial_executor,
                    self._load_model,
                    model_path,
                    1,
                )
            except STTError:
                raise
            except Exception as error:  # noqa: BLE001 - expose a stable service error
                raise STTConfigurationError(
                    f"Whisper model initialization failed: {type(error).__name__}"
                ) from error
            self._final_model = final_model
            self._partial_model = partial_model
            self._model = final_model
            self._model_info = STTModelInfo(
                model_path=str(model_path),
                model_format="CTranslate2",
                faster_whisper_version=importlib.metadata.version("faster-whisper"),
                ctranslate2_version=ctranslate2.__version__,
                device=self.settings.stt_device,
                compute_type=self.settings.stt_compute_type,
                load_time_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            LOGGER.info(
                "STT model initialization completed",
                extra={
                    "event": "stt.model.load.completed",
                    "model_path": str(model_path),
                    "model_format": "CTranslate2",
                    "device": self.settings.stt_device,
                    "compute_type": self.settings.stt_compute_type,
                    "cpu_threads": self.settings.stt_threads,
                    "workers": self.settings.stt_workers,
                    "load_time_ms": self._model_info.load_time_ms,
                },
            )
            LOGGER.info(
                "STT model ready for inference",
                extra={
                    "event": "stt.model.ready",
                    "model_path": str(model_path),
                    "device": self.settings.stt_device,
                    "compute_type": self.settings.stt_compute_type,
                    "cpu_threads": self.settings.stt_threads,
                },
            )
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
        selected_language = self.normalize_language(language or self.settings.stt_language)
        key = (session_id, turn_id)
        async with self._turns_lock:
            if self._closed:
                raise STTConfigurationError("STT service is closed")
            if key in self._turns:
                raise STTConfigurationError("STT turn is already active")
            if len(self._turns) >= self.settings.stt_max_active_turns:
                raise STTConfigurationError("maximum active STT turns reached")
            turn = STTTurn(
                service=self,
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                language=selected_language,
            )
            self._turns[key] = turn
            return turn

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        turns = list(self._turns.values())
        for turn in turns:
            await turn.cancel()
        await asyncio.to_thread(self._final_executor.shutdown, wait=True, cancel_futures=True)
        await asyncio.to_thread(self._partial_executor.shutdown, wait=True, cancel_futures=True)

    async def _release_turn(self, turn: STTTurn) -> None:
        async with self._turns_lock:
            self._turns.pop((turn.session_id, turn.turn_id), None)

    def _load_model(self, model_path: Path, cpu_threads: int | None = None) -> Any:
        import inspect

        threads = cpu_threads or self.settings.stt_threads
        kwargs: dict[str, Any] = {
            "device": self.settings.stt_device,
            "compute_type": self.settings.stt_compute_type,
            "num_workers": self.settings.stt_workers,
        }
        try:
            sig = inspect.signature(self._model_factory)
            if "cpu_threads" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                kwargs["cpu_threads"] = threads
        except (ValueError, TypeError):
            kwargs["cpu_threads"] = threads
        return self._model_factory(str(model_path), **kwargs)

    def _validate_runtime_configuration(self) -> None:
        if self.settings.stt_device.lower() != "cpu":
            raise STTConfigurationError("Phase 4 STT is permanently CPU-only")
        if self.settings.stt_compute_type.lower() != "int8":
            raise STTConfigurationError("Phase 4 CPU STT requires int8 compute type")

    @staticmethod
    def normalize_language(language: str | None) -> str | None:
        if language is None or not language.strip():
            return None
        code = language.strip().lower().replace("_", "-").split("-", 1)[0]
        if code not in _LANGUAGE_CODES:
            raise STTConfigurationError(f"unsupported STT language: {language}")
        return code

    async def _transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None,
        cancel_event: threading.Event,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        inference_kind: Literal["partial", "final"],
    ) -> _InferenceResult:
        model = (
            self._partial_model
            if inference_kind == "partial" and self._partial_model is not None
            else self._final_model or self._model
        )
        if model is None:
            raise STTConfigurationError("STT model is not initialized")
        if cancel_event.is_set():
            raise STTCancelledError("STT inference was cancelled")
        loop = asyncio.get_running_loop()
        audio_duration_ms = round(len(audio_bytes) / 2 / self.settings.voice_sample_rate_hz * 1000)
        LOGGER.info(
            "STT inference submitted",
            extra={
                "event": "stt.inference.submitted",
                "session_id": str(session_id),
                "turn_id": str(turn_id),
                "response_id": str(response_id),
                "inference_kind": inference_kind,
                "audio_duration_ms": audio_duration_ms,
                "submitted_timestamp_ms": int(time.time() * 1000),
                "submitted_monotonic_ms": round(time.monotonic() * 1000, 1),
            },
        )
        executor = self._partial_executor if inference_kind == "partial" else self._final_executor
        future = loop.run_in_executor(
            executor,
            self._transcribe_sync,
            model,
            audio_bytes,
            language,
            cancel_event,
            session_id,
            turn_id,
            response_id,
            inference_kind,
        )
        try:
            return await asyncio.wait_for(future, timeout=self.settings.stt_timeout)
        except TimeoutError as error:
            cancel_event.set()
            future.cancel()
            raise STTTimeoutError("STT inference timed out") from error
        except asyncio.CancelledError:
            cancel_event.set()
            future.cancel()
            raise
        except STTError:
            raise
        except Exception as error:  # noqa: BLE001 - isolate model failures per turn
            raise STTInferenceError(f"STT inference failed: {type(error).__name__}") from error

    def _transcribe_sync(
        self,
        model: Any,
        audio_bytes: bytes,
        language: str | None,
        cancel_event: threading.Event,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        inference_kind: Literal["partial", "final"],
    ) -> _InferenceResult:
        started = time.perf_counter()
        LOGGER.info(
            "STT inference started",
            extra={
                "event": "stt.inference.started",
                "session_id": str(session_id),
                "turn_id": str(turn_id),
                "response_id": str(response_id),
                "inference_kind": inference_kind,
                "inference_start_timestamp_ms": int(time.time() * 1000),
                "inference_start_monotonic_ms": round(time.monotonic() * 1000, 1),
                "audio_duration_ms": round(
                    len(audio_bytes) / 2 / self.settings.voice_sample_rate_hz * 1000
                ),
            },
        )
        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        audio = samples.astype(np.float32)
        audio /= 32768.0
        # The Android VAD owns speech-end detection. This inexpensive guard
        # prevents a committed near-silence turn from becoming a Whisper
        # hallucination without introducing a second VAD implementation.
        if (
            not audio.size
            or float(np.sqrt(np.mean(np.square(audio)))) < self._SILENCE_RMS_THRESHOLD
        ):
            result = _InferenceResult(
                text="",
                language=language,
                inference_duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            LOGGER.info(
                "STT inference completed",
                extra={
                    "event": "stt.inference.completed",
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "response_id": str(response_id),
                    "inference_kind": inference_kind,
                    "inference_duration_ms": result.inference_duration_ms,
                    "text": result.text,
                    "completed_timestamp_ms": int(time.time() * 1000),
                    "completed_monotonic_ms": round(time.monotonic() * 1000, 1),
                },
            )
            return result
        options: dict[str, Any] = {
            "beam_size": self.settings.stt_beam_size if inference_kind == "final" else 1,
            "condition_on_previous_text": False,
            "task": "transcribe",
            "vad_filter": False,
            "without_timestamps": True,
        }
        target_language = language or self.settings.stt_language
        if target_language is not None:
            options["language"] = target_language
        try:
            segments, info = model.transcribe(audio, **options)
            text_parts: list[str] = []
            for segment in segments:
                if cancel_event.is_set():
                    raise STTCancelledError("STT inference was cancelled")
                text_parts.append(segment.text)
            detected_language = getattr(info, "language", None) or target_language
            result = _InferenceResult(
                text="".join(text_parts).strip(),
                language=detected_language,
                inference_duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            LOGGER.info(
                "STT inference completed",
                extra={
                    "event": "stt.inference.completed",
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "response_id": str(response_id),
                    "inference_kind": inference_kind,
                    "inference_duration_ms": result.inference_duration_ms,
                    "text": result.text,
                    "language": result.language,
                    "completed_timestamp_ms": int(time.time() * 1000),
                    "completed_monotonic_ms": round(time.monotonic() * 1000, 1),
                },
            )
            return result
        except STTError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize runtime exceptions
            LOGGER.exception(
                "STT inference failed",
                extra={
                    "event": "stt.inference.failed",
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "response_id": str(response_id),
                    "inference_kind": inference_kind,
                    "inference_duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            raise STTInferenceError(f"STT inference failed: {type(error).__name__}") from error


class STTTurn:
    """Bounded, isolated state for one STT session/turn."""

    def __init__(
        self,
        *,
        service: STTService,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        language: str | None,
    ) -> None:
        self.service = service
        self.session_id = session_id
        self.turn_id = turn_id
        self.response_id = response_id
        self.language = language
        self.events: asyncio.Queue[STTTranscriptEvent | None] = asyncio.Queue(maxsize=8)
        self._audio = bytearray()
        self._audio_samples = 0
        self._last_partial_samples = 0
        self._partial_task: asyncio.Task[None] | None = None
        self._final_task: asyncio.Task[STTTranscriptResult] | None = None
        self._active_cancel_event: threading.Event | None = None
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
        if len(self._audio) + len(pcm_bytes) > max_bytes:
            raise STTAudioError("STT turn audio limit exceeded")
        if self._audio_start is None:
            self._audio_start = time.monotonic()
            self._speech_start = self._audio_start
            LOGGER.info(
                "STT audio started",
                extra={
                    "event": "stt.audio.started",
                    "session_id": str(self.session_id),
                    "turn_id": str(self.turn_id),
                    "response_id": str(self.response_id),
                    "audio_start_timestamp_ms": int(time.time() * 1000),
                    "audio_start_monotonic_ms": round(self._audio_start * 1000, 1),
                },
            )
        self._audio.extend(pcm_bytes)
        self._audio_samples += len(pcm_bytes) // 2
        partial_samples = round(
            self.service.settings.stt_partial_interval_seconds
            * self.service.settings.voice_sample_rate_hz
        )
        if self._audio_samples >= self._last_partial_samples + partial_samples and (
            self._partial_task is None or self._partial_task.done()
        ):
            self._last_partial_samples = self._audio_samples
            snapshot = self._partial_snapshot()
            generation = self._generation
            LOGGER.info(
                "STT partial inference scheduled",
                extra={
                    "event": "stt.partial.scheduled",
                    "session_id": str(self.session_id),
                    "turn_id": str(self.turn_id),
                    "response_id": str(self.response_id),
                    "audio_duration_ms": self.audio_duration_ms,
                    "scheduled_timestamp_ms": int(time.time() * 1000),
                },
            )
            self._partial_task = asyncio.create_task(
                self._run_partial(snapshot, generation),
                name=f"stt-partial-{self.turn_id}",
            )

    async def finalize(self) -> STTTranscriptResult:
        if self._cancel_requested:
            raise STTCancelledError("STT turn was cancelled")
        if self._finalized:
            raise STTConfigurationError("STT turn is already finalized")
        self._finalized = True
        self._generation += 1
        LOGGER.info(
            "STT finalization requested",
            extra={
                "event": "stt.finalize.requested",
                "session_id": str(self.session_id),
                "turn_id": str(self.turn_id),
                "response_id": str(self.response_id),
                "audio_duration_ms": self.audio_duration_ms,
                "partial_task_running": self._partial_task is not None
                and not self._partial_task.done(),
                "finalize_requested_timestamp_ms": int(time.time() * 1000),
            },
        )
        self._cancel_partial_task(wait=False)
        self._clear_pending_events()
        self._speech_end = time.monotonic()
        LOGGER.info(
            "STT speech end marked from client commit",
            extra={
                "event": "stt.speech_end.marked",
                "session_id": str(self.session_id),
                "turn_id": str(self.turn_id),
                "response_id": str(self.response_id),
                "speech_end_source": "client.audio.commit",
                "speech_end_timestamp_ms": int(time.time() * 1000),
                "speech_end_monotonic_ms": round(self._speech_end * 1000, 1),
            },
        )
        if not self._audio:
            inference = _InferenceResult(text="", language=self.language, inference_duration_ms=0.0)
        else:
            self._final_task = asyncio.create_task(
                self._run_inference(bytes(self._audio)),
                name=f"stt-final-{self.turn_id}",
            )
            try:
                inference = await self._final_task
            except asyncio.CancelledError as error:
                if self._cancel_requested:
                    raise STTCancelledError("STT turn was cancelled") from error
                raise
            finally:
                self._final_task = None
        if self._cancel_requested:
            raise STTCancelledError("STT turn was cancelled")

        final_at = time.monotonic()
        speech_end = self._speech_end or final_at
        audio_start = self._audio_start or speech_end
        metrics: dict[str, float | int | None] = {
            "audio_duration_ms": self.audio_duration_ms,
            "inference_duration_ms": inference.inference_duration_ms,
            "first_partial_latency_ms": (
                round((self._first_partial - audio_start) * 1000, 1)
                if self._first_partial is not None
                else None
            ),
            "speech_end_to_final_transcript_ms": round((final_at - speech_end) * 1000, 1),
            "commit_to_final_transcript_ms": round((final_at - speech_end) * 1000, 1),
            "real_time_factor": (
                round(inference.inference_duration_ms / self.audio_duration_ms, 4)
                if self.audio_duration_ms
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
            text=inference.text,
            final=True,
            transcript_sequence=self._transcript_sequence,
            timestamp_ms=int(time.time() * 1000),
            language=inference.language,
            audio_duration_ms=self.audio_duration_ms,
            metrics=metrics,
        )
        LOGGER.info(
            "STT final transcript ready",
            extra={
                "event": "stt.final.completed",
                "session_id": str(self.session_id),
                "turn_id": str(self.turn_id),
                "response_id": str(self.response_id),
                "text": event.text,
                "language": event.language,
                "final_transcript_timestamp_ms": event.timestamp_ms,
                "final_transcript_monotonic_ms": round(final_at * 1000, 1),
                "metrics": metrics,
            },
        )
        return STTTranscriptResult(event=event, metrics=metrics)

    def request_cancel(self) -> None:
        """Signal cancellation without awaiting, safe for a receive-side fast path."""

        if self._finalized and not self._final_task:
            return
        self._cancel_requested = True
        self._generation += 1
        if self._active_cancel_event is not None:
            self._active_cancel_event.set()
        if self._partial_task is not None and not self._partial_task.done():
            self._partial_task.cancel()
        if self._final_task is not None and not self._final_task.done():
            self._final_task.cancel()

    async def cancel(self) -> None:
        self.request_cancel()
        current = asyncio.current_task()
        for task in (self._partial_task, self._final_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        for task in (self._partial_task, self._final_task):
            if task is not None and task is not current:
                with contextlib.suppress(asyncio.CancelledError, STTError):
                    await task
        self._audio.clear()
        await self._close_events()
        await self.service._release_turn(self)

    async def close(self) -> None:
        if not self._cancel_requested:
            self._cancel_partial_task()
        self._audio.clear()
        await self._close_events()
        await self.service._release_turn(self)

    async def _run_partial(self, audio_snapshot: bytes, generation: int) -> None:
        cancel_event = threading.Event()
        self._active_cancel_event = cancel_event
        try:
            result = await self._run_inference(
                audio_snapshot,
                cancel_event=cancel_event,
                inference_kind="partial",
            )
            if (
                self._cancel_requested
                or self._finalized
                or generation != self._generation
                or result.text == self._last_partial_text
            ):
                return
            self._last_partial_text = result.text
            self._transcript_sequence += 1
            now = time.monotonic()
            if self._first_partial is None:
                self._first_partial = now
            audio_start = self._audio_start or now
            event = STTTranscriptEvent(
                event_type="transcript.partial",
                session_id=self.session_id,
                turn_id=self.turn_id,
                response_id=self.response_id,
                text=result.text,
                final=False,
                transcript_sequence=self._transcript_sequence,
                timestamp_ms=int(time.time() * 1000),
                language=result.language,
                audio_duration_ms=round(
                    len(audio_snapshot) / 2 / self.service.settings.voice_sample_rate_hz * 1000
                ),
                metrics={
                    "first_partial_latency_ms": round((now - audio_start) * 1000, 1),
                    "inference_duration_ms": result.inference_duration_ms,
                },
            )
            self._offer_event(event)
            LOGGER.info(
                "STT partial transcript emitted",
                extra={
                    "event": "stt.partial.emitted",
                    "session_id": str(self.session_id),
                    "turn_id": str(self.turn_id),
                    "response_id": str(self.response_id),
                    "text": event.text,
                    "language": event.language,
                    "timestamp_ms": event.timestamp_ms,
                    "transcript_sequence": event.transcript_sequence,
                    "metrics": event.metrics,
                },
            )
        except (STTCancelledError, STTTimeoutError, STTInferenceError):
            return
        except asyncio.CancelledError:
            return
        finally:
            if self._active_cancel_event is cancel_event:
                self._active_cancel_event = None

    async def _run_inference(
        self,
        audio_bytes: bytes,
        *,
        cancel_event: threading.Event | None = None,
        inference_kind: Literal["partial", "final"] = "final",
    ) -> _InferenceResult:
        active_cancel_event = cancel_event or threading.Event()
        self._active_cancel_event = active_cancel_event
        try:
            return await self.service._transcribe(
                audio_bytes,
                language=self.language,
                cancel_event=active_cancel_event,
                session_id=self.session_id,
                turn_id=self.turn_id,
                response_id=self.response_id,
                inference_kind=inference_kind,
            )
        except asyncio.CancelledError as error:
            active_cancel_event.set()
            raise STTCancelledError("STT inference was cancelled") from error
        finally:
            if self._active_cancel_event is active_cancel_event:
                self._active_cancel_event = None

    def _partial_snapshot(self) -> bytes:
        max_bytes = (
            self.service.settings.stt_partial_window_seconds
            * self.service.settings.voice_sample_rate_hz
            * 2
        )
        return bytes(self._audio[-max_bytes:])

    def _cancel_partial_task(self, *, wait: bool = False) -> None:
        task = self._partial_task
        if task is None or task.done():
            return
        if self._active_cancel_event is not None:
            self._active_cancel_event.set()
        task.cancel()

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
