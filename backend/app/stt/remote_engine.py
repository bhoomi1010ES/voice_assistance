"""Remote HTTP transcription engine for bounded voice turns."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
import uuid
import wave
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.services.latency_trace import LatencyTracer, latency_span
from app.stt.base import (
    EnginePartialCallback,
    STTAudioError,
    STTAudioTooLongError,
    STTAuthenticationError,
    STTCancelledError,
    STTConfigurationError,
    STTEmptyAudioError,
    STTEmptyTranscriptError,
    STTEngine,
    STTEngineFinal,
    STTEngineInfo,
    STTEngineTurn,
    STTError,
    STTInferenceError,
    STTNetworkError,
    STTRateLimitError,
    STTTimeoutError,
    STTUnavailableError,
)

LOGGER = logging.getLogger("voice-assistance-backend")


@dataclass
class _RemoteTurnState:
    handle: STTEngineTurn
    audio: bytearray = field(default_factory=bytearray)
    audio_samples: int = 0
    generation: int = 0
    cancelled: bool = False
    finalized: bool = False
    request_task: asyncio.Task[STTEngineFinal] | None = None


class RemoteTranscriptionEngine(STTEngine):
    """Submit one exact PCM16 turn to a configured transcription API.

    The current endpoint is a commit-time HTTP transcription contract. It does
    not manufacture partials; partials are emitted only if a future transport
    explicitly adds a genuine streaming result path.
    """

    name = "remote"
    buffers_audio = True

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._info: STTEngineInfo | None = None
        self._latency_tracer = LatencyTracer()
        self._initialize_lock = asyncio.Lock()
        self._turns_lock = asyncio.Lock()
        self._turns: dict[tuple[uuid.UUID, uuid.UUID], _RemoteTurnState] = {}
        self._closed = False

    async def initialize(self) -> STTEngineInfo:
        if self._closed:
            raise STTConfigurationError("remote STT engine is closed")
        if self._info is not None:
            return self._info

        async with self._initialize_lock:
            if self._info is not None:
                return self._info
            try:
                endpoint = self.settings.stt_api_url_resolved
            except RuntimeError as error:
                raise STTConfigurationError(str(error)) from error
            if self.settings.stt_api_key is None:
                raise STTConfigurationError("STT_API_KEY is required when STT_ENGINE=remote")
            if not self.settings.stt_api_auth_header.strip():
                raise STTConfigurationError("STT_API_AUTH_HEADER must not be empty")
            if (
                "\r" in self.settings.stt_api_auth_header
                or "\n" in self.settings.stt_api_auth_header
            ):
                raise STTConfigurationError("STT_API_AUTH_HEADER contains invalid characters")
            self._validate_audio_format()
            timeout = httpx.Timeout(
                self.settings.stt_api_timeout_seconds,
                connect=self.settings.stt_api_connect_timeout_seconds,
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=self.settings.stt_max_active_turns),
                transport=self._transport,
            )
            self._info = STTEngineInfo(
                engine=self.name,
                runtime="httpx",
                available=True,
                language=self.settings.stt_api_language,
                recognizer_name=urlsplit(endpoint).netloc,
            )
            LOGGER.info(
                "Remote STT engine ready",
                extra={
                    "event": "STT_ENGINE_STARTED",
                    "engine": self.name,
                    "runtime": "httpx",
                    "endpoint_host": urlsplit(endpoint).netloc,
                    "language": self.settings.stt_api_language,
                    "partials_supported": False,
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
        async with self._turns_lock:
            if self._closed:
                raise STTConfigurationError("remote STT engine is closed")
            key = (session_id, turn_id)
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
            self._turns[key] = _RemoteTurnState(
                handle=handle,
                generation=generation,
            )
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
        max_bytes = (
            self.settings.stt_max_audio_seconds
            * self.settings.voice_sample_rate_hz
            * self.settings.voice_channels
            * 2
        )
        if len(state.audio) + len(pcm_bytes) > max_bytes:
            raise STTAudioTooLongError("STT turn audio limit exceeded")
        state.audio.extend(pcm_bytes)
        state.audio_samples += len(pcm_bytes) // 2

    async def finish_turn(self, turn: STTEngineTurn, *, generation: int) -> STTEngineFinal:
        state = self._state(turn)
        if state.cancelled:
            raise STTCancelledError("STT turn was cancelled")
        if state.finalized:
            raise STTConfigurationError("STT turn is already finalized")
        state.finalized = True
        state.generation = generation
        if not state.audio:
            raise STTEmptyAudioError("STT turn did not contain audio")

        state.request_task = asyncio.create_task(
            self._transcribe(state, generation),
            name=f"remote-stt-final-{turn.turn_id}",
        )
        try:
            return await state.request_task
        except asyncio.CancelledError as error:
            if state.cancelled:
                raise STTCancelledError("STT turn was cancelled") from error
            raise
        finally:
            state.request_task = None

    async def cancel_turn(self, turn: STTEngineTurn, *, generation: int) -> None:
        state = self._state(turn, required=False)
        if state is None:
            return
        state.cancelled = True
        state.generation = generation
        if state.request_task is not None and not state.request_task.done():
            state.request_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, STTError):
                await state.request_task
        state.audio.clear()
        async with self._turns_lock:
            self._turns.pop((turn.session_id, turn.turn_id), None)

    async def close_turn(self, turn: STTEngineTurn) -> None:
        async with self._turns_lock:
            self._turns.pop((turn.session_id, turn.turn_id), None)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for state in list(self._turns.values()):
            state.cancelled = True
            if state.request_task is not None and not state.request_task.done():
                state.request_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, STTError):
                    await state.request_task
            state.audio.clear()
        self._turns.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        LOGGER.info("Remote STT engine stopped", extra={"event": "STT_ENGINE_STOPPED"})

    async def _transcribe(self, state: _RemoteTurnState, generation: int) -> STTEngineFinal:
        client = self._client
        if client is None:
            raise STTConfigurationError("remote STT client is not initialized")
        if state.cancelled or generation != state.generation:
            raise STTCancelledError("STT turn is no longer active")
        wav_bytes = self._to_wav(bytes(state.audio))
        data: dict[str, str] = {
            "response_format": self.settings.stt_api_response_format,
        }
        if self.settings.stt_api_model:
            data["model"] = self.settings.stt_api_model
        language = state.handle.language or self.settings.stt_api_language
        if language:
            data["language"] = language
        request_start_wall_ms = int(time.time() * 1000)
        request_start_monotonic_ns = time.perf_counter_ns()
        request_start_monotonic_ms = round(request_start_monotonic_ns / 1_000_000, 3)
        audio_duration_ms = round(
            state.audio_samples / self.settings.voice_sample_rate_hz * 1000,
            1,
        )
        with latency_span(
            self._latency_tracer.emit,
            component="sync_logging",
            event="stt_request_log_write",
            session_id=state.handle.session_id,
            turn_id=state.handle.turn_id,
            response_id=state.handle.response_id,
        ):
            LOGGER.info(
                "Remote STT request started",
                extra={
                    "event": "STT_REMOTE_REQUEST_STARTED",
                    "session_id": str(state.handle.session_id),
                    "turn_id": str(state.handle.turn_id),
                    "response_id": str(state.handle.response_id),
                    "generation": generation,
                    "endpoint_host": urlsplit(self.settings.stt_api_url_resolved).netloc,
                    "audio_bytes": len(state.audio),
                    "audio_duration_ms": audio_duration_ms,
                    "request_start_timestamp_ms": request_start_wall_ms,
                    "request_start_monotonic_ms": request_start_monotonic_ms,
                },
            )
        self._latency_tracer.emit(
            session_id=state.handle.session_id,
            turn_id=state.handle.turn_id,
            response_id=state.handle.response_id,
            component="stt",
            event="stt_request_start",
            monotonic_ns=request_start_monotonic_ns,
            metadata={"audio_bytes": len(state.audio), "audio_duration_ms": audio_duration_ms},
        )
        self._latency_tracer.emit(
            session_id=state.handle.session_id,
            turn_id=state.handle.turn_id,
            response_id=state.handle.response_id,
            component="stt",
            event="stt_remote_request_sent",
            monotonic_ms=request_start_monotonic_ms,
        )
        started_ns = time.perf_counter_ns()
        self._latency_tracer.emit(
            session_id=state.handle.session_id,
            turn_id=state.handle.turn_id,
            response_id=state.handle.response_id,
            component="stt",
            event="stt_request_started",
            monotonic_ns=started_ns,
            metadata={"audio_bytes": len(state.audio), "audio_duration_ms": audio_duration_ms},
        )

        transport_started: dict[str, int] = {}

        async def http_trace(name: str, info: dict[str, Any]) -> None:
            event_ns = time.perf_counter_ns()
            if name == "connection.connect_tcp.started":
                transport_started["tcp"] = event_ns
                event_name = "stt_tcp_connect_started"
                duration = None
                metadata: dict[str, Any] = {}
            elif name == "connection.connect_tcp.complete":
                prior_ns = transport_started.pop("tcp", None)
                event_name = "stt_tcp_connect_completed"
                duration = (event_ns - prior_ns) / 1_000_000 if prior_ns is not None else None
                metadata = {}
            elif name in {
                "http11.send_request_headers.started",
                "http2.send_request_headers.started",
            }:
                event_name = "stt_connection_acquired"
                duration = None
                metadata = {"connection_pool_wait_separately_measured": False}
            elif name == "connection.start_tls.started":
                transport_started["tls"] = event_ns
                event_name = "stt_tls_started"
                duration = None
                metadata = {}
            elif name == "connection.start_tls.complete":
                prior_ns = transport_started.pop("tls", None)
                event_name = "stt_tls_completed"
                duration = (event_ns - prior_ns) / 1_000_000 if prior_ns is not None else None
                metadata = {}
            elif name in {
                "http11.receive_response_headers.complete",
                "http2.receive_response_headers.complete",
            }:
                returned = info.get("return_value")
                status_code = None
                if isinstance(returned, tuple) and returned:
                    candidate = returned[1] if name.startswith("http11") else returned[0]
                    if isinstance(candidate, int):
                        status_code = candidate
                event_name = "stt_response_headers_received"
                duration = None
                metadata = {"status_code": status_code}
            else:
                return
            self._latency_tracer.emit(
                session_id=state.handle.session_id,
                turn_id=state.handle.turn_id,
                response_id=state.handle.response_id,
                component="stt_provider",
                event=event_name,
                monotonic_ns=event_ns,
                duration_ms=duration,
                metadata=metadata,
            )

        try:
            response = await client.post(
                self.settings.stt_api_url_resolved,
                headers=self._auth_headers(),
                data=data,
                files={
                    self.settings.stt_api_file_field: (
                        self.settings.stt_api_filename,
                        wav_bytes,
                        "audio/wav",
                    )
                },
                extensions={"trace": http_trace},
            )
        except httpx.TimeoutException as error:
            raise STTTimeoutError("remote STT request timed out") from error
        except httpx.RequestError as error:
            raise STTNetworkError("remote STT request failed") from error
        response_received_ns = time.perf_counter_ns()
        duration_ms = round((response_received_ns - started_ns) / 1_000_000, 3)
        if len(response.content) > self.settings.stt_api_max_response_bytes:
            raise STTUnavailableError("remote STT response exceeded the configured size limit")
        self._raise_for_status(response)
        text = self._extract_text(response)
        completed_ns = time.perf_counter_ns()
        completed = completed_ns / 1_000_000_000
        response_wall_ms = int(time.time() * 1000)
        response_monotonic_ms = round(response_received_ns / 1_000_000, 3)
        with latency_span(
            self._latency_tracer.emit,
            component="sync_logging",
            event="stt_response_log_write",
            session_id=state.handle.session_id,
            turn_id=state.handle.turn_id,
            response_id=state.handle.response_id,
        ):
            LOGGER.info(
                "Remote STT transcription completed",
                extra={
                    "event": "STT_REMOTE_FINAL",
                    "session_id": str(state.handle.session_id),
                    "turn_id": str(state.handle.turn_id),
                    "response_id": str(state.handle.response_id),
                    "generation": generation,
                    "endpoint_host": urlsplit(self.settings.stt_api_url_resolved).netloc,
                    "request_id": response.headers.get("x-request-id"),
                    "status_code": response.status_code,
                    "audio_bytes": len(state.audio),
                    "audio_duration_ms": audio_duration_ms,
                    "request_start_timestamp_ms": request_start_wall_ms,
                    "request_start_monotonic_ms": request_start_monotonic_ms,
                    "response_timestamp_ms": response_wall_ms,
                    "response_monotonic_ms": response_monotonic_ms,
                    "request_duration_ms": duration_ms,
                    "remote_request_latency_ms": duration_ms,
                },
            )
        self._latency_tracer.emit(
            session_id=state.handle.session_id,
            turn_id=state.handle.turn_id,
            response_id=state.handle.response_id,
            component="stt",
            event="stt_response_received",
            monotonic_ns=response_received_ns,
            duration_ms=duration_ms,
            metadata={"status_code": response.status_code},
        )
        self._latency_tracer.emit(
            session_id=state.handle.session_id,
            turn_id=state.handle.turn_id,
            response_id=state.handle.response_id,
            component="stt",
            event="stt_final",
            monotonic_ns=completed_ns,
        )
        self._latency_tracer.emit(
            session_id=state.handle.session_id,
            turn_id=state.handle.turn_id,
            response_id=state.handle.response_id,
            component="stt",
            event="stt_final_parsed",
            monotonic_ns=completed_ns,
            metadata={"text_characters": len(text)},
        )
        return STTEngineFinal(
            session_id=state.handle.session_id,
            turn_id=state.handle.turn_id,
            response_id=state.handle.response_id,
            generation=generation,
            text=text,
            language=language,
            confidence=None,
            timestamp_ms=int(time.time() * 1000),
            monotonic_timestamp=completed,
            inference_duration_ms=duration_ms,
        )

    def _auth_headers(self) -> dict[str, str]:
        if self.settings.stt_api_key is None:
            raise STTConfigurationError("STT_API_KEY is required when STT_ENGINE=remote")
        value = self.settings.stt_api_key.get_secret_value()
        scheme = self.settings.stt_api_auth_scheme.strip()
        token = f"{scheme} {value}" if scheme else value
        return {self.settings.stt_api_auth_header.strip(): token}

    @staticmethod
    def _extract_text(response: httpx.Response) -> str:
        try:
            payload: Any = response.json()
        except ValueError as error:
            raise STTUnavailableError("remote STT returned invalid JSON") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise STTUnavailableError("remote STT response did not contain text")
        text = payload["text"].strip()
        if not text:
            raise STTEmptyTranscriptError("remote STT returned an empty transcript")
        return text

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise STTAuthenticationError(f"remote STT authentication failed (HTTP {status})")
        if status == 413:
            raise STTAudioTooLongError("remote STT rejected the audio size (HTTP 413)")
        if status == 429:
            raise STTRateLimitError("remote STT rate limited the request (HTTP 429)")
        if status == 408 or status >= 500:
            raise STTUnavailableError(f"remote STT service unavailable (HTTP {status})")
        raise STTInferenceError(f"remote STT request rejected (HTTP {status})")

    def _to_wav(self, pcm_bytes: bytes) -> bytes:
        self._validate_audio_format()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(self.settings.voice_channels)
            wav.setsampwidth(2)
            wav.setframerate(self.settings.voice_sample_rate_hz)
            wav.writeframes(pcm_bytes)
        return output.getvalue()

    def _validate_audio_format(self) -> None:
        if self.settings.voice_sample_rate_hz != 16_000:
            raise STTConfigurationError("remote STT requires 16 kHz PCM audio")
        if self.settings.voice_channels != 1:
            raise STTConfigurationError("remote STT requires mono PCM audio")

    def _state(self, turn: STTEngineTurn, *, required: bool = True) -> _RemoteTurnState | None:
        state = self._turns.get((turn.session_id, turn.turn_id))
        if state is None and required:
            raise STTConfigurationError("unknown remote STT turn")
        return state

    @staticmethod
    def _empty_final(turn: STTEngineTurn, generation: int) -> STTEngineFinal:
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
