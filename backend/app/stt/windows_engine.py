"""Windows Speech Recognition Engine adapter.

The adapter owns one reusable local C# worker process.  The worker receives
bounded base64-wrapped PCM chunks over stdin and emits newline-delimited JSON
over stdout.  No Windows interop is imported by the FastAPI gateway.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass

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
from app.stt.protocol import WorkerProtocolError, WorkerRequest, WorkerResponse, parse_response

LOGGER = logging.getLogger("voice-assistance-backend")


@dataclass
class _WindowsTurnState:
    handle: STTEngineTurn
    start_future: asyncio.Future[WorkerResponse]
    final_future: asyncio.Future[STTEngineFinal]
    generation: int
    audio_bytes: int = 0
    committed: bool = False
    cancelled: bool = False
    cancel_sent: bool = False
    error: Exception | None = None

    @property
    def audio_duration_ms(self) -> int:
        return round(self.audio_bytes / 2 / 16_000 * 1000)


class WindowsSpeechEngine(STTEngine):
    """STTEngine implementation backed by ``System.Speech.Recognition``."""

    name = "windows"
    buffers_audio = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._startup_lock = asyncio.Lock()
        self._ready_future: asyncio.Future[WorkerResponse] | None = None
        self._turns: dict[tuple[uuid.UUID, uuid.UUID], _WindowsTurnState] = {}
        self._info: STTEngineInfo | None = None
        self._worker_error: STTError | None = None
        self._closed = False
        self._stopping = False

    async def initialize(self) -> STTEngineInfo:
        if self._closed:
            raise STTConfigurationError("Windows Speech engine is closed")
        if self._info is not None and self._process is not None and self._worker_error is None:
            return self._info
        if os.name != "nt":
            raise STTConfigurationError("Windows Speech engine requires a Windows host")

        async with self._startup_lock:
            if self._info is not None and self._process is not None and self._worker_error is None:
                return self._info
            if self._process is not None:
                await self._stop_process(force=True)
            self._info = None
            self._worker_error = None
            worker_command = self._worker_command()
            loop = asyncio.get_running_loop()
            self._ready_future = loop.create_future()
            started = time.perf_counter()
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *worker_command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=self.settings.stt_worker_max_line_bytes,
                )
            except (FileNotFoundError, OSError) as error:
                raise STTConfigurationError(
                    "Windows Speech worker could not start: "
                    f"{type(error).__name__}. Verify STT_WINDOWS_WORKER_PATH "
                    "points to the self-contained publish output."
                ) from error

            self._stopping = False
            LOGGER.info(
                "Windows Speech worker process started",
                extra={
                    "event": "STT_WORKER_PROCESS_STARTED",
                    "worker_pid": getattr(self._process, "pid", None),
                    "path": str(self.settings.stt_windows_worker_path_resolved),
                    "monotonic_ms": round(time.monotonic() * 1000, 1),
                },
            )
            self._reader_task = asyncio.create_task(self._reader_loop(), name="windows-stt-reader")
            self._stderr_task = asyncio.create_task(self._stderr_loop(), name="windows-stt-stderr")
            try:
                ready = await asyncio.wait_for(
                    self._ready_future,
                    timeout=self.settings.stt_start_timeout_seconds,
                )
            except TimeoutError as error:
                await self._stop_process(force=True)
                raise STTTimeoutError("Windows Speech worker startup timed out") from error
            except STTError:
                await self._stop_process(force=True)
                raise

            if not ready.available:
                await self._stop_process(force=True)
                raise STTConfigurationError("No Windows speech recognizer is installed")
            english = [
                language
                for language in ready.languages
                if language.lower().split("-", 1)[0] == "en"
            ]
            if not english:
                await self._stop_process(force=True)
                raise STTConfigurationError(
                    "No installed Windows English speech recognizer is available"
                )
            selected_language = self._select_english_language(english)
            self._info = STTEngineInfo(
                engine=self.name,
                runtime=ready.runtime or "System.Speech.Recognition",
                available=True,
                language=selected_language,
                recognizer_name=ready.recognizer_name,
                load_time_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            LOGGER.info(
                "Windows Speech engine started",
                extra={
                    "event": "STT_ENGINE_STARTED",
                    "engine": self.name,
                    "runtime": self._info.runtime,
                    "recognizer_available": True,
                    "recognizer_language": selected_language,
                    "recognizer_name": ready.recognizer_name,
                    "worker_pid": getattr(self._process, "pid", None) if self._process else None,
                    "monotonic_ms": round(time.monotonic() * 1000, 1),
                },
            )
            LOGGER.info(
                "Windows Speech worker is ready",
                extra={
                    "event": "STT_WORKER_READY",
                    "worker_pid": getattr(self._process, "pid", None) if self._process else None,
                    "engine": self._info.engine,
                    "runtime": self._info.runtime,
                    "recognizer_name": self._info.recognizer_name,
                    "language": self._info.language,
                    "monotonic_ms": round(time.monotonic() * 1000, 1),
                },
            )
            return self._info

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
        loop = asyncio.get_running_loop()
        handle = STTEngineTurn(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            generation=generation,
            language=language,
            on_partial=on_partial,
        )
        state = _WindowsTurnState(
            handle=handle,
            start_future=loop.create_future(),
            final_future=loop.create_future(),
            generation=generation,
        )
        if key in self._turns:
            raise STTConfigurationError("STT turn is already active")
        self._turns[key] = state
        try:
            await self._send(
                WorkerRequest(
                    type="START_TURN",
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    generation=generation,
                    language=self._worker_language(language),
                )
            )
            await asyncio.wait_for(
                state.start_future,
                timeout=self.settings.stt_start_timeout_seconds,
            )
            LOGGER.info(
                "Windows Speech turn started",
                extra={
                    "event": "STT_TURN_STARTED",
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "response_id": str(response_id),
                    "generation": generation,
                },
            )
            return handle
        except TimeoutError as error:
            await self.cancel_turn(handle, generation=generation + 1)
            raise STTTimeoutError("Windows Speech turn startup timed out") from error
        except STTError:
            await self.cancel_turn(handle, generation=generation + 1)
            raise
        except Exception as error:  # noqa: BLE001
            await self.cancel_turn(handle, generation=generation + 1)
            raise STTInferenceError("Windows Speech turn could not start") from error

    async def push_audio(
        self,
        turn: STTEngineTurn,
        pcm_bytes: bytes,
        *,
        generation: int,
    ) -> None:
        state = self._state(turn)
        self._raise_state_error(state, generation)
        if len(pcm_bytes) % 2:
            raise STTAudioError("PCM16 audio must contain an even number of bytes")
        if not pcm_bytes:
            return
        LOGGER.debug(
            "Windows Speech audio received",
            extra={
                "event": "STT_AUDIO_RECEIVED",
                "session_id": str(turn.session_id),
                "turn_id": str(turn.turn_id),
                "response_id": str(turn.response_id),
                "generation": generation,
                "audio_bytes": len(pcm_bytes),
            },
        )
        await self._send(
            WorkerRequest(
                type="AUDIO",
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                response_id=turn.response_id,
                generation=generation,
                audio=pcm_bytes,
            )
        )
        state.audio_bytes += len(pcm_bytes)

    async def finish_turn(self, turn: STTEngineTurn, *, generation: int) -> STTEngineFinal:
        state = self._state(turn)
        self._raise_state_error(state, state.generation)
        if state.committed:
            raise STTConfigurationError("STT turn is already committed")
        state.committed = True
        state.generation = generation
        LOGGER.info(
            "Windows Speech commit received",
            extra={
                "event": "STT_COMMIT_RECEIVED",
                "session_id": str(turn.session_id),
                "turn_id": str(turn.turn_id),
                "response_id": str(turn.response_id),
                "generation": generation,
                "audio_duration_ms": state.audio_duration_ms,
            },
        )
        await self._send(
            WorkerRequest(
                type="COMMIT",
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                response_id=turn.response_id,
                generation=generation,
            )
        )
        try:
            return await asyncio.wait_for(
                state.final_future,
                timeout=self.settings.stt_final_timeout_seconds,
            )
        except TimeoutError as error:
            await self.cancel_turn(turn, generation=generation + 1)
            raise STTTimeoutError("Windows Speech final recognition timed out") from error

    async def cancel_turn(self, turn: STTEngineTurn, *, generation: int) -> None:
        state = self._state(turn, required=False)
        if state is None or state.cancelled:
            return
        state.cancelled = True
        state.generation = generation
        if not state.cancel_sent and self._process is not None:
            state.cancel_sent = True
            with contextlib.suppress(STTError, OSError, RuntimeError):
                await self._send(
                    WorkerRequest(
                        type="CANCEL",
                        session_id=turn.session_id,
                        turn_id=turn.turn_id,
                        response_id=turn.response_id,
                        generation=generation,
                    )
                )
        if not state.final_future.done():
            state.final_future.cancel()
        if not state.start_future.done():
            state.start_future.cancel()
        LOGGER.info(
            "Windows Speech turn cancelled",
            extra={
                "event": "STT_CANCELLED",
                "session_id": str(turn.session_id),
                "turn_id": str(turn.turn_id),
                "response_id": str(turn.response_id),
                "generation": generation,
            },
        )

    async def close_turn(self, turn: STTEngineTurn) -> None:
        state = self._state(turn, required=False)
        if state is None:
            return
        if not state.cancelled and not state.committed:
            await self.cancel_turn(turn, generation=state.generation + 1)
        self._turns.pop((turn.session_id, turn.turn_id), None)
        LOGGER.info(
            "Windows Speech turn closed",
            extra={
                "event": "STT_TURN_CLOSED",
                "session_id": str(turn.session_id),
                "turn_id": str(turn.turn_id),
                "response_id": str(turn.response_id),
                "generation": state.generation,
            },
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for state in list(self._turns.values()):
            await self.cancel_turn(state.handle, generation=state.generation + 1)
        self._turns.clear()
        await self._stop_process(force=False)
        LOGGER.info("Windows Speech engine stopped", extra={"event": "STT_ENGINE_STOPPED"})

    def _worker_command(self) -> list[str]:
        path = self.settings.stt_windows_worker_path_resolved
        if not path.is_file():
            raise STTConfigurationError(
                "Windows Speech worker executable is missing: "
                f"{path}. Build backend/windows_stt before starting the backend."
            )
        if path.suffix.lower() == ".dll":
            configured_dotnet = self.settings.stt_dotnet_path_resolved
            if configured_dotnet is not None and not configured_dotnet.is_file():
                raise STTConfigurationError(
                    f"Configured STT_DOTNET_PATH does not exist: {configured_dotnet}"
                )
            dotnet = str(configured_dotnet) if configured_dotnet else shutil.which("dotnet")
            if dotnet is None:
                raise STTConfigurationError(
                    "dotnet is required to run the configured Windows Speech worker DLL. "
                    "Set STT_DOTNET_PATH or use the self-contained published EXE."
                )
            return [dotnet, str(path)]
        return [str(path)]

    def _worker_language(self, language: str | None) -> str:
        if language is None or language == "en":
            return self.settings.stt_windows_language
        if language == "en-us":
            return "en-US"
        return language

    def _select_english_language(self, languages: list[str]) -> str:
        preferred = self.settings.stt_windows_language.lower()
        for language in languages:
            if language.lower() == preferred:
                return language
        return languages[0]

    def _state(self, turn: STTEngineTurn, *, required: bool = True) -> _WindowsTurnState | None:
        state = self._turns.get((turn.session_id, turn.turn_id))
        if state is None and required:
            raise STTConfigurationError("STT turn is not active")
        return state

    def _raise_state_error(self, state: _WindowsTurnState, generation: int) -> None:
        if state.error is not None:
            if isinstance(state.error, STTError):
                raise state.error
            raise STTInferenceError("Windows Speech worker reported an error") from state.error
        if state.cancelled or generation != state.generation:
            raise STTCancelledError("STT turn was cancelled or superseded")

    async def _send(self, request: WorkerRequest) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise STTConfigurationError("Windows Speech worker is not running")
        if self._worker_error is not None:
            raise self._worker_error
        data = request.to_line()
        async with self._write_lock:
            try:
                process.stdin.write(data)
                await asyncio.wait_for(
                    process.stdin.drain(),
                    timeout=self.settings.stt_worker_timeout_seconds,
                )
            except (TimeoutError, BrokenPipeError, ConnectionError) as error:
                worker_error = STTInferenceError("Windows Speech worker is unavailable")
                self._worker_error = worker_error
                raise worker_error from error

    async def _reader_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    response = parse_response(line)
                except WorkerProtocolError as error:
                    await self._fail_worker(STTInferenceError(str(error)))
                    break
                await self._route_response(response)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - isolate worker reader failures
            await self._fail_worker(STTInferenceError("Windows Speech worker reader failed"))
            LOGGER.exception("Windows Speech worker reader failed")
        finally:
            if self._process is process and self._worker_error is None and not self._stopping:
                await self._fail_worker(
                    STTInferenceError("Windows Speech worker exited unexpectedly")
                )

    async def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                LOGGER.debug(
                    "Windows Speech worker diagnostic",
                    extra={
                        "event": "STT_WORKER_DIAGNOSTIC",
                        "diagnostic": line.decode(errors="replace").strip(),
                    },
                )
        except asyncio.CancelledError:
            return

    async def _route_response(self, response: WorkerResponse) -> None:
        if response.type == "READY":
            if self._ready_future is not None and not self._ready_future.done():
                if response.available is False:
                    self._ready_future.set_exception(
                        STTConfigurationError(response.message or "Windows recognizer unavailable")
                    )
                else:
                    self._ready_future.set_result(response)
            return
        if response.type == "SHUTDOWN_ACK":
            return
        if response.type == "ERROR" and response.session_id is None:
            error = self._response_error(response)
            if self._ready_future is not None and not self._ready_future.done():
                self._ready_future.set_exception(error)
            else:
                await self._fail_worker(error)
            return
        state = self._find_correlated_state(response)
        if state is None:
            LOGGER.warning(
                "Ignoring uncorrelated Windows Speech worker result",
                extra={"event": "STT_STALE_RESULT_IGNORED", "worker_type": response.type},
            )
            return
        if response.type == "TURN_READY":
            if not state.start_future.done():
                state.start_future.set_result(response)
            return
        if response.type == "PARTIAL":
            if (
                state.cancelled
                or state.committed
                or response.generation != state.generation
                or not response.text
                or not response.text.strip()
            ):
                return
            received_monotonic = time.monotonic()
            try:
                await state.handle.on_partial(
                    STTEnginePartial(
                        session_id=state.handle.session_id,
                        turn_id=state.handle.turn_id,
                        response_id=state.handle.response_id,
                        generation=response.generation or state.generation,
                        text=response.text.strip(),
                        language=response.language or state.handle.language,
                        confidence=response.confidence,
                        audio_duration_ms=response.audio_duration_ms or state.audio_duration_ms,
                        timestamp_ms=response.timestamp_ms or int(time.time() * 1000),
                        monotonic_timestamp=received_monotonic,
                    )
                )
            except Exception:  # noqa: BLE001 - client event mapping cannot kill worker reader
                LOGGER.exception("Windows Speech partial callback failed")
            return
        if response.type == "FINAL":
            if state.cancelled or response.generation != state.generation:
                return
            received_monotonic = time.monotonic()
            final = STTEngineFinal(
                session_id=state.handle.session_id,
                turn_id=state.handle.turn_id,
                response_id=state.handle.response_id,
                generation=response.generation or state.generation,
                text=(response.text or "").strip(),
                language=response.language or state.handle.language,
                confidence=response.confidence,
                timestamp_ms=response.timestamp_ms or int(time.time() * 1000),
                monotonic_timestamp=received_monotonic,
                inference_duration_ms=0.0,
            )
            if not state.final_future.done():
                state.final_future.set_result(final)
            return
        if response.type == "CANCELLED":
            state.cancelled = True
            if not state.final_future.done():
                state.final_future.cancel()
            return
        if response.type == "ERROR":
            error = self._response_error(response)
            LOGGER.error(
                "Windows Speech worker returned an error",
                extra={
                    "event": "STT_ERROR",
                    "session_id": str(state.handle.session_id),
                    "turn_id": str(state.handle.turn_id),
                    "response_id": str(state.handle.response_id),
                    "generation": state.generation,
                    "code": response.code,
                    "error_message": response.message,
                },
            )
            state.error = error
            if not state.start_future.done():
                state.start_future.set_exception(error)
            if not state.final_future.done():
                state.final_future.set_exception(error)

    def _find_correlated_state(self, response: WorkerResponse) -> _WindowsTurnState | None:
        if response.session_id is None or response.turn_id is None or response.response_id is None:
            return None
        state = self._turns.get((response.session_id, response.turn_id))
        if (
            state is None
            or state.handle.response_id != response.response_id
            or response.generation != state.generation
        ):
            return None
        return state

    def _response_error(self, response: WorkerResponse) -> STTError:
        code = response.code or "stt_worker_error"
        message = response.message or "Windows Speech worker error"
        if code in {"unsupported_language", "recognizer_unavailable", "invalid_format"}:
            return STTConfigurationError(message)
        if code in {"worker_timeout", "recognizer_timeout"}:
            return STTTimeoutError(message)
        return STTInferenceError(message)

    async def _fail_worker(self, error: STTError) -> None:
        if self._worker_error is None:
            self._worker_error = error
            LOGGER.error(
                "Windows Speech worker failed",
                extra={"event": "STT_ERROR", "code": error.code, "error_message": str(error)},
            )
        if self._ready_future is not None and not self._ready_future.done():
            self._ready_future.set_exception(error)
        for state in self._turns.values():
            state.error = error
            if not state.start_future.done():
                state.start_future.set_exception(error)
            if not state.final_future.done():
                state.final_future.set_exception(error)

    async def _stop_process(self, *, force: bool) -> None:
        process = self._process
        if process is None:
            return
        self._stopping = True
        if not force and process.returncode is None:
            with contextlib.suppress(STTError, OSError, RuntimeError):
                await self._send(WorkerRequest(type="SHUTDOWN"))
        if process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=self.settings.stt_worker_timeout_seconds
                )
            except TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._process = None
        self._reader_task = None
        self._stderr_task = None
        self._stopping = False
