"""Optional legacy Whisper engine.

This module is intentionally imported only when ``STT_ENGINE=whisper`` (or a
test injects a Whisper model factory).  The active Phase 4 default is the
Windows Speech engine and does not need either Whisper or CTranslate2.
"""

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

from app.core.config import Settings
from app.stt.base import (
    EnginePartialCallback,
    STTAudioError,
    STTCancelledError,
    STTConfigurationError,
    STTEngine,
    STTEngineFinal,
    STTEngineInfo,
    STTEnginePartial,
    STTEngineTurn,
    STTError,
    STTInferenceError,
    STTTimeoutError,
)

LOGGER = logging.getLogger("voice-assistance-backend")


@dataclass(frozen=True)
class _InferenceResult:
    text: str
    language: str | None
    inference_duration_ms: float


@dataclass
class _WhisperTurnState:
    handle: STTEngineTurn
    audio: bytearray
    audio_samples: int = 0
    last_partial_samples: int = 0
    partial_task: asyncio.Task[None] | None = None
    final_task: asyncio.Task[STTEngineFinal] | None = None
    active_cancel_event: threading.Event | None = None
    generation: int = 0
    last_partial_text: str | None = None
    finalized: bool = False
    cancelled: bool = False


class WhisperEngine(STTEngine):
    """Compatibility adapter for the previous local faster-whisper path."""

    name = "whisper"
    buffers_audio = True
    REQUIRED_MODEL_FILES = (
        "model.bin",
        "config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    )

    def __init__(
        self,
        settings: Settings,
        *,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._model_factory = model_factory
        self._final_model: Any | None = None
        self._partial_model: Any | None = None
        self._info: STTEngineInfo | None = None
        self._model_lock = asyncio.Lock()
        self._final_executor = ThreadPoolExecutor(
            max_workers=settings.stt_workers,
            thread_name_prefix="local-stt-final",
        )
        self._partial_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="local-stt-partial",
        )
        self._turns: dict[tuple[uuid.UUID, uuid.UUID], _WhisperTurnState] = {}
        self._turns_lock = asyncio.Lock()
        self._closed = False

    async def initialize(self) -> STTEngineInfo:
        if self._closed:
            raise STTConfigurationError("STT engine is closed")
        if self._info is not None:
            return self._info

        async with self._model_lock:
            if self._info is not None:
                return self._info
            self._validate_runtime_configuration()
            model_path = self.settings.stt_model_dir
            missing = [
                filename
                for filename in self.REQUIRED_MODEL_FILES
                if not (model_path / filename).is_file()
            ]
            if missing:
                raise STTConfigurationError(
                    f"Whisper model is missing required files: {', '.join(missing)}"
                )
            try:
                from faster_whisper import WhisperModel

                if self._model_factory is None:
                    self._model_factory = WhisperModel
                loop = asyncio.get_running_loop()
                started = time.perf_counter()
                self._final_model = await loop.run_in_executor(
                    self._final_executor,
                    self._load_model,
                    model_path,
                    self.settings.stt_threads,
                )
                self._partial_model = await loop.run_in_executor(
                    self._partial_executor,
                    self._load_model,
                    model_path,
                    1,
                )
                self._info = STTEngineInfo(
                    engine=self.name,
                    runtime="faster-whisper/CTranslate2",
                    available=True,
                    model_path=str(model_path),
                    model_format="CTranslate2",
                    faster_whisper_version=importlib.metadata.version("faster-whisper"),
                    ctranslate2_version=importlib.metadata.version("ctranslate2"),
                    device=self.settings.stt_device,
                    compute_type=self.settings.stt_compute_type,
                    load_time_ms=round((time.perf_counter() - started) * 1000, 1),
                )
                return self._info
            except STTConfigurationError:
                raise
            except Exception as error:  # noqa: BLE001 - normalize optional runtime failures
                raise STTConfigurationError(
                    f"Whisper engine initialization failed: {type(error).__name__}"
                ) from error

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
        await self.initialize()
        key = (session_id, turn_id)
        async with self._turns_lock:
            if self._closed:
                raise STTConfigurationError("STT engine is closed")
            if key in self._turns:
                raise STTConfigurationError("STT turn is already active")
            handle = STTEngineTurn(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                generation=generation,
                language=language,
                on_partial=on_partial,
            )
            self._turns[key] = _WhisperTurnState(handle=handle, audio=bytearray())
            return handle

    async def push_audio(
        self,
        turn: STTEngineTurn,
        pcm_bytes: bytes,
        *,
        generation: int,
    ) -> None:
        state = self._state(turn)
        if state.cancelled or state.finalized or generation != state.generation:
            raise STTCancelledError("STT turn is no longer accepting audio")
        if len(pcm_bytes) % 2:
            raise STTAudioError("PCM16 audio must contain an even number of bytes")
        if not pcm_bytes:
            return
        state.audio.extend(pcm_bytes)
        state.audio_samples += len(pcm_bytes) // 2
        partial_samples = round(
            self.settings.stt_partial_interval_seconds * self.settings.voice_sample_rate_hz
        )
        if state.audio_samples >= state.last_partial_samples + partial_samples and (
            state.partial_task is None or state.partial_task.done()
        ):
            state.last_partial_samples = state.audio_samples
            state.partial_task = asyncio.create_task(
                self._run_partial(state, self._partial_snapshot(state)),
                name=f"stt-partial-{turn.turn_id}",
            )

    async def finish_turn(self, turn: STTEngineTurn, *, generation: int) -> STTEngineFinal:
        state = self._state(turn)
        if state.cancelled:
            raise STTCancelledError("STT turn was cancelled")
        if state.finalized:
            raise STTConfigurationError("STT turn is already finalized")
        state.finalized = True
        state.generation = generation
        self._cancel_partial(state)
        if not state.audio:
            return STTEngineFinal(
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                response_id=turn.response_id,
                generation=generation,
                text="",
                language=turn.language,
                confidence=None,
                timestamp_ms=int(time.time() * 1000),
                monotonic_timestamp=time.monotonic(),
                inference_duration_ms=0.0,
            )
        state.final_task = asyncio.create_task(
            self._run_inference(state, bytes(state.audio), inference_kind="final"),
            name=f"stt-final-{turn.turn_id}",
        )
        try:
            return await state.final_task
        except asyncio.CancelledError as error:
            if state.cancelled:
                raise STTCancelledError("STT turn was cancelled") from error
            raise
        finally:
            state.final_task = None

    async def cancel_turn(self, turn: STTEngineTurn, *, generation: int) -> None:
        state = self._state(turn, required=False)
        if state is None:
            return
        state.cancelled = True
        state.generation = generation
        if state.active_cancel_event is not None:
            state.active_cancel_event.set()
        tasks = [state.partial_task, state.final_task]
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        for task in tasks:
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, STTError):
                    await task
        state.audio.clear()
        async with self._turns_lock:
            self._turns.pop((turn.session_id, turn.turn_id), None)

    async def close_turn(self, turn: STTEngineTurn) -> None:
        state = self._state(turn, required=False)
        if state is None:
            return
        if not state.cancelled and not state.finalized:
            await self.cancel_turn(turn, generation=state.generation + 1)
            return
        state.audio.clear()
        async with self._turns_lock:
            self._turns.pop((turn.session_id, turn.turn_id), None)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        turns = list(self._turns.values())
        for state in turns:
            await self.cancel_turn(state.handle, generation=state.generation + 1)
        await asyncio.to_thread(self._final_executor.shutdown, wait=True, cancel_futures=True)
        await asyncio.to_thread(self._partial_executor.shutdown, wait=True, cancel_futures=True)

    def _state(self, turn: STTEngineTurn, *, required: bool = True) -> _WhisperTurnState | None:
        state = self._turns.get((turn.session_id, turn.turn_id))
        if state is None and required:
            raise STTConfigurationError("STT turn is not active")
        return state

    def _load_model(self, model_path: Path, cpu_threads: int) -> Any:
        if self._model_factory is None:
            raise STTConfigurationError("Whisper model factory is unavailable")
        kwargs: dict[str, Any] = {
            "device": self.settings.stt_device,
            "compute_type": self.settings.stt_compute_type,
            "num_workers": self.settings.stt_workers if cpu_threads > 1 else 1,
            "cpu_threads": cpu_threads,
        }
        return self._model_factory(str(model_path), **kwargs)

    def _validate_runtime_configuration(self) -> None:
        if self.settings.stt_device.lower() != "cpu":
            raise STTConfigurationError("Legacy Whisper engine is configured for CPU only")
        if self.settings.stt_compute_type.lower() != "int8":
            raise STTConfigurationError("Legacy Whisper engine requires int8 compute type")

    def _partial_snapshot(self, state: _WhisperTurnState) -> bytes:
        max_bytes = (
            self.settings.stt_partial_window_seconds * self.settings.voice_sample_rate_hz * 2
        )
        return bytes(state.audio[-max_bytes:])

    def _cancel_partial(self, state: _WhisperTurnState) -> None:
        if state.active_cancel_event is not None:
            state.active_cancel_event.set()
        if state.partial_task is not None and not state.partial_task.done():
            state.partial_task.cancel()

    async def _run_partial(self, state: _WhisperTurnState, snapshot: bytes) -> None:
        cancel_event = threading.Event()
        state.active_cancel_event = cancel_event
        try:
            result = await self._run_inference(
                state,
                snapshot,
                inference_kind="partial",
                cancel_event=cancel_event,
            )
            if state.cancelled or state.finalized or result.text == state.last_partial_text:
                return
            state.last_partial_text = result.text
            text = result.text.strip()
            if not text:
                return
            await state.handle.on_partial(
                STTEnginePartial(
                    session_id=state.handle.session_id,
                    turn_id=state.handle.turn_id,
                    response_id=state.handle.response_id,
                    generation=state.generation,
                    text=text,
                    language=result.language,
                    confidence=None,
                    audio_duration_ms=round(
                        len(snapshot) / 2 / self.settings.voice_sample_rate_hz * 1000
                    ),
                    timestamp_ms=int(time.time() * 1000),
                    monotonic_timestamp=time.monotonic(),
                )
            )
        except (STTCancelledError, STTTimeoutError, STTInferenceError, asyncio.CancelledError):
            return
        finally:
            if state.active_cancel_event is cancel_event:
                state.active_cancel_event = None

    async def _run_inference(
        self,
        state: _WhisperTurnState,
        audio_bytes: bytes,
        *,
        inference_kind: Literal["partial", "final"],
        cancel_event: threading.Event | None = None,
    ) -> STTEngineFinal:
        active_cancel_event = cancel_event or threading.Event()
        state.active_cancel_event = active_cancel_event
        model = self._partial_model if inference_kind == "partial" else self._final_model
        if model is None:
            raise STTConfigurationError("Whisper model is not initialized")
        loop = asyncio.get_running_loop()
        executor = self._partial_executor if inference_kind == "partial" else self._final_executor
        future = loop.run_in_executor(
            executor,
            self._transcribe_sync,
            model,
            audio_bytes,
            state.handle.language,
            active_cancel_event,
            inference_kind,
        )
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(future, timeout=self.settings.stt_timeout)
            finished = time.monotonic()
            return STTEngineFinal(
                session_id=state.handle.session_id,
                turn_id=state.handle.turn_id,
                response_id=state.handle.response_id,
                generation=state.generation,
                text=result.text,
                language=result.language,
                confidence=None,
                timestamp_ms=int(time.time() * 1000),
                monotonic_timestamp=finished,
                inference_duration_ms=result.inference_duration_ms,
            )
        except TimeoutError as error:
            active_cancel_event.set()
            future.cancel()
            raise STTTimeoutError("STT inference timed out") from error
        except asyncio.CancelledError:
            active_cancel_event.set()
            future.cancel()
            raise
        except (STTError, STTCancelledError):
            raise
        except Exception as error:  # noqa: BLE001
            raise STTInferenceError(f"STT inference failed: {type(error).__name__}") from error
        finally:
            if state.active_cancel_event is active_cancel_event:
                state.active_cancel_event = None
            del started

    def _transcribe_sync(
        self,
        model: Any,
        audio_bytes: bytes,
        language: str | None,
        cancel_event: threading.Event,
        inference_kind: Literal["partial", "final"],
    ) -> _InferenceResult:
        import numpy as np

        started = time.perf_counter()
        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        audio = samples.astype(np.float32)
        audio /= 32768.0
        if not audio.size or float(np.sqrt(np.mean(np.square(audio)))) < 0.005:
            return _InferenceResult(
                text="",
                language=language,
                inference_duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        options: dict[str, Any] = {
            "beam_size": self.settings.stt_beam_size if inference_kind == "final" else 1,
            "condition_on_previous_text": False,
            "task": "transcribe",
            "vad_filter": False,
            "without_timestamps": True,
        }
        if language is not None:
            options["language"] = language
        try:
            segments, info = model.transcribe(audio, **options)
            text_parts: list[str] = []
            for segment in segments:
                if cancel_event.is_set():
                    raise STTCancelledError("STT inference was cancelled")
                text_parts.append(segment.text)
            return _InferenceResult(
                text="".join(text_parts).strip(),
                language=getattr(info, "language", None) or language,
                inference_duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        except STTError:
            raise
        except Exception as error:  # noqa: BLE001
            raise STTInferenceError(f"STT inference failed: {type(error).__name__}") from error
