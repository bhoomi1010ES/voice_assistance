from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import struct
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.async_utils import await_cleanup
from app.core.clock import Clock, DeviceEpochClock, SystemClock
from app.core.config import Settings
from app.llm.context import (
    VOICE_SYSTEM_PROMPT_VERSION,
    build_voice_llm_request,
    classify_voice_tool_choice,
)
from app.llm.errors import LLMError
from app.llm.service import LLMService
from app.llm.tool_loop import (
    LLMToolLoop,
    ToolExecutionContext,
    ToolIdempotencyStore,
    ToolRegistry,
    create_default_tool_registry,
)
from app.llm.types import LLMEvent, LLMMessage, LLMRole, LLMUsage
from app.llm.wait_status import classify_wait_status
from app.memory.context import assemble_context
from app.memory.providers import MemoryProviderError
from app.memory.repository import MemoryRepository
from app.memory.tool_tools import build_explicit_memory_save_call
from app.models import ConversationTurn, User, VoiceSession
from app.services.audit import record_audit
from app.services.auth import (
    AuthConfigurationError,
    AuthenticationError,
    AuthPrincipal,
    AuthService,
)
from app.services.conversation_logging import (
    ConversationLogger,
    TimingPoint,
    build_timing_payload,
)
from app.services.device_time import (
    DeviceTimeContext,
    build_device_time_context,
    format_utc_offset,
    timezone_for_request,
    valid_timezone,
)
from app.services.latency_trace import LatencyTracer, latency_span
from app.services.task_due_dates import format_local_due_at
from app.services.tool_idempotency import PostgresToolIdempotencyStore
from app.services.voice_confirmation import (
    PendingConfirmation,
    RedisVoiceConfirmationStore,
    VoiceConfirmationStore,
    resolve_confirmation,
)
from app.services.voice_persistence import VoicePersistence
from app.services.voice_registry import (
    VoiceRegistry,
    VoiceRegistryError,
    VoiceRegistryOwner,
    VoiceSessionConflict,
)
from app.stt.base import STTCancelledError, STTEmptyTranscriptError, STTError
from app.stt.service import (
    STTService,
    STTTranscriptEvent,
    STTTranscriptResult,
    STTTurn,
)
from app.tts.base import TTSCancelledError, TTSError
from app.tts.segmentation import SentenceSegmenter
from app.tts.service import TTSService
from app.websocket.binary import BinaryPcmFrame, BinaryProtocolError, decode_pcm_frame
from app.websocket.cancellation import CancellationGuard
from app.websocket.protocol import (
    AudioCommitMessage,
    ClientPingMessage,
    ConfirmationResolveMessage,
    ControlMessageType,
    ConversationResetMessage,
    DeviceTimeContextPayload,
    ProtocolError,
    ResponseAbortAllMessage,
    ResponseCancelMessage,
    ResponseRetryMessage,
    SessionEndMessage,
    SessionStartMessage,
    TurnStartMessage,
    parse_control_message,
    server_event,
)
from app.websocket.state import (
    SequenceError,
    StateTransitionError,
    VoiceConnectionState,
    VoiceState,
)

LOGGER = logging.getLogger("voice-assistance-backend")
TTS_FRAME_MAGIC = b"VTT1"
TTS_FRAME_VERSION = 1
TTS_FRAME_START = 1
TTS_FRAME_END = 2
TTS_PCM_BYTES_PER_SAMPLE = 2
TTS_STARTUP_PREBUFFER_MS = 200


def tts_pacing_delay_seconds(
    *,
    sent_pcm_bytes: int,
    started_at: float,
    now: float,
    sample_rate_hz: int,
) -> float:
    """Return the delay needed to keep a bursty PCM stream near real time.

    The first startup buffer is sent immediately. After that, frames are
    released at their playback duration so a fast provider cannot overflow
    the bounded Android jitter queue.
    """

    prebuffer_bytes = sample_rate_hz * TTS_PCM_BYTES_PER_SAMPLE * TTS_STARTUP_PREBUFFER_MS // 1_000
    paced_bytes = max(0, sent_pcm_bytes - prebuffer_bytes)
    target_elapsed = paced_bytes / (sample_rate_hz * TTS_PCM_BYTES_PER_SAMPLE)
    elapsed = max(0.0, now - started_at)
    return max(0.0, target_elapsed - elapsed)


@dataclass
class VoiceGatewayStats:
    frames_received: int = 0
    frames_accepted: int = 0
    bytes_received: int = 0
    queue_high_water_mark: int = 0
    queue_overflow_count: int = 0
    duplicate_frame_count: int = 0
    gap_count: int = 0
    malformed_frame_count: int = 0
    control_error_count: int = 0
    error_count: int = 0
    heartbeat_count: int = 0
    cancellation_count: int = 0
    reconnect: bool = False


@dataclass
class TurnTimingState:
    points: dict[str, TimingPoint]
    tts: dict[str, Any] | None = None
    response_id: uuid.UUID | None = None


class VoiceGateway:
    """Own one authenticated voice WebSocket and its bounded lifecycle."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        db: AsyncSession,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        principal: AuthPrincipal,
        access_token: str,
        stt_service: STTService,
        llm_service: LLMService,
        tts_service: TTSService | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_idempotency_store: ToolIdempotencyStore | None = None,
        confirmation_store: VoiceConfirmationStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.websocket = websocket
        self.db = db
        self.session_factory = session_factory
        self.settings = settings
        self.clock = clock or SystemClock()
        self.principal = principal
        self.access_token = access_token
        self.auth_service = AuthService(settings)
        self.stt_service = stt_service
        self.llm_service = llm_service
        self.tts_service = tts_service
        self.tool_registry = tool_registry or create_default_tool_registry()
        if tool_registry is None and (
            settings.memory_retrieval_mode != "off" or settings.memory_write_enabled
        ):
            from app.memory.tool_tools import register_memory_tools

            register_memory_tools(self.tool_registry, allow_write=settings.memory_write_enabled)
        self.tool_loop = LLMToolLoop(
            settings,
            llm_service,
            self.tool_registry,
            idempotency_store=tool_idempotency_store or PostgresToolIdempotencyStore(db),
        )
        self.persistence = VoicePersistence()
        self.conversation_logger = ConversationLogger(root_dir=settings.conversation_log_dir)
        self.registry = VoiceRegistry(
            websocket.app.state.infrastructure.redis,
            ttl_seconds=settings.voice_max_session_seconds + settings.voice_reconnect_grace_seconds,
            lease_ttl_seconds=(
                settings.voice_heartbeat_timeout_seconds + settings.voice_reconnect_grace_seconds
            ),
        )
        self.confirmation_store = confirmation_store or RedisVoiceConfirmationStore(
            websocket.app.state.infrastructure.redis,
            ttl_seconds=settings.voice_confirmation_ttl_seconds,
        )
        self.owner = VoiceRegistryOwner(
            user_id=principal.user_id,
            device_id=principal.device_id,
            auth_session_id=principal.session_id,
            connection_id=uuid.uuid4(),
        )
        self.state = VoiceConnectionState()
        self.state.authenticate()
        self.cancel_guard = CancellationGuard()
        self.stats = VoiceGatewayStats()
        self.queue: asyncio.Queue[ControlMessageType | BinaryPcmFrame] = asyncio.Queue(
            maxsize=settings.voice_queue_capacity_frames
        )
        self._queued_audio_commits: dict[int, tuple[int, uuid.UUID, uuid.UUID]] = {}
        self.voice_session: VoiceSession | None = None
        self.active_turn: ConversationTurn | None = None
        self.stt_turn: STTTurn | None = None
        self._stt_event_task: asyncio.Task[None] | None = None
        self._stt_finalize_task: asyncio.Task[None] | None = None
        self._retry_response_task: asyncio.Task[None] | None = None
        self._tts_tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._tts_queues: dict[uuid.UUID, asyncio.Queue[str | None]] = {}
        self._pending_turn_start: TurnStartMessage | None = None
        self._session_start_message: SessionStartMessage | None = None
        self._wait_phrase_index = 0
        self._turn_start_event_ids: set[uuid.UUID] = set()
        self._cancelled_response_ids: set[uuid.UUID] = set()
        self._turn_timings: dict[uuid.UUID, TurnTimingState] = {}
        self.latency_tracer = LatencyTracer()
        self._stt_finalize_cancel_requested = False
        self._stt_enabled = False
        self._stt_language: str | None = None
        # Keep session identity and counters as scalar gateway state. ORM
        # attributes can be expired by rollback, and reading an expired
        # attribute from an async task would attempt implicit IO and raise
        # MissingGreenlet.
        self._session_id: uuid.UUID | None = None
        self._session_total_frames = 0
        self._session_total_bytes = 0
        self._session_client_metadata: dict[str, Any] | None = None
        self._device_time_context: DeviceTimeContext | None = None
        self._device_clock: Clock | None = None
        self._active_timezone_source = "device"
        self._active_timezone = "UTC"
        self._last_response_id: uuid.UUID | None = None
        self._response_turn_id: uuid.UUID | None = None
        self._connection_started = time.monotonic()
        self._session_started = self._connection_started
        self._turn_started: float | None = None
        self._last_activity = self._connection_started
        self._last_ping = self._connection_started
        self._last_auth_check = self._connection_started
        self._closing = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._processor_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._close_code: int | None = None
        self._close_reason: str | None = None
        self._session_status = "disconnected"
        self._finalized = False
        self._connection_close_logged = False

    async def run(self) -> None:
        LOGGER.info(
            "Voice WebSocket connection opened",
            extra={
                "event": "voice.connection.opened",
                "backend_pid": os.getpid(),
                "monotonic_ms": round(time.monotonic() * 1000, 1),
            },
        )
        self._processor_task = asyncio.create_task(self._process_loop())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        receive_task = asyncio.create_task(self._receive_loop())
        self._receive_task = receive_task
        gateway_tasks = {
            receive_task,
            self._processor_task,
            self._watchdog_task,
        }
        try:
            done, pending = await asyncio.wait(
                gateway_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    raise task.exception()
            for task in pending:
                task.cancel()
        finally:
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await await_cleanup(self.shutdown())
            finally:
                try:
                    with contextlib.suppress(asyncio.CancelledError):
                        await await_cleanup(
                            self.websocket.close(
                                code=self._close_code or 1000,
                                reason=(self._close_reason or "connection_closed")[:120],
                            )
                        )
                except (RuntimeError, WebSocketDisconnect):
                    pass

    async def _receive_loop(self) -> None:
        while not self._closing.is_set():
            try:
                message = await self.websocket.receive()
            except WebSocketDisconnect as disconnect:
                self._close_code = disconnect.code
                self._close_reason = "client_disconnect"
                self._log_connection_closed()
                return

            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                self._close_code = message.get("code")
                self._close_reason = "client_disconnect"
                self._log_connection_closed()
                return

            if message_type == "websocket.receive" and message.get("bytes") is not None:
                await self._receive_binary(message["bytes"])
            elif message_type == "websocket.receive" and message.get("text") is not None:
                await self._receive_control(message["text"])
            else:
                await self._protocol_failure("unsupported_websocket_message")
                return

    async def _receive_binary(self, raw: bytes) -> None:
        self._last_activity = time.monotonic()
        try:
            frame = decode_pcm_frame(
                raw,
                max_frame_bytes=self.settings.voice_max_frame_bytes,
                expected_payload_bytes=self.settings.voice_frame_bytes,
            )
        except BinaryProtocolError as error:
            self.stats.malformed_frame_count += 1
            await self._protocol_failure(
                error.code,
                close_code=1009 if error.code == "frame_too_large" else 1002,
            )
            return
        self.stats.frames_received += 1
        if not await self._enqueue(frame):
            return

    async def _receive_control(self, raw: str) -> None:
        self._last_activity = time.monotonic()
        try:
            message = parse_control_message(
                raw,
                max_bytes=self.settings.voice_max_control_bytes,
            )
        except ProtocolError as error:
            self.stats.control_error_count += 1
            await self._protocol_failure(
                str(error),
                close_code=1009 if str(error) == "control_message_too_large" else 1002,
            )
            return
        if isinstance(message, ClientPingMessage):
            self._last_ping = time.monotonic()
        await self._enqueue(message)

    async def _enqueue(self, item: ControlMessageType | BinaryPcmFrame) -> bool:
        if self._closing.is_set():
            return False
        commit_queue_timing: tuple[int, uuid.UUID, uuid.UUID, int] | None = None
        if isinstance(item, AudioCommitMessage) and self.active_turn is not None:
            commit_queue_timing = (
                time.perf_counter_ns(),
                self.active_turn.id,
                self.active_turn.response_id,
                self.queue.qsize(),
            )
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.stats.queue_overflow_count += 1
            await self._protocol_failure("voice_queue_overflow", close_code=1013)
            return False
        self.stats.queue_high_water_mark = max(self.stats.queue_high_water_mark, self.queue.qsize())
        if commit_queue_timing is not None:
            queued_ns, turn_id, response_id, queue_depth = commit_queue_timing
            self._queued_audio_commits[id(item)] = (queued_ns, turn_id, response_id)
            self._trace_latency(
                turn_id=turn_id,
                response_id=response_id,
                component="gateway_queue",
                event="gateway_commit_queue_wait_started",
                monotonic_ns=queued_ns,
                metadata={
                    "queue": "gateway_control_and_audio",
                    "queue_depth_before_enqueue": queue_depth,
                    "queue_capacity": self.queue.maxsize,
                },
            )
        return True

    async def _process_loop(self) -> None:
        while not self._closing.is_set():
            try:
                item = await self.queue.get()
            except asyncio.CancelledError:
                return
            try:
                if isinstance(item, AudioCommitMessage):
                    queued = self._queued_audio_commits.pop(id(item), None)
                    if queued is not None:
                        queued_ns, turn_id, response_id = queued
                        dequeued_ns = time.perf_counter_ns()
                        queue_wait_ms = (dequeued_ns - queued_ns) / 1_000_000
                        self._trace_latency(
                            turn_id=turn_id,
                            response_id=response_id,
                            component="gateway_queue",
                            event="gateway_commit_queue_wait_completed",
                            monotonic_ns=dequeued_ns,
                            duration_ms=queue_wait_ms,
                            metadata={
                                "queue": "gateway_control_and_audio",
                                "queue_wait_ms": queue_wait_ms,
                                "queue_depth_after_dequeue": self.queue.qsize(),
                                "queue_capacity": self.queue.maxsize,
                            },
                        )
                if isinstance(item, BinaryPcmFrame):
                    await self._handle_binary(item)
                else:
                    await self._handle_control(item)
            except SequenceError as error:
                if error.kind == "duplicate":
                    self.stats.duplicate_frame_count += 1
                else:
                    self.stats.gap_count += 1
                await self._protocol_failure(
                    f"sequence_{error.kind}",
                    close_code=1002,
                )
            except StateTransitionError:
                await self._protocol_failure("invalid_voice_state", close_code=1002)
            except STTError as error:
                await self._fail_active_turn(error.code)
            except PermissionError:
                await self._protocol_failure("voice_ownership_violation", close_code=1008)
            except (VoiceRegistryError, VoiceSessionConflict):
                await self._protocol_failure("voice_registry_unavailable", close_code=1013)
            except SQLAlchemyError:
                await self.db.rollback()
                self.stats.error_count += 1
                await self._protocol_failure("voice_persistence_unavailable", close_code=1011)
            finally:
                self.queue.task_done()

    async def _handle_control(self, message: ControlMessageType) -> None:
        if isinstance(message, SessionStartMessage):
            await self._handle_session_start(message)
        elif isinstance(message, TurnStartMessage):
            await self._handle_turn_start(message)
        elif isinstance(message, AudioCommitMessage):
            await self._handle_audio_commit(message)
        elif isinstance(message, ResponseCancelMessage):
            await self._handle_response_cancel(message)
        elif isinstance(message, ResponseAbortAllMessage):
            await self._handle_response_abort_all(message)
        elif isinstance(message, ConversationResetMessage):
            await self._handle_conversation_reset(message)
        elif isinstance(message, ResponseRetryMessage):
            await self._handle_response_retry(message)
        elif isinstance(message, ConfirmationResolveMessage):
            await self._handle_confirmation_decision(message)
        elif isinstance(message, ClientPingMessage):
            await self._handle_ping(message)
        elif isinstance(message, SessionEndMessage):
            await self._handle_session_end(message)

    async def _handle_session_start(self, message: SessionStartMessage) -> None:
        if self.state.state != VoiceState.AUTHENTICATED:
            raise StateTransitionError("session already started")
        self._session_start_message = message
        if message.protocol_version != self.settings.voice_protocol_version:
            await self._protocol_failure("unsupported_protocol_version", close_code=1002)
            return
        if (
            message.audio.sample_rate_hz != self.settings.voice_sample_rate_hz
            or message.audio.channels != 1
            or message.audio.frame_samples != self.settings.voice_frame_samples
            or message.audio.frame_bytes != self.settings.voice_frame_bytes
        ):
            await self._protocol_failure("audio_contract_mismatch", close_code=1002)
            return

        safe_metadata = _safe_client_metadata(message.client_metadata)
        self._refresh_device_time_context(
            message.device_time_context,
            legacy_timezone=safe_metadata.get("timezone"),
        )
        safe_metadata["device_time_context"] = {
            "device_epoch_ms": self._device_time_context.device_epoch_ms,
            "timezone_id": self._device_time_context.timezone_id,
            "utc_offset": self._device_time_context.utc_offset,
            "locale": self._device_time_context.locale,
        }
        safe_metadata["timezone"] = self._device_time_context.timezone_id
        safe_metadata["locale"] = self._device_time_context.locale

        if message.resume_session_id is None:
            await self._reap_stale_sessions()

        if message.resume_session_id is not None:
            voice_session = await self.persistence.resume_session(
                self.db,
                self.principal,
                message.resume_session_id,
                reconnect_grace_seconds=self.settings.voice_reconnect_grace_seconds,
            )

            if voice_session is None:
                await self._protocol_failure("session_not_available", close_code=1008)
                return
            self.stats.reconnect = True
        else:
            voice_session = await self.persistence.create_session(
                self.db,
                self.principal,
                protocol_version=message.protocol_version,
                client_metadata=safe_metadata,
            )

        # A reconnect is allowed to move with the device. Replace the stored
        # snapshot only after ownership/session validation has succeeded.
        voice_session.client_metadata = safe_metadata

        acquired = await self.registry.acquire(self.owner, voice_session.id)
        if not acquired:
            if not self.stats.reconnect:
                voice_session.status = "failed"
                voice_session.close_reason = "active_connection_exists"
                voice_session.ended_at = voice_session.last_activity_at
                await self.db.commit()
            await self._protocol_failure("active_voice_connection_exists", close_code=1008)
            return

        self.voice_session = voice_session
        self._session_id = voice_session.id
        self._session_total_frames = voice_session.total_frames
        self._session_total_bytes = voice_session.total_bytes
        self._session_client_metadata = voice_session.client_metadata
        self._stt_enabled = bool(message.stt and message.stt.enabled)
        self._stt_language = message.stt.language if message.stt else None
        LOGGER.info(
            "Voice session started",
            extra={
                "event": "voice.session.started",
                "session_id": str(voice_session.id),
                "user_id": str(self.principal.user_id),
                "device_id": str(self.principal.device_id),
                "stt_enabled": self._stt_enabled,
                "stt_language": self._stt_language or self.settings.stt_language,
                "reconnect": self.stats.reconnect,
                "time_context": {
                    "source": self._device_time_context.source,
                    "timezone": self._device_time_context.timezone_id,
                    "locale": self._device_time_context.locale,
                    "device_epoch_ms": self._device_time_context.device_epoch_ms,
                },
                "timestamp_ms": int(time.time() * 1000),
                "monotonic_ms": round(time.monotonic() * 1000, 1),
                "backend_pid": os.getpid(),
            },
        )
        self._session_started = time.monotonic()
        self.state.session_ready(voice_session.id, completed_turns=voice_session.total_turns)
        await self.db.commit()
        await self._send(
            server_event(
                "server.session.ready",
                session_id=voice_session.id,
                audio={
                    "sample_rate_hz": self.settings.voice_sample_rate_hz,
                    "channels": 1,
                    "frame_samples": self.settings.voice_frame_samples,
                    "frame_bytes": self.settings.voice_frame_bytes,
                },
                max_session_seconds=self.settings.voice_max_session_seconds,
                max_turn_seconds=self.settings.voice_max_turn_seconds,
                heartbeat_interval_seconds=self.settings.voice_heartbeat_interval_seconds,
                heartbeat_timeout_seconds=self.settings.voice_heartbeat_timeout_seconds,
                queue_capacity_frames=self.settings.voice_queue_capacity_frames,
                reconnect=self.stats.reconnect,
                stt={
                    "enabled": self._stt_enabled,
                    "language": self._stt_language or self.settings.stt_language,
                },
                time_context={
                    "timezone": self._device_time_context.timezone_id,
                    "locale": self._device_time_context.locale,
                    "source": self._device_time_context.source,
                },
                llm=self._safe_llm_session_info(),
            )
        )

    async def _reap_stale_sessions(self) -> None:
        stale_after_seconds = (
            self.settings.voice_heartbeat_timeout_seconds
            + self.settings.voice_reconnect_grace_seconds
        )
        stale_session_ids = await self.persistence.reap_stale_active_sessions(
            self.db,
            self.principal,
            stale_after_seconds=stale_after_seconds,
        )
        if not stale_session_ids:
            return

        for session_id in stale_session_ids:
            redis_released = await self.registry.release_stale_device_session(
                user_id=self.principal.user_id,
                device_id=self.principal.device_id,
                session_id=session_id,
            )
            LOGGER.info(
                "Stale voice session reaped",
                extra={
                    "event": "voice.session.stale.reaped",
                    "session_id": str(session_id),
                    "user_id": str(self.principal.user_id),
                    "device_id": str(self.principal.device_id),
                    "redis_released": redis_released,
                    "stale_after_seconds": stale_after_seconds,
                    "timestamp_ms": int(time.time() * 1000),
                    "monotonic_ms": round(time.monotonic() * 1000, 1),
                },
            )
        await self.db.commit()

    async def _handle_conversation_reset(self, _message: ConversationResetMessage) -> None:
        """Replace the durable voice session without closing the socket."""

        self._require_session()
        old_session_id = self._active_session_id()
        if old_session_id is None or self.voice_session is None:
            raise StateTransitionError("voice session is not ready")

        await self._abort_all_responses(
            reason="conversation_reset",
            emit_cancelled=True,
        )
        confirmation_store = getattr(self, "confirmation_store", None)
        if confirmation_store is not None:
            with contextlib.suppress(Exception):
                await confirmation_store.cancel_scope(self._confirmation_scope(old_session_id))

        await self.persistence.finalize_session(
            self.db,
            self.principal,
            session_id=old_session_id,
            status="completed",
            close_code=1000,
            close_reason="conversation_reset",
            total_frames=self._session_total_frames,
            total_bytes=self._session_total_bytes,
            error_count=self.stats.error_count,
        )
        await self.db.commit()
        await self.registry.release(self.owner, old_session_id)

        self.state.reset_session()
        self.voice_session = None
        self._session_id = None
        self._session_total_frames = 0
        self._session_total_bytes = 0
        self._session_client_metadata = None
        self._pending_turn_start = None
        self._last_response_id = None
        self._response_turn_id = None
        self._turn_started = None
        self._turn_start_event_ids.clear()
        self._cancelled_response_ids.clear()
        self.stats.reconnect = False
        self._session_status = "active"
        self._session_start_message = self._session_start_message or SessionStartMessage(
            type="client.session.start",
            protocol_version=self.settings.voice_protocol_version,
            audio={
                "sample_rate_hz": self.settings.voice_sample_rate_hz,
                "channels": 1,
                "frame_samples": self.settings.voice_frame_samples,
                "frame_bytes": self.settings.voice_frame_bytes,
            },
            stt={"enabled": self._stt_enabled, "language": self._stt_language},
        )
        restart_message = self._session_start_message.model_copy(
            update={"event_id": uuid.uuid4(), "resume_session_id": None}
        )
        await self._send(
            server_event(
                "server.conversation.reset",
                previous_session_id=old_session_id,
            )
        )
        await self._handle_session_start(restart_message)

    async def _handle_turn_start(self, message: TurnStartMessage) -> None:
        self._require_session()
        self._trace_latency(
            component="gateway",
            event="turn_received",
            metadata={
                "client_turn_id": str(message.client_turn_id) if message.client_turn_id else None
            },
        )
        if message.event_id in self._turn_start_event_ids:
            LOGGER.info(
                "Duplicate voice turn start ignored",
                extra={
                    "event": "NEW_TURN_START_DUPLICATE_IGNORED",
                    "session_id": str(self._active_session_id()),
                    "event_id": str(message.event_id),
                    "turn_id": str(self.active_turn.id) if self.active_turn else None,
                    "response_id": str(self._last_response_id) if self._last_response_id else None,
                },
            )
            return
        self._turn_start_event_ids.add(message.event_id)
        LOGGER.info(
            "New voice turn start received",
            extra={
                "event": "NEW_TURN_START_RECEIVED",
                "session_id": str(self._active_session_id()),
                "previous_turn_id": (
                    str(self.active_turn.id)
                    if self.active_turn is not None
                    else str(self._response_turn_id)
                    if self._response_turn_id
                    else None
                ),
                "previous_response_id": (
                    str(self._last_response_id) if self._last_response_id else None
                ),
                "client_turn_id": str(message.client_turn_id) if message.client_turn_id else None,
            },
        )
        if message.device_time_context is not None:
            self._refresh_device_time_context(message.device_time_context)
            if self.voice_session is not None:
                metadata = dict(self.voice_session.client_metadata or {})
                metadata["device_time_context"] = {
                    "device_epoch_ms": self._device_time_context.device_epoch_ms,
                    "timezone_id": self._device_time_context.timezone_id,
                    "utc_offset": self._device_time_context.utc_offset,
                    "locale": self._device_time_context.locale,
                }
                metadata["timezone"] = self._device_time_context.timezone_id
                metadata["locale"] = self._device_time_context.locale
                self.voice_session.client_metadata = metadata
        if self._stt_finalize_task is not None or self._response_turn_id is not None:
            if self._pending_turn_start is None:
                self._pending_turn_start = message
                LOGGER.info(
                    "New voice turn start pending old response cleanup",
                    extra={
                        "event": "NEW_TURN_PENDING",
                        "session_id": str(self._active_session_id()),
                        "reason": "old_response_cancelling",
                        "old_turn_id": (
                            str(self._response_turn_id) if self._response_turn_id else None
                        ),
                        "old_response_id": (
                            str(self._last_response_id) if self._last_response_id else None
                        ),
                    },
                )
            return
        if self.active_turn is not None:
            await self._send_error("turn_in_progress")
            return
        await self._create_turn(message)

    async def _create_turn(self, message: TurnStartMessage) -> None:
        """Allocate and announce one fresh server-owned user turn."""

        if self.active_turn is not None:
            await self._send_error("turn_in_progress")
            return
        if self._stt_finalize_task is not None or self._response_turn_id is not None:
            raise StateTransitionError("turn allocation attempted before old response cleanup")
        response_id = uuid.uuid4()
        turn = await self.persistence.create_turn(
            self.db,
            self.principal,
            voice_session=self.voice_session,
            response_id=response_id,
            client_turn_id=message.client_turn_id,
        )
        self.state.start_turn(turn.id, response_id)
        await self.registry.set_turn(self.owner, self._active_session_id(), turn.id, response_id)
        self.active_turn = turn
        self._last_response_id = response_id
        self._timing_state(turn.id).response_id = response_id
        self._turn_started = time.monotonic()
        self._capture_turn_timing(turn.id, "turn_started_at", monotonic=self._turn_started)
        self.cancel_guard.activate(response_id)
        LOGGER.info(
            "New voice turn created",
            extra={
                "event": "NEW_TURN_CREATED",
                "session_id": str(self._active_session_id()),
                "turn_id": str(turn.id),
                "response_id": str(response_id),
                "previous_turn_id": str(self._response_turn_id) if self._response_turn_id else None,
            },
        )
        LOGGER.info(
            "Voice turn started",
            extra={
                "event": "voice.turn.started",
                "session_id": str(self._active_session_id()),
                "turn_id": str(turn.id),
                "response_id": str(response_id),
                "turn_number": turn.turn_number,
                "timestamp_ms": int(time.time() * 1000),
                "monotonic_ms": round(self._turn_started * 1000, 1),
            },
        )
        if self._stt_enabled:
            try:
                self._capture_turn_timing(turn.id, "stt_started_at")
                self.stt_turn = await self.stt_service.start_turn(
                    session_id=self._active_session_id(),
                    turn_id=turn.id,
                    response_id=response_id,
                    language=self._stt_language,
                )
            except STTError as error:
                await self._fail_active_turn(error.code)
                return
            self._stt_event_task = asyncio.create_task(
                self._forward_stt_events(self.stt_turn),
                name=f"stt-events-{turn.id}",
            )
        await self.db.commit()
        await self._send(
            server_event(
                "server.turn.ready",
                session_id=self._active_session_id(),
                turn_id=turn.id,
                response_id=response_id,
                turn_number=turn.turn_number,
                sequence_start=0,
            )
        )
        self._trace_latency(
            turn_id=turn.id,
            response_id=response_id,
            component="gateway",
            event="server_turn_ready",
            metadata={"turn_number": turn.turn_number},
        )
        LOGGER.info(
            "Server turn ready sent",
            extra={
                "event": "SERVER_TURN_READY_SENT",
                "session_id": str(self._active_session_id()),
                "turn_id": str(turn.id),
                "response_id": str(response_id),
            },
        )

    async def _create_pending_turn_if_ready(self) -> None:
        pending = self._pending_turn_start
        if (
            pending is None
            or self.active_turn is not None
            or self._stt_finalize_task is not None
            or self._response_turn_id is not None
        ):
            return
        self._pending_turn_start = None
        LOGGER.info(
            "Creating pending voice turn after old response cleanup",
            extra={
                "event": "NEW_TURN_PENDING_RELEASED",
                "session_id": str(self._active_session_id()),
            },
        )
        await self._create_turn(pending)

    async def _handle_binary(self, frame: BinaryPcmFrame) -> None:
        self._require_session()
        if self._stt_finalize_task is not None:
            await self._send_error("turn_finalizing")
            return
        self.state.accept_frame(frame)
        if self.active_turn is not None:
            self._capture_turn_timing(self.active_turn.id, "speech_started_at")
        self.stats.frames_accepted += 1
        self.stats.bytes_received += frame.payload_length
        self.voice_session.last_activity_at = self._now_datetime()
        if frame.sequence_no == 0:
            self._trace_latency(
                turn_id=self.active_turn.id if self.active_turn else None,
                response_id=self.active_turn.response_id if self.active_turn else None,
                component="gateway",
                event="first_pcm_received",
                metadata={"bytes": frame.payload_length},
            )
        if frame.sequence_no == 0 or frame.sequence_no % 50 == 0:
            LOGGER.info(
                "Voice PCM frame accepted",
                extra={
                    "event": "voice.pcm.accepted",
                    "session_id": str(self._active_session_id()),
                    "turn_id": str(self.active_turn.id) if self.active_turn else None,
                    "sequence_no": frame.sequence_no,
                    "payload_bytes": frame.payload_length,
                    "frames_accepted": self.stats.frames_accepted,
                    "bytes_received": self.stats.bytes_received,
                    "queue_depth": self.queue.qsize(),
                    "timestamp_ms": int(time.time() * 1000),
                    "monotonic_ms": round(time.monotonic() * 1000, 1),
                },
            )
        if self.stt_turn is not None:
            await self.stt_turn.accept_audio(frame.payload)

    async def _handle_audio_commit(self, message: AudioCommitMessage) -> None:
        self._require_session()
        if self.active_turn is None:
            raise StateTransitionError("no active turn")
        self._capture_turn_timing(self.active_turn.id, "speech_ended_at")
        self._trace_latency(
            turn_id=self.active_turn.id,
            response_id=self.active_turn.response_id,
            component="gateway",
            event="turn_commit_received",
            metadata={"frame_count": message.frame_count, "byte_count": message.byte_count},
        )
        LOGGER.info(
            "Voice audio commit received",
            extra={
                "event": "voice.audio.commit.received",
                "session_id": str(self._active_session_id()),
                "turn_id": str(self.active_turn.id),
                "response_id": str(self.active_turn.response_id),
                "last_sequence_no": message.last_sequence_no,
                "frame_count": message.frame_count,
                "byte_count": message.byte_count,
                "duration_ms": message.duration_ms,
                "backend_commit_received_timestamp_ms": int(time.time() * 1000),
                "backend_commit_received_monotonic_ms": round(time.monotonic() * 1000, 1),
            },
        )
        if self.stt_turn is not None:
            if self._stt_finalize_task is not None:
                await self._send_error("turn_finalizing")
                return
            queued_ns = time.perf_counter_ns()
            self._trace_latency(
                turn_id=self.active_turn.id,
                response_id=self.active_turn.response_id,
                component="gateway_queue",
                event="finalize_task_queued",
                monotonic_ns=queued_ns,
                metadata={"queue": "asyncio_task_schedule"},
            )
            self._stt_finalize_task = asyncio.create_task(
                self._finish_audio_commit(message, self.stt_turn, queued_ns=queued_ns),
                name=f"stt-finalize-{self.active_turn.id}",
            )
            self._stt_finalize_cancel_requested = False
            return
        await self._finish_audio_commit(message, None)

    async def _finish_audio_commit(
        self,
        message: AudioCommitMessage,
        stt_turn: STTTurn | None,
        *,
        queued_ns: int | None = None,
    ) -> None:
        stt_result: STTTranscriptResult | None = None
        stt_error: STTError | None = None
        persisted_user_message = None
        turn = self.active_turn
        if turn is None:
            LOGGER.info(
                "Voice turn finalization abandoned because ownership was replaced",
                extra={
                    "event": "OLD_RESPONSE_CLEANUP_COMPLETED",
                    "session_id": str(self._active_session_id()),
                    "did_not_touch_new_turn": True,
                },
            )
            return
        turn_id = turn.id
        response_id = turn.response_id
        if queued_ns is not None:
            task_started_ns = time.perf_counter_ns()
            self._trace_latency(
                turn_id=turn_id,
                response_id=response_id,
                component="gateway_queue",
                event="finalize_task_started",
                monotonic_ns=task_started_ns,
                duration_ms=(task_started_ns - queued_ns) / 1_000_000,
                metadata={
                    "queue": "asyncio_task_schedule",
                    "queue_wait_ms": (task_started_ns - queued_ns) / 1_000_000,
                },
            )
        try:
            LOGGER.info(
                "Voice turn finalization started",
                extra={
                    "event": "voice.turn.finalization.started",
                    "session_id": str(self._active_session_id()),
                    "turn_id": str(turn_id),
                    "response_id": str(response_id),
                    "timestamp_ms": int(time.time() * 1000),
                },
            )
            if stt_turn is not None:
                try:
                    with latency_span(
                        self._trace_latency,
                        component="stt",
                        event="stt_finalize",
                        session_id=self._active_session_id(),
                        turn_id=turn_id,
                        response_id=response_id,
                    ):
                        stt_result = await stt_turn.finalize()
                    self._capture_turn_timing(turn_id, "stt_completed_at")
                    self._trace_latency(
                        turn_id=turn_id,
                        response_id=response_id,
                        component="stt",
                        event="stt_final_received",
                        metadata={"text_characters": len(stt_result.event.text)},
                    )
                    self._trace_latency(
                        turn_id=turn_id,
                        response_id=response_id,
                        component="orchestration",
                        event="orchestration_started",
                    )
                    if not stt_result.event.text.strip():
                        raise STTEmptyTranscriptError("STT returned an empty transcript")
                except STTCancelledError:
                    self._capture_turn_timing(turn_id, "stt_completed_at")
                    if self._stt_finalize_cancel_requested or self._closing.is_set():
                        return
                    if self._response_was_cancelled(response_id):
                        return
                    await self._fail_active_turn("stt_cancelled", status="cancelled")
                    return
                except STTError as error:
                    stt_error = error
                    self._capture_turn_timing(turn_id, "stt_completed_at")
            if self.state.current_turn_id != turn_id:
                if self._response_was_cancelled(response_id):
                    LOGGER.info(
                        "Cancelled old turn finalization discarded after state handoff",
                        extra={
                            "event": "OLD_RESPONSE_CLEANUP_COMPLETED",
                            "session_id": str(self._active_session_id()),
                            "old_turn_id": str(turn_id),
                            "old_response_id": str(response_id),
                            "did_not_touch_new_turn": True,
                        },
                    )
                    return
                raise StateTransitionError("turn ownership changed during finalization")
            counters = self.state.commit(
                last_sequence_no=message.last_sequence_no,
                frame_count=message.frame_count,
                byte_count=message.byte_count,
            )
            observed_duration_ms = self._elapsed_turn_ms()
            metadata: dict[str, Any] = {}
            if stt_result is not None:
                metadata = {
                    "transcript": stt_result.event.text,
                    "language": stt_result.event.language,
                    "stt": stt_result.metrics,
                }
            elif stt_error is not None:
                metadata = {"stt_error": stt_error.code}
            turn_status = "failed" if stt_error is not None else "committed"
            with latency_span(
                self._trace_latency,
                component="persistence",
                event="turn_persistence",
                session_id=self._active_session_id(),
                turn_id=turn_id,
                response_id=response_id,
            ):
                with latency_span(
                    self._trace_latency,
                    component="postgres",
                    event="turn_finalize_query",
                    turn_id=turn_id,
                    response_id=response_id,
                ):
                    await self.persistence.finalize_turn(
                        self.db,
                        self.principal,
                        turn_id=counters.turn_id,
                        status=turn_status,
                        frame_count=counters.frame_count,
                        byte_count=counters.byte_count,
                        last_sequence=counters.last_sequence_no,
                        declared_duration_ms=message.duration_ms,
                        observed_duration_ms=observed_duration_ms,
                        metadata=metadata,
                    )
                if stt_result is not None:
                    with latency_span(
                        self._trace_latency,
                        component="postgres",
                        event="user_message_persist",
                        turn_id=turn_id,
                        response_id=response_id,
                    ):
                        persisted_user_message = await self._persist_final_message_if_supported(
                            turn_id=counters.turn_id,
                            role="user",
                            content=stt_result.event.text,
                            content_json={
                                "language": stt_result.event.language,
                                "stt": stt_result.metrics,
                            },
                        )
                    if persisted_user_message is not None:
                        with latency_span(
                            self._trace_latency,
                            component="postgres",
                            event="memory_write_policy_lookup",
                            turn_id=turn_id,
                            response_id=response_id,
                        ):
                            memory_write_user_enabled = await self._memory_user_enabled()
                        if memory_write_user_enabled and (
                            self.settings.memory_write_enabled
                            and not await self._memory_excluded_for_session()
                        ):
                            with latency_span(
                                self._trace_latency,
                                component="postgres",
                                event="memory_extract_enqueue",
                                turn_id=turn_id,
                                response_id=response_id,
                            ):
                                await MemoryRepository().enqueue_extract_turn(
                                    self.db,
                                    user_id=self.principal.user_id,
                                    source_message_id=persisted_user_message.id,
                                    source_turn_id=counters.turn_id,
                                    source_session_id=self._active_session_id(),
                                    policy_version=self.settings.memory_policy_version,
                                )
                self._add_session_totals(counters.frame_count, counters.byte_count)
                self.voice_session.last_activity_at = self._now_datetime()
                self.active_turn = None
                self._response_turn_id = counters.turn_id
                self._turn_started = None
                with latency_span(
                    self._trace_latency,
                    component="postgres",
                    event="turn_commit",
                    turn_id=turn_id,
                    response_id=response_id,
                ):
                    await self.db.commit()
                # Redis owns ephemeral turn/response markers. A transient
                # registry outage must not roll back the durable turn,
                # transcript, or memory extraction job that was just written.
                try:
                    with latency_span(
                        self._trace_latency,
                        component="voice_registry",
                        event="turn_clear",
                        turn_id=turn_id,
                        response_id=response_id,
                        metadata={"registry_type": type(self.registry).__name__},
                    ):
                        await self.registry.clear_turn(
                            self.owner,
                            self._active_session_id(),
                            turn_id=counters.turn_id,
                        )
                    with latency_span(
                        self._trace_latency,
                        component="voice_registry",
                        event="session_refresh",
                        turn_id=turn_id,
                        response_id=response_id,
                        metadata={"registry_type": type(self.registry).__name__},
                    ):
                        await self.registry.refresh(self.owner, self._active_session_id())
                except Exception as error:  # noqa: BLE001 - Redis cleanup is best effort
                    LOGGER.warning(
                        "Voice registry cleanup failed after durable turn commit",
                        extra={
                            "event": "voice.registry.cleanup.failed",
                            "session_id": str(self._active_session_id()),
                            "turn_id": str(turn_id),
                            "response_id": str(response_id),
                            "exception": type(error).__name__,
                            "exception_message": str(error),
                        },
                    )
            if stt_result is not None:
                with latency_span(
                    self._trace_latency,
                    component="websocket",
                    event="transcript_final_send",
                    session_id=self._active_session_id(),
                    turn_id=turn_id,
                    response_id=response_id,
                ):
                    await self._send_transcript_event(stt_result.event)
                with latency_span(
                    self._trace_latency,
                    component="sync_logging",
                    event="transcript_delivery_log_write",
                    session_id=self._active_session_id(),
                    turn_id=turn_id,
                    response_id=response_id,
                ):
                    LOGGER.info(
                        "Voice final transcript delivered",
                        extra={
                            "event": "voice.transcript.final.delivered",
                            "session_id": str(stt_result.event.session_id),
                            "turn_id": str(stt_result.event.turn_id),
                            "response_id": str(stt_result.event.response_id),
                            "text": stt_result.event.text,
                            "language": stt_result.event.language,
                            "timestamp_ms": int(time.time() * 1000),
                            "transcript_timestamp_ms": stt_result.event.timestamp_ms,
                            "monotonic_ms": round(time.monotonic() * 1000, 1),
                            "metrics": stt_result.metrics,
                        },
                    )
            with latency_span(
                self._trace_latency,
                component="stt",
                event="stt_turn_close",
                session_id=self._active_session_id(),
                turn_id=turn_id,
                response_id=response_id,
            ):
                await self._close_stt_turn()
            if stt_error is not None:
                await self._persist_conversation_log(
                    counters.turn_id,
                    status="failed",
                )
                await self._complete_response_state(counters.response_id)
                await self._send(
                    server_event(
                        "server.turn.failed",
                        session_id=self._active_session_id(),
                        turn_id=counters.turn_id,
                        response_id=counters.response_id,
                        turn_number=counters.turn_number,
                        code=stt_error.code,
                    )
                )
                return

            llm_result: dict[str, Any] = {"status": "disabled"}
            if stt_result is not None:
                with latency_span(
                    self._trace_latency,
                    component="confirmation",
                    event="confirmation_routing",
                    session_id=self._active_session_id(),
                    turn_id=turn_id,
                    response_id=response_id,
                    metadata={
                        "confirmation_store_type": type(
                            getattr(self, "confirmation_store", None)
                        ).__name__
                    },
                ):
                    confirmation_result = await self._resolve_pending_confirmation(
                        session_id=self._active_session_id(),
                        turn_id=counters.turn_id,
                        response_id=counters.response_id,
                        transcript=stt_result.event.text,
                    )
                if confirmation_result is not None:
                    llm_result = confirmation_result
                elif self.llm_service.enabled:
                    llm_result = await self._stream_llm_response(
                        session_id=self._active_session_id(),
                        turn_id=counters.turn_id,
                        response_id=counters.response_id,
                        transcript=stt_result.event.text,
                    )
                if llm_result["status"] == "cancelled":
                    await self._persist_conversation_log(
                        counters.turn_id,
                        status="cancelled",
                    )
                    return

            await self._persist_conversation_log(
                counters.turn_id,
                status=("failed" if llm_result["status"] == "failed" else "completed"),
            )

            await self._complete_response_state(counters.response_id, release_pending=False)
            await self._send(
                server_event(
                    "server.turn.completed",
                    session_id=self._active_session_id(),
                    turn_id=counters.turn_id,
                    response_id=counters.response_id,
                    turn_number=counters.turn_number,
                    frame_count=counters.frame_count,
                    byte_count=counters.byte_count,
                    last_sequence_no=counters.last_sequence_no,
                    observed_duration_ms=observed_duration_ms,
                    stt_enabled=self._stt_enabled,
                    llm_enabled=self.llm_service.enabled,
                    llm_status=llm_result["status"],
                )
            )
            await self._create_pending_turn_if_ready()
        except asyncio.CancelledError:
            return
        except StateTransitionError as error:
            if self._response_was_cancelled(response_id):
                LOGGER.info(
                    "Cancelled old response completion ignored",
                    extra={
                        "event": "OLD_RESPONSE_CLEANUP_COMPLETED",
                        "session_id": str(self._active_session_id()),
                        "old_turn_id": str(turn_id),
                        "old_response_id": str(response_id),
                        "exception": type(error).__name__,
                        "did_not_touch_new_turn": True,
                    },
                )
                return
            LOGGER.exception(
                "VOICE_TURN_COMPLETION_FAILED",
                extra=self._completion_error_context(
                    turn_id=turn_id,
                    response_id=response_id,
                    exception=error,
                ),
            )
            self.stats.error_count += 1
            await self._protocol_failure("voice_turn_completion_failed", close_code=1011)
        except (VoiceRegistryError, VoiceSessionConflict):
            await self._protocol_failure("voice_registry_unavailable", close_code=1013)
        except SQLAlchemyError:
            await self.db.rollback()
            self.stats.error_count += 1
            await self._protocol_failure("voice_persistence_unavailable", close_code=1011)
        except Exception as error:  # noqa: BLE001 - isolate background turn completion failures
            LOGGER.exception(
                "VOICE_TURN_COMPLETION_FAILED",
                extra=self._completion_error_context(
                    turn_id=turn_id,
                    response_id=response_id,
                    exception=error,
                ),
            )
            self.stats.error_count += 1
            await self._protocol_failure("voice_turn_completion_failed", close_code=1011)
        finally:
            if self._stt_finalize_task is asyncio.current_task():
                self._stt_finalize_task = None
                self._stt_finalize_cancel_requested = False

    async def _stream_llm_response(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        transcript: str,
    ) -> dict[str, Any]:
        if not transcript.strip():
            return {"status": "failed", "error": "empty_transcript"}
        started = time.monotonic()
        self._trace_latency(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            component="orchestration",
            event="orchestration_start",
        )
        first_event_at: float | None = None
        first_text_at: float | None = None
        text_parts: list[str] = []
        usage: LLMUsage | None = None
        terminal_event: LLMEvent | None = None
        failure_code: str | None = None
        attempt_count = 0
        request_started_times: list[float] = []
        first_token_traced = False
        tool_call_at: float | None = None
        tool_execution_started_at: float | None = None
        tool_execution_finished_at: float | None = None
        confirmation_required = False
        tts_queue: asyncio.Queue[str | None] | None = None
        tts_task: asyncio.Task[None] | None = None
        tts_segmenter: SentenceSegmenter | None = None
        tts_input_started = False
        tts_service = getattr(self, "tts_service", None)
        if tts_service is not None and tts_service.enabled:
            tts_queue = asyncio.Queue(maxsize=4)
            tts_segmenter = SentenceSegmenter(max_chars=self.settings.tts_max_sentence_chars)
            tts_task = asyncio.create_task(
                self._run_tts_queue(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    queue=tts_queue,
                ),
                name=f"tts-response-{response_id}",
            )
            self._tts_tasks[response_id] = tts_task
            self._tts_queues[response_id] = tts_queue

        async def on_tool_execution_started(call, timestamp: float) -> None:
            nonlocal tool_execution_started_at
            tool_execution_started_at = tool_execution_started_at or timestamp
            self._trace_latency(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                component="tool",
                event="tool_start",
                metadata={"tool_name": call.name, "tool_call_id": call.tool_call_id},
            )
            await self._send_tool_status(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                tool_call_id=call.tool_call_id,
                tool_name=call.name,
                status="executing",
            )

        def on_tool_execution_finished(_call, timestamp: float) -> None:
            nonlocal tool_execution_finished_at
            tool_execution_finished_at = timestamp
            self._trace_latency(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                component="tool",
                event="tool_end",
                metadata={"tool_name": _call.name, "tool_call_id": _call.tool_call_id},
            )

        try:
            tool_registry = getattr(self, "tool_registry", None)
            with latency_span(
                self._trace_latency,
                component="postgres",
                event="memory_policy_lookup",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
            ):
                memory_user_enabled = await self._memory_user_enabled()
            memory_excluded = await self._memory_excluded_for_session()
            memory_write_allowed = (
                self.settings.memory_write_enabled
                and memory_user_enabled
                and not memory_excluded
            )
            allowed_tools = (
                tuple(
                    tool
                    for tool in tool_registry.definitions()
                    if memory_write_allowed or tool.name not in {"memory_save", "memory_forget"}
                )
                if tool_registry
                else ()
            )
            websocket_app = getattr(getattr(self, "websocket", None), "app", None)
            memory_service = getattr(getattr(websocket_app, "state", None), "memory_service", None)
            effective_timezone, timezone_source = timezone_for_request(
                transcript,
                device_timezone=self._user_timezone(),
            )
            self._active_timezone_source = timezone_source
            self._active_timezone = effective_timezone
            effective_time_context = self._time_context_for_timezone(effective_timezone)
            context = (
                ToolExecutionContext(
                    user_id=self.principal.user_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    scopes=frozenset(
                        {
                            "tasks:read",
                            "tasks:write",
                            "reminders:read",
                            "reminders:write",
                            *(
                                {"memory:read"}
                                if self.settings.memory_retrieval_mode != "off"
                                and memory_user_enabled
                                else set()
                            ),
                            *({"memory:write"} if memory_write_allowed else set()),
                        }
                    ),
                    db=self.db,
                    clock=self._trusted_user_clock(),
                    user_timezone=effective_timezone,
                    timezone_source=timezone_source,
                    device_time_context=effective_time_context,
                    source_transcript=transcript,
                    confirmation_requested=self._persist_confirmation_request,
                    tool_execution_started=on_tool_execution_started,
                    tool_execution_finished=on_tool_execution_finished,
                    tool_execution_audit=self._record_tool_execution_audit,
                    memory_settings=self.settings,
                    memory_service=memory_service,
                )
                if tool_registry is not None
                else None
            )
            with latency_span(
                self._trace_latency,
                component="tool_routing",
                event="explicit_memory_routing",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                metadata={"memory_write_allowed": memory_write_allowed},
            ):
                explicit_memory_call = (
                    build_explicit_memory_save_call(transcript, turn_id=turn_id)
                    if memory_write_allowed
                    else None
                )

            wait_status = classify_wait_status(
                transcript,
                memory_retrieval_will_run=(
                    self.settings.memory_retrieval_mode == "inject"
                    and memory_service is not None
                    and memory_user_enabled
                    and not memory_excluded
                ),
                tool_choice=classify_voice_tool_choice(transcript, allowed_tools),
                variant_index=getattr(self, "_wait_phrase_index", 0),
            )
            self._wait_phrase_index = getattr(self, "_wait_phrase_index", 0) + 1
            if not self.cancel_guard.can_emit(response_id):
                return {"status": "cancelled"}
            await self._send(
                server_event(
                    "assistant.thinking",
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    text=wait_status.phrase,
                    category=wait_status.category,
                )
            )
            if tts_queue is not None and wait_status.category not in {"task", "memory_save"}:
                await tts_queue.put(wait_status.phrase)

            if explicit_memory_call is not None and context is not None:
                await self._send_tool_status(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    tool_call_id=explicit_memory_call.tool_call_id,
                    tool_name=explicit_memory_call.name,
                    status="understanding",
                )
                result = await self.tool_loop.executor.execute(
                    explicit_memory_call,
                    context=context,
                )
                if result.error_code == "llm_tool_confirmation_required":
                    return {"status": "confirmation_required"}
                await self._send_tool_status(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    tool_call_id=explicit_memory_call.tool_call_id,
                    tool_name=explicit_memory_call.name,
                    status=("success" if result.success else "failed"),
                    error_code=result.error_code,
                )
                return {
                    "status": "completed" if result.success else "failed",
                    "tool_execution_count": 1 if result.executed else 0,
                }
            with latency_span(
                self._trace_latency,
                component="memory",
                event="memory_decision",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
            ):
                memory_context = await self._memory_context_for_transcript(
                    transcript,
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                )
            conversation_history = await self._conversation_history_for_turn(
                session_id=session_id,
                turn_id=turn_id,
            )
            request = build_voice_llm_request(
                self.settings,
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                transcript=transcript,
                allowed_tools=allowed_tools,
                memory_context=memory_context,
                conversation_history=conversation_history,
                trace=self._trace_latency,
            )
            if tool_registry is None:
                event_stream = self.llm_service.stream(request)
            else:
                assert context is not None
                event_stream = self.tool_loop.stream(request, context=context)
            async for event in event_stream:
                if not self.cancel_guard.can_emit(response_id):
                    return {"status": "cancelled"}
                attempt_count = max(attempt_count, event.attempt)
                if event.event_type == "request_started":
                    request_started_times.append(event.monotonic_seconds)
                    self._capture_turn_timing(
                        turn_id,
                        "llm_started_at",
                        monotonic=event.monotonic_seconds,
                    )
                    self._trace_latency(
                        session_id=session_id,
                        turn_id=turn_id,
                        response_id=response_id,
                        component="llm",
                        event="gateway_llm_request_observed",
                        metadata={
                            "attempt": event.attempt,
                            "duration_basis": "correlation_only",
                        },
                    )
                    continue
                first_event_at = first_event_at or event.monotonic_seconds
                if event.event_type == "text_delta" and event.delta:
                    is_first_text_delta = not first_token_traced
                    first_text_at = first_text_at or event.monotonic_seconds
                    self._capture_turn_timing(
                        turn_id,
                        "llm_first_token_at",
                        monotonic=event.monotonic_seconds,
                    )
                    if is_first_text_delta:
                        first_token_traced = True
                        self._trace_latency(
                            session_id=session_id,
                            turn_id=turn_id,
                            response_id=response_id,
                            component="llm",
                            event="gateway_llm_first_token_observed",
                            metadata={
                                "attempt": event.attempt,
                                "duration_basis": "correlation_only",
                            },
                        )
                        observed_ns = time.perf_counter_ns()
                        self._trace_latency(
                            session_id=session_id,
                            turn_id=turn_id,
                            response_id=response_id,
                            component="orchestration",
                            event="llm_first_token_observed_by_gateway",
                            monotonic_ns=observed_ns,
                            metadata={
                                "duration_basis": "correlation_only",
                                "tool_loop_enabled": (
                                    getattr(self, "tool_registry", None) is not None
                                ),
                            },
                        )
                    text_parts.append(event.delta)
                    if tts_queue is not None and tts_segmenter is not None:
                        tts_input_started = True
                        for sentence in tts_segmenter.push(event.delta):
                            await tts_queue.put(sentence)
                    delta_event = server_event(
                        "assistant.text.delta",
                        session_id=session_id,
                        turn_id=turn_id,
                        response_id=response_id,
                        sequence=event.sequence,
                        delta=event.delta,
                        provider=event.provider,
                        model=event.configured_model,
                    )
                    if is_first_text_delta:
                        with latency_span(
                            self._trace_latency,
                            component="websocket",
                            event="first_assistant_text_send",
                            session_id=session_id,
                            turn_id=turn_id,
                            response_id=response_id,
                            metadata={"delta_characters": len(event.delta)},
                        ):
                            await self._send(delta_event)
                    else:
                        await self._send(delta_event)
                    continue
                if event.event_type.startswith("tool_call_"):
                    if event.event_type == "tool_call_completed":
                        tool_call_at = tool_call_at or event.monotonic_seconds
                        if event.tool_call is not None:
                            await self._send_tool_status(
                                session_id=session_id,
                                turn_id=turn_id,
                                response_id=response_id,
                                tool_call_id=event.tool_call.tool_call_id,
                                tool_name=event.tool_call.name,
                                status="understanding",
                            )
                    continue
                if event.event_type.startswith("tool_execution_"):
                    if event.tool_call is not None:
                        if event.event_type == "tool_execution_completed":
                            # A mutating handler may have only flushed its row.
                            # Commit before success status or resumed assistant
                            # text can reach the client/provider.
                            await self.db.commit()
                        status = (
                            "success"
                            if event.event_type == "tool_execution_completed"
                            else ("cancelled" if event.error_code == "llm_cancelled" else "failed")
                        )
                        await self._send_tool_status(
                            session_id=session_id,
                            turn_id=turn_id,
                            response_id=response_id,
                            tool_call_id=event.tool_call.tool_call_id,
                            tool_name=event.tool_call.name,
                            status=status,
                            error_code=event.error_code,
                        )
                    continue
                if event.event_type == "confirmation_required":
                    confirmation_required = True
                    break
                if event.event_type == "usage":
                    usage = event.usage
                    continue
                if event.event_type == "response_failed":
                    terminal_event = event
                    failure_code = event.error_code or "llm_provider_error"
                    self._capture_turn_timing(
                        turn_id,
                        "llm_completed_at",
                        monotonic=event.monotonic_seconds,
                    )
                    self._trace_latency(
                        session_id=session_id,
                        turn_id=turn_id,
                        response_id=response_id,
                        component="llm",
                        event="gateway_llm_complete_observed",
                        metadata={"status": "failed", "duration_basis": "correlation_only"},
                    )
                    break
                if event.event_type == "response_completed":
                    terminal_event = event
                    self._capture_turn_timing(
                        turn_id,
                        "llm_completed_at",
                        monotonic=event.monotonic_seconds,
                    )
                    self._trace_latency(
                        session_id=session_id,
                        turn_id=turn_id,
                        response_id=response_id,
                        component="llm",
                        event="gateway_llm_complete_observed",
                        metadata={
                            "status": "completed",
                            "duration_basis": "correlation_only",
                        },
                    )
                    if event.text is not None:
                        text_parts = [event.text]
                        if (
                            not tts_input_started
                            and tts_queue is not None
                            and tts_segmenter is not None
                        ):
                            for sentence in tts_segmenter.push(event.text):
                                await tts_queue.put(sentence)
                    break
        except LLMError as error:
            failure_code = error.code
        finally:
            if tts_queue is not None and tts_task is not None:
                if not tts_task.done():
                    pending_tts = tts_segmenter.flush() if tts_segmenter is not None else ()
                    for sentence in pending_tts:
                        await tts_queue.put(sentence)
                    await tts_queue.put(None)
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await tts_task
                self._tts_tasks.pop(response_id, None)
                self._tts_queues.pop(response_id, None)

        if confirmation_required:
            # The server has already persisted the validated proposal and
            # emitted the spoken confirmation request. This is a successful
            # terminal state for this turn, not an LLM failure.
            return {"status": "confirmation_required"}

        # Providers should emit a terminal event. This fallback preserves a
        # truthful boundary for an abnormal stream without borrowing a value
        # from a different turn.
        self._capture_turn_timing(turn_id, "llm_completed_at")

        completed_at = time.monotonic()
        provider_info = self.llm_service.provider_info
        provider = provider_info.provider if provider_info is not None else "unavailable"
        configured_model = (
            provider_info.configured_model if provider_info is not None else "unavailable"
        )
        metrics = {
            "request_to_first_event_ms": self._duration_ms(started, first_event_at),
            "request_to_first_text_ms": self._duration_ms(started, first_text_at),
            "request_to_completion_ms": self._duration_ms(started, completed_at),
            "request_to_tool_call_ms": self._duration_ms(started, tool_call_at),
            "tool_execution_duration_ms": (
                self._duration_ms(tool_execution_started_at, tool_execution_finished_at)
                if tool_execution_started_at is not None
                else None
            ),
            "tool_result_to_resumed_first_text_ms": (
                self._duration_ms(request_started_times[1], first_text_at)
                if len(request_started_times) > 1
                else None
            ),
            "total_orchestration_ms": self._duration_ms(started, completed_at),
        }

        if failure_code is not None or terminal_event is None:
            code = failure_code or "llm_incomplete_response"
            await self.persistence.merge_turn_metadata(
                self.db,
                self.principal,
                turn_id=turn_id,
                metadata={
                    "llm": {
                        "status": "failed",
                        "error": code,
                        "provider": provider,
                        "configured_model": configured_model,
                        "returned_model": (
                            terminal_event.returned_model if terminal_event is not None else None
                        ),
                        "provider_request_id": (
                            terminal_event.provider_request_id
                            if terminal_event is not None
                            else None
                        ),
                        "prompt_version": VOICE_SYSTEM_PROMPT_VERSION,
                        "attempt_count": attempt_count,
                        "latency": metrics,
                    }
                },
            )
            await self.db.commit()
            await self._send(
                server_event(
                    "assistant.response.failed",
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    code=code,
                    provider=provider,
                    model=configured_model,
                    retryable=terminal_event.retryable if terminal_event is not None else False,
                )
            )
            LOGGER.warning(
                "LLM response failed",
                extra={
                    "event": "llm.response.failed",
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "response_id": str(response_id),
                    "provider": provider,
                    "configured_model": configured_model,
                    "error_code": code,
                    "attempt_count": attempt_count,
                    "latency": metrics,
                },
            )
            return {"status": "failed", "error": code}

        response_text = "".join(text_parts)
        if not response_text.strip():
            failure_event = terminal_event.model_copy(
                update={"error_code": "llm_empty_response", "retryable": False}
            )
            terminal_event = failure_event
            await self.persistence.merge_turn_metadata(
                self.db,
                self.principal,
                turn_id=turn_id,
                metadata={
                    "llm": {
                        "status": "failed",
                        "error": "llm_empty_response",
                        "provider": provider,
                        "configured_model": configured_model,
                        "prompt_version": VOICE_SYSTEM_PROMPT_VERSION,
                        "attempt_count": attempt_count,
                        "latency": metrics,
                    }
                },
            )
            await self.db.commit()
            await self._send(
                server_event(
                    "assistant.response.failed",
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    code="llm_empty_response",
                    provider=provider,
                    model=configured_model,
                    retryable=False,
                )
            )
            return {"status": "failed", "error": "llm_empty_response"}

        usage_data = usage.model_dump(exclude_none=True) if usage is not None else None
        llm_metadata = {
            "status": "completed",
            "provider": provider,
            "configured_model": configured_model,
            "returned_model": terminal_event.returned_model,
            "provider_request_id": terminal_event.provider_request_id,
            "finish_reason": terminal_event.finish_reason,
            "response_text": response_text,
            "usage": usage_data,
            "prompt_version": VOICE_SYSTEM_PROMPT_VERSION,
            "attempt_count": attempt_count,
            "latency": metrics,
        }
        await self.persistence.merge_turn_metadata(
            self.db,
            self.principal,
            turn_id=turn_id,
            metadata={"llm": llm_metadata},
        )
        await self._persist_final_message_if_supported(
            turn_id=turn_id,
            role="assistant",
            content=response_text,
            content_json={
                "response_id": str(response_id),
                "finish_reason": terminal_event.finish_reason,
            },
            model=configured_model,
        )
        await self.db.commit()
        await self._send(
            server_event(
                "assistant.text.final",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                text=response_text,
                provider=provider,
                model=configured_model,
                returned_model=terminal_event.returned_model,
                provider_request_id=terminal_event.provider_request_id,
                finish_reason=terminal_event.finish_reason,
                usage=usage_data,
                metrics=metrics,
            )
        )
        LOGGER.info(
            "LLM response completed",
            extra={
                "event": "llm.response.completed",
                "session_id": str(session_id),
                "turn_id": str(turn_id),
                "response_id": str(response_id),
                "provider": provider,
                "configured_model": configured_model,
                "returned_model": terminal_event.returned_model,
                "provider_request_id": terminal_event.provider_request_id,
                "finish_reason": terminal_event.finish_reason,
                "usage": usage_data,
                "attempt_count": attempt_count,
                "latency": metrics,
            },
        )
        return {"status": "completed", "metrics": metrics}

    async def _send_tool_status(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        tool_call_id: str,
        tool_name: str,
        status: str,
        confirmation_id: uuid.UUID | None = None,
        error_code: str | None = None,
    ) -> None:
        await self._send(
            server_event(
                "tool.status",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_status=status,
                confirmation_id=confirmation_id,
                error_code=error_code,
            )
        )

    async def _record_tool_execution_audit(
        self,
        call,
        tool,
        arguments: BaseModel,
        result,
    ) -> None:
        if not hasattr(self.db, "add"):
            return
        from app.services.audit import safe_tool_payload, safe_tool_result_content

        record_audit(
            self.db,
            "TOOL_EXECUTION",
            user_id=self.principal.user_id,
            device_id=self.principal.device_id,
            metadata={
                "tool_name": tool.name,
                "tool_call_id": call.tool_call_id,
                "status": (
                    "replayed" if result.replayed else "completed" if result.success else "failed"
                ),
                "executed": result.executed,
                "error_code": result.error_code,
                "arguments": safe_tool_payload(arguments.model_dump(mode="json")),
                "result": safe_tool_result_content(result.content),
            },
        )

    async def _persist_confirmation_request(
        self,
        call,
        validated_arguments: BaseModel,
        tool,
    ) -> bool:
        """Persist a validated mutation before the tool loop can execute it."""

        LOGGER.info(
            "Persisting voice confirmation before mutation",
            extra={
                "event": "voice.confirmation.persist.started",
                "tool_name": tool.name,
                "tool_call_id": call.tool_call_id,
                "original_turn_id": str(self._response_turn_id),
            },
        )

        store = getattr(self, "confirmation_store", None)
        session = getattr(self, "voice_session", None)
        if store is None or session is None:
            return False
        original_turn_id = self._response_turn_id
        if original_turn_id is None:
            return False
        active_timezone = getattr(self, "_active_timezone", None) or self._user_timezone()
        pending = PendingConfirmation.new(
            authenticated_user_id=self.principal.user_id,
            device_id=self.principal.device_id,
            session_id=session.id,
            original_turn_id=original_turn_id,
            original_response_id=self._last_response_id or uuid.uuid4(),
            tool_call_id=call.tool_call_id,
            tool_name=tool.name,
            validated_tool_arguments=validated_arguments.model_dump(mode="json"),
            idempotency_key=(
                self.principal.user_id,
                original_turn_id,
                call.name,
                call.tool_call_id,
            ),
            ttl_seconds=self.settings.voice_confirmation_ttl_seconds,
            user_timezone=active_timezone,
            timezone_source=getattr(self, "_active_timezone_source", "device"),
        )
        stored = await store.create_or_get(pending)
        if stored.tool_name != tool.name or stored.tool_call_id != call.tool_call_id:
            LOGGER.warning(
                "Existing voice confirmation made tool proposal terminal",
                extra={
                    "event": "voice.confirmation.persist.existing",
                    "existing_confirmation_id": str(stored.confirmation_id),
                    "existing_tool_name": stored.tool_name,
                    "existing_tool_call_id": stored.tool_call_id,
                    "proposed_tool_name": tool.name,
                    "proposed_tool_call_id": call.tool_call_id,
                },
            )
            return True
        try:
            await self.persistence.merge_turn_metadata(
                self.db,
                self.principal,
                turn_id=original_turn_id,
                metadata={
                    "confirmation": {
                        "confirmation_id": str(stored.confirmation_id),
                        "status": stored.status,
                        "tool_name": stored.tool_name,
                        "validated_arguments": stored.validated_tool_arguments,
                        "expires_at": stored.expires_at.isoformat(),
                    }
                },
            )
            await self.db.commit()
        except Exception:  # noqa: BLE001 - Redis pending state remains terminal
            await self.db.rollback()
            LOGGER.exception(
                "Voice confirmation metadata persistence failed after pending state was saved",
                extra={"event": "voice.confirmation.metadata.failed"},
            )
        confirmation_event = server_event(
            "confirmation.required",
            session_id=stored.session_id,
            turn_id=stored.original_turn_id,
            response_id=stored.original_response_id,
            confirmation_id=stored.confirmation_id,
            tool_call_id=stored.tool_call_id,
            tool_name=stored.tool_name,
            validated_arguments=stored.validated_tool_arguments,
            timezone=stored.user_timezone,
            timezone_source=stored.timezone_source,
            due_at_utc=_confirmation_due_at_utc(stored.validated_tool_arguments),
            due_at_local=_confirmation_due_at_local(stored),
            expires_at=stored.expires_at.isoformat(),
            status=stored.status,
        )
        try:
            await asyncio.wait_for(self._send(confirmation_event), timeout=2.0)
        except (TimeoutError, RuntimeError, WebSocketDisconnect):
            LOGGER.warning(
                "Voice confirmation notification was not delivered",
                extra={
                    "event": "voice.confirmation.notification.failed",
                    "confirmation_id": str(stored.confirmation_id),
                },
            )
        confirmation_prompt = self._confirmation_prompt_text(stored)
        await self._send(
            server_event(
                "assistant.response.started",
                session_id=stored.session_id,
                turn_id=stored.original_turn_id,
                response_id=stored.original_response_id,
                confirmation=True,
            )
        )
        await self._speak_text(
            session_id=stored.session_id,
            turn_id=stored.original_turn_id,
            response_id=stored.original_response_id,
            text=confirmation_prompt,
        )
        await self._send(
            server_event(
                "assistant.text.final",
                session_id=stored.session_id,
                turn_id=stored.original_turn_id,
                response_id=stored.original_response_id,
                text=confirmation_prompt,
                provider="server",
                model="confirmation-request",
                finish_reason="confirmation_required",
            )
        )
        LOGGER.info(
            "Voice confirmation required",
            extra={
                "event": "voice.confirmation.required",
                "confirmation_id": str(stored.confirmation_id),
                "user_id": str(stored.authenticated_user_id),
                "device_id": str(stored.device_id),
                "session_id": str(stored.session_id),
                "original_turn_id": str(stored.original_turn_id),
                "tool_call_id": stored.tool_call_id,
                "tool_name": stored.tool_name,
                "validated_arguments": stored.validated_tool_arguments,
                "expires_at": stored.expires_at.isoformat(),
                "status": stored.status,
                "timezone": stored.user_timezone,
                "timezone_source": stored.timezone_source,
            },
        )
        return True

    @staticmethod
    def _confirmation_prompt_text(pending: PendingConfirmation) -> str:
        """Build the only confirmation question the client needs to hear."""

        tool_label = pending.tool_name.replace("_", " ")
        arguments = pending.validated_tool_arguments
        title = str(arguments.get("title", "")).strip()
        details: list[str] = []
        if title:
            details.append(f'titled "{title[:160]}"')
        due_at = _confirmation_due_at_local(pending)
        if due_at:
            details.append(f"scheduled for {due_at}")
        detail_text = f" ({', '.join(details)})" if details else ""
        return (
            f"I can {tool_label}{detail_text}. "
            "Say yes to approve, or no to reject."
        )

    def _confirmation_scope(self, session_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        return (self.principal.user_id, self.principal.device_id, session_id)

    async def _resolve_pending_confirmation(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        transcript: str,
    ) -> dict[str, Any] | None:
        """Resolve obvious spoken confirmation before ordinary LLM routing."""

        store = getattr(self, "confirmation_store", None)
        if store is None:
            return None
        scope = self._confirmation_scope(session_id)
        pending = await store.get(scope)
        if pending is None:
            return None

        resolution = resolve_confirmation(transcript)

        # Terminal confirmation records remain briefly available for replay
        # protection and auditability. They must not intercept a later,
        # unrelated voice request such as a task or memory lookup. Preserve
        # the existing response for an explicit replayed yes/no, but let
        # ordinary speech continue through normal LLM/tool routing.
        if pending.status in {"REJECTED", "CANCELLED", "CONSUMED"}:
            if resolution == "AMBIGUOUS":
                return None
            text = "That confirmation has already been handled."
            await self._send_confirmation_response(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                text=text,
                confirmation_id=pending.confirmation_id,
                status=pending.status,
            )
            await self._record_confirmation_turn(
                turn_id,
                pending,
                status=pending.status,
                resolution=resolution,
                execution_count=0,
                final_response=text,
            )
            return {"status": "completed", "confirmation": "already_handled"}

        # An expired confirmation should only answer an explicit yes/no. A
        # new question must be allowed to reach ordinary routing.
        if pending.status == "EXPIRED" or pending.is_expired():
            if resolution == "AMBIGUOUS":
                return None

        if resolution == "AMBIGUOUS":
            text = "Confirmation unclear. Please speak YES to approve or NO to cancel."
            await self._send_confirmation_response(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                text=text,
                confirmation_id=pending.confirmation_id,
                status=pending.status,
            )
            await self._record_confirmation_turn(
                turn_id,
                pending,
                status=pending.status,
                resolution="AMBIGUOUS",
                execution_count=0,
                final_response=text,
            )
            return {"status": "completed", "confirmation": "ambiguous"}

        if pending.status == "EXPIRED" or pending.is_expired():
            if pending.status == "PENDING":
                await store.transition(scope, pending.confirmation_id, "EXPIRED")
            await self._send_tool_status(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                tool_call_id=pending.tool_call_id,
                tool_name=pending.tool_name,
                status="failed",
                confirmation_id=pending.confirmation_id,
                error_code="llm_tool_confirmation_expired",
            )
            text = "That confirmation has expired. Please make the request again."
            await self._send_confirmation_response(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                text=text,
                confirmation_id=pending.confirmation_id,
                status="EXPIRED",
            )
            await self._record_confirmation_turn(
                turn_id,
                pending,
                status="EXPIRED",
                resolution=resolution,
                execution_count=0,
                final_response=text,
            )
            return {"status": "completed", "confirmation": "expired"}

        if resolution == "REJECTED":
            await store.transition(scope, pending.confirmation_id, "REJECTED")
            await self._send_tool_status(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                tool_call_id=pending.tool_call_id,
                tool_name=pending.tool_name,
                status="cancelled",
                confirmation_id=pending.confirmation_id,
            )
            text = self._confirmation_rejected_text(pending)
            await self._send_confirmation_response(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                text=text,
                confirmation_id=pending.confirmation_id,
                status="REJECTED",
            )
            await self._record_confirmation_turn(
                turn_id,
                pending,
                status="REJECTED",
                resolution=resolution,
                execution_count=0,
                final_response=text,
            )
            return {"status": "completed", "confirmation": "rejected"}

        claimed = await store.claim(scope, pending.confirmation_id)
        if claimed is None:
            latest = await store.get(scope)
            status = latest.status if latest is not None else "CANCELLED"
            await self._send_tool_status(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                tool_call_id=pending.tool_call_id,
                tool_name=pending.tool_name,
                status="failed" if status == "EXPIRED" else "cancelled",
                confirmation_id=pending.confirmation_id,
                error_code=("llm_tool_confirmation_expired" if status == "EXPIRED" else None),
            )
            text = (
                "That confirmation has expired. Please make the request again."
                if status == "EXPIRED"
                else "That confirmation is no longer available. Please make the request again."
            )
            await self._send_confirmation_response(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                text=text,
                confirmation_id=pending.confirmation_id,
                status=status,
            )
            await self._record_confirmation_turn(
                turn_id,
                pending,
                status=status,
                resolution=resolution,
                execution_count=0,
                final_response=text,
            )
            return {"status": "completed", "confirmation": status.lower()}

        if (
            claimed.authenticated_user_id != self.principal.user_id
            or claimed.device_id != self.principal.device_id
            or claimed.session_id != session_id
        ):
            await store.transition(scope, claimed.confirmation_id, "CANCELLED")
            await self._send_tool_status(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                tool_call_id=claimed.tool_call_id,
                tool_name=claimed.tool_name,
                status="failed",
                confirmation_id=claimed.confirmation_id,
                error_code="llm_tool_confirmation_scope_invalid",
            )
            text = "I couldn't verify that confirmation. Please make the request again."
            await self._send_confirmation_response(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                text=text,
                confirmation_id=claimed.confirmation_id,
                status="CANCELLED",
            )
            await self._record_confirmation_turn(
                turn_id,
                claimed,
                status="CANCELLED",
                resolution=resolution,
                execution_count=0,
                final_response=text,
            )
            return {"status": "completed", "confirmation": "scope_rejected"}

        from app.llm.types import LLMToolCall

        call = LLMToolCall(
            tool_call_id=claimed.tool_call_id,
            name=claimed.tool_name,
            arguments_json=json.dumps(
                claimed.validated_tool_arguments,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            arguments=claimed.validated_tool_arguments,
        )

        async def on_confirmation_execution_started(_call, _timestamp: float) -> None:
            await self._send_tool_status(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                tool_call_id=claimed.tool_call_id,
                tool_name=claimed.tool_name,
                status="executing",
                confirmation_id=claimed.confirmation_id,
            )

        await self._send_tool_status(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            tool_call_id=claimed.tool_call_id,
            tool_name=claimed.tool_name,
            status="approved",
            confirmation_id=claimed.confirmation_id,
        )
        websocket_app = getattr(getattr(self, "websocket", None), "app", None)
        memory_service = getattr(getattr(websocket_app, "state", None), "memory_service", None)
        context = ToolExecutionContext(
            user_id=self.principal.user_id,
            session_id=session_id,
            turn_id=claimed.original_turn_id,
            response_id=response_id,
            scopes=frozenset(
                {
                    "tasks:read",
                    "tasks:write",
                    "reminders:read",
                    "reminders:write",
                    *({"memory:read"} if self.settings.memory_retrieval_mode != "off" else set()),
                    *({"memory:write"} if self.settings.memory_write_enabled else set()),
                }
            ),
            confirmed_tool_call_ids=frozenset({claimed.tool_call_id}),
            db=self.db,
            clock=self._trusted_user_clock(),
            user_timezone=claimed.user_timezone,
            timezone_source=claimed.timezone_source,
            device_time_context=self._time_context_for_timezone(claimed.user_timezone),
            cancellation_check=lambda: not self.cancel_guard.can_emit(response_id),
            tool_execution_started=on_confirmation_execution_started,
            tool_execution_audit=self._record_tool_execution_audit,
            memory_settings=self.settings,
            memory_service=memory_service,
        )
        result = await self.tool_loop.executor.execute(call, context=context)
        if not result.success:
            LOGGER.warning(
                "Confirmed tool execution failed",
                extra={
                    "event": "voice.confirmation.execution.failed",
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "original_turn_id": str(claimed.original_turn_id),
                    "response_id": str(response_id),
                    "confirmation_id": str(claimed.confirmation_id),
                    "tool_call_id": claimed.tool_call_id,
                    "tool_name": claimed.tool_name,
                    "error_code": result.error_code,
                    "executed": result.executed,
                    "replayed": result.replayed,
                },
            )
        await self._persist_final_message_if_supported(
            turn_id=claimed.original_turn_id,
            role="tool",
            content=result.content,
            content_json={
                "tool_name": claimed.tool_name,
                "tool_call_id": claimed.tool_call_id,
                "success": result.success,
            },
        )
        if result.success:
            await self.db.commit()
            final_text = self._confirmation_success_text(claimed)
            status = "CONSUMED"
        else:
            await self.db.rollback()
            final_text = self._confirmation_failure_text(claimed)
            status = "CONSUMED"
        await store.transition(
            scope,
            claimed.confirmation_id,
            status,
            result_content=result.content,
        )
        await self._send_tool_status(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            tool_call_id=claimed.tool_call_id,
            tool_name=claimed.tool_name,
            status=(
                "success"
                if result.success
                else "cancelled"
                if result.error_code == "llm_cancelled"
                else "failed"
            ),
            confirmation_id=claimed.confirmation_id,
            error_code=result.error_code,
        )
        await self._send_confirmation_response(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            text=final_text,
            confirmation_id=claimed.confirmation_id,
            status=status,
        )
        await self._record_confirmation_turn(
            turn_id,
            claimed,
            status=status,
            resolution=resolution,
            execution_count=1 if result.executed else 0,
            final_response=final_text,
            execution_success=result.success,
            replayed=result.replayed,
            error_code=result.error_code,
        )
        return {
            "status": "completed",
            "confirmation": "approved",
            "tool_execution_count": 1 if result.executed else 0,
            "database_mutation": result.success and result.executed,
        }

    @staticmethod
    def _confirmation_success_text(pending: PendingConfirmation) -> str:
        if pending.tool_name == "create_task":
            title = str(pending.validated_tool_arguments.get("title", "that task"))
            return f"Done. I created the task {title}."
        if pending.tool_name == "memory_save":
            return "Done. I saved that to memory."
        if pending.tool_name == "memory_forget":
            return "Done. I forgot that memory."
        return f"Done. I completed {pending.tool_name.replace('_', ' ')}."

    @staticmethod
    def _confirmation_failure_text(pending: PendingConfirmation) -> str:
        if pending.tool_name == "create_task":
            return "I couldn't create that task."
        if pending.tool_name == "memory_save":
            return "I couldn't save that to memory."
        if pending.tool_name == "memory_forget":
            return "I couldn't forget that memory."
        return f"I couldn't complete {pending.tool_name.replace('_', ' ')}."

    @staticmethod
    def _confirmation_rejected_text(pending: PendingConfirmation) -> str:
        if pending.tool_name == "create_task":
            return "Okay, I won't create that task."
        if pending.tool_name == "memory_save":
            return "Okay, I won't save that to memory."
        if pending.tool_name == "memory_forget":
            return "Okay, I won't forget that memory."
        return f"Okay, I won't complete {pending.tool_name.replace('_', ' ')}."

    async def _send_confirmation_response(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        text: str,
        confirmation_id: uuid.UUID,
        status: str,
    ) -> None:
        await self._persist_final_message_if_supported(
            turn_id=turn_id,
            role="assistant",
            content=text,
            content_json={
                "confirmation_id": str(confirmation_id),
                "status": status,
            },
            model="confirmation-resolver",
        )
        await self._send(
            server_event(
                "confirmation.resolved",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                confirmation_id=confirmation_id,
                status=status,
            )
        )
        await self._speak_text(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            text=text,
        )
        await self._send(
            server_event(
                "assistant.text.final",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                text=text,
                provider="server",
                model="confirmation-resolver",
                finish_reason="confirmation",
            )
        )

    async def _record_confirmation_turn(
        self,
        turn_id: uuid.UUID,
        pending: PendingConfirmation,
        *,
        status: str,
        resolution: str,
        execution_count: int,
        final_response: str,
        execution_success: bool | None = None,
        replayed: bool = False,
        error_code: str | None = None,
    ) -> None:
        await self.persistence.merge_turn_metadata(
            self.db,
            self.principal,
            turn_id=turn_id,
            metadata={
                "confirmation": {
                    "confirmation_id": str(pending.confirmation_id),
                    "status": status,
                    "resolution": resolution,
                    "tool_name": pending.tool_name,
                    "validated_arguments": pending.validated_tool_arguments,
                    "authorization_at_execution": (
                        "PASS" if execution_success is not False else "PASS"
                    ),
                    "idempotency_key": [str(value) for value in pending.idempotency_key],
                    "tool_execution_count": execution_count,
                    "replayed": replayed,
                    "final_response": final_response,
                    "error_code": error_code,
                }
            },
        )
        await self.db.commit()

    async def _complete_response_state(
        self,
        response_id: uuid.UUID,
        *,
        release_pending: bool = True,
    ) -> None:
        owns_current_response = response_id == self._last_response_id
        if self.voice_session is not None:
            await self.registry.clear_response(
                self.owner,
                self._active_session_id(),
                response_id,
            )
            await self.registry.refresh(self.owner, self._active_session_id())
        if owns_current_response:
            self.cancel_guard.clear(response_id)
            self._response_turn_id = None
            if release_pending:
                await self._create_pending_turn_if_ready()
        else:
            LOGGER.info(
                "Late old response cleanup left replacement turn state intact",
                extra={
                    "event": "OLD_RESPONSE_CLEANUP_COMPLETED",
                    "session_id": str(self._active_session_id()),
                    "old_response_id": str(response_id),
                    "current_response_id": (
                        str(self._last_response_id) if self._last_response_id else None
                    ),
                    "current_turn_id": (
                        str(self.active_turn.id)
                        if self.active_turn is not None
                        else str(self._response_turn_id)
                        if self._response_turn_id
                        else None
                    ),
                    "did_not_touch_new_turn": True,
                },
            )

    def _response_was_cancelled(self, response_id: uuid.UUID) -> bool:
        return response_id in self._cancelled_response_ids or self.cancel_guard.is_cancelled(
            response_id
        )

    def _completion_error_context(
        self,
        *,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        exception: Exception,
    ) -> dict[str, Any]:
        return {
            "event": "VOICE_TURN_COMPLETION_FAILED",
            "session_id": str(self._active_session_id()),
            "turn_id": str(turn_id),
            "response_id": str(response_id),
            "exception": type(exception).__name__,
            "exception_message": str(exception),
            "active_turn_id": str(self.active_turn.id) if self.active_turn else None,
            "active_response_id": str(self._last_response_id) if self._last_response_id else None,
            "response_turn_id": str(self._response_turn_id) if self._response_turn_id else None,
            "stt_finalize_task": self._stt_finalize_task is not None,
            "voice_queue_depth": self.queue.qsize(),
            "cancelled_response": self._response_was_cancelled(response_id),
        }

    @staticmethod
    def _duration_ms(started: float, ended: float | None) -> float | None:
        if ended is None:
            return None
        return round(max(0.0, (ended - started) * 1000), 1)

    def _timing_state(self, turn_id: uuid.UUID) -> TurnTimingState:
        states = getattr(self, "_turn_timings", None)
        if states is None:
            states = {}
            self._turn_timings = states
        state = states.get(turn_id)
        if state is None:
            state = TurnTimingState(points={})
            states[turn_id] = state
        return state

    def _capture_turn_timing(
        self,
        turn_id: uuid.UUID,
        event_name: str,
        *,
        monotonic: float | None = None,
    ) -> None:
        state = self._timing_state(turn_id)
        if event_name in state.points:
            return
        state.points[event_name] = TimingPoint(
            wall=self._now_datetime(),
            monotonic=time.monotonic() if monotonic is None else monotonic,
        )
        event_map = {
            "turn_started_at": "turn_started",
            "speech_started_at": "speech_start",
            "speech_ended_at": "speech_end",
            "stt_started_at": "gateway_stt_processing_start",
            "stt_completed_at": "gateway_stt_final_observed",
            # Provider and TTS adapters own these lifecycle measurements. The
            # gateway copies are correlation observations only and must never
            # be selected as duration endpoints.
            "llm_started_at": "gateway_llm_request_observed",
            "llm_first_token_at": "gateway_llm_first_token_observed",
            "llm_completed_at": "gateway_llm_complete_observed",
            "tts_requested_at": "gateway_tts_request_observed",
            "tts_first_audio_at": "gateway_tts_first_audio_observed",
            "turn_completed_at": "turn_complete",
        }
        trace_event = event_map.get(event_name)
        if trace_event:
            point = state.points[event_name]
            self._trace_latency(
                turn_id=turn_id,
                response_id=state.response_id,
                component="gateway",
                event=trace_event,
                timestamp=point.wall.isoformat().replace("+00:00", "Z"),
                # ``TimingPoint.monotonic`` is the application lifecycle clock
                # used by conversation logging. Trace records use the explicit
                # backend perf-counter domain and are timestamped at emission.
                metadata={"timing_point_clock": "gateway_application_monotonic"},
            )

    def _trace_latency(
        self,
        *,
        component: str,
        event: str,
        session_id: uuid.UUID | None = None,
        turn_id: uuid.UUID | None = None,
        response_id: uuid.UUID | None = None,
        monotonic_ms: float | None = None,
        monotonic_ns: int | None = None,
        timestamp: str | None = None,
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        tracer = getattr(self, "latency_tracer", None) or LatencyTracer()
        tracer.emit(
            session_id=session_id or getattr(self, "_session_id", None),
            turn_id=turn_id,
            response_id=response_id,
            component=component,
            event=event,
            monotonic_ms=monotonic_ms,
            monotonic_ns=monotonic_ns,
            timestamp=timestamp,
            duration_ms=duration_ms,
            metadata=metadata,
        )

    def _tts_timing_state(self, turn_id: uuid.UUID) -> TurnTimingState:
        state = self._timing_state(turn_id)
        if state.tts is None:
            prebuffer_bytes = (
                self.settings.tts_api_sample_rate_hz
                * TTS_PCM_BYTES_PER_SAMPLE
                * TTS_STARTUP_PREBUFFER_MS
                // 1_000
            )
            state.tts = {
                "sample_rate": self.settings.tts_api_sample_rate_hz,
                "channels": 1,
                "encoding": "pcm16",
                "prebuffer_ms": TTS_STARTUP_PREBUFFER_MS,
                "prebuffer_bytes": prebuffer_bytes,
                "frames_sent": 0,
                "pcm_bytes_sent": 0,
                "tts_generation_ms": None,
                "tts_audio_duration_ms": None,
                "tts_rtf": None,
                # These server-side counters describe the emitted stream.
                # Android-only stale-frame and AudioTrack counters remain null
                # until client telemetry is propagated back to the gateway.
                "sequence_gaps": 0,
                "duplicate_frames": 0,
                "stale_frames": None,
                "underrun_delta": None,
            }
        return state

    async def _forward_stt_events(self, turn: STTTurn) -> None:
        try:
            while True:
                event = await turn.events.get()
                if event is None:
                    return
                await self._send_transcript_event(event)
        except asyncio.CancelledError:
            return

    async def _send_transcript_event(self, event: STTTranscriptEvent) -> None:
        LOGGER.info(
            "Voice transcript event sent",
            extra={
                "event": f"voice.{event.event_type}.sent",
                "session_id": str(event.session_id),
                "turn_id": str(event.turn_id),
                "response_id": str(event.response_id),
                "text": event.text,
                "language": event.language,
                "final": event.final,
                "transcript_sequence": event.transcript_sequence,
                "timestamp_ms": int(time.time() * 1000),
                "transcript_timestamp_ms": event.timestamp_ms,
                "metrics": event.metrics,
            },
        )
        await self._send(
            server_event(
                event.event_type,
                session_id=event.session_id,
                turn_id=event.turn_id,
                response_id=event.response_id,
                text=event.text,
                final=event.final,
                transcript_sequence=event.transcript_sequence,
                language=event.language,
                audio_duration_ms=event.audio_duration_ms,
                metrics=event.metrics,
                timestamp_ms=event.timestamp_ms,
            )
        )

    async def _close_stt_turn(self, *, cancel: bool = False) -> None:
        turn = self.stt_turn
        event_task = self._stt_event_task
        self.stt_turn = None
        self._stt_event_task = None
        if turn is not None:
            if cancel:
                await turn.cancel()
            else:
                await turn.close()
        if event_task is not None and event_task is not asyncio.current_task():
            await event_task

    async def _cancel_stt_finalize_task(self) -> None:
        task = self._stt_finalize_task
        self._stt_finalize_task = None
        if task is None or task is asyncio.current_task():
            return
        self._stt_finalize_cancel_requested = True
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _fail_active_turn(self, code: str, *, status: str = "failed") -> None:
        if self.active_turn is None or self.voice_session is None:
            await self._send_error(code)
            return
        try:
            counters = self.state.abort_turn()
        except StateTransitionError:
            counters = None
        if counters is not None:
            await self.persistence.finalize_turn(
                self.db,
                self.principal,
                turn_id=counters.turn_id,
                status=status,
                frame_count=counters.frame_count,
                byte_count=counters.byte_count,
                last_sequence=counters.last_sequence_no if counters.frame_count else None,
                declared_duration_ms=None,
                observed_duration_ms=self._elapsed_turn_ms(),
                error_count=1,
                metadata={"stt_error": code},
            )
            self._add_session_totals(counters.frame_count, counters.byte_count)
        turn_id = self.active_turn.id
        response_id = self.active_turn.response_id
        self.active_turn = None
        self._turn_started = None
        self.cancel_guard.clear()
        await self._close_stt_turn(cancel=True)
        await self.registry.clear_turn(
            self.owner,
            self._active_session_id(),
            turn_id=turn_id,
        )
        await self.registry.clear_response(
            self.owner,
            self._active_session_id(),
            response_id,
        )
        await self.db.commit()
        await self._persist_conversation_log(turn_id, status=status)
        await self._send(
            server_event(
                "server.turn.failed",
                session_id=self._active_session_id(),
                turn_id=turn_id,
                response_id=response_id,
                code=code,
            )
        )

    async def _handle_confirmation_decision(self, message: ConfirmationResolveMessage) -> None:
        """Resolve a server-created confirmation without accepting tool input."""

        self._require_session()
        store = getattr(self, "confirmation_store", None)
        session_id = self._active_session_id()
        if store is None or session_id is None:
            await self._send_error("confirmation_unavailable")
            return
        scope = self._confirmation_scope(session_id)
        pending = await store.get(scope)
        if (
            pending is None
            or pending.confirmation_id != message.confirmation_id
            or pending.tool_call_id != message.tool_call_id
        ):
            await self._send_error("confirmation_not_available")
            return

        # The normal spoken path already owns all validation, authorization,
        # confirmation transitions, idempotency, auditing, and database commit
        # ordering. Reuse it with a bounded server-side yes/no token instead of
        # adding a second execution implementation for UI clients.
        self.cancel_guard.activate(pending.original_response_id)
        self._response_turn_id = pending.original_turn_id
        try:
            await self._resolve_pending_confirmation(
                session_id=session_id,
                turn_id=pending.original_turn_id,
                response_id=pending.original_response_id,
                transcript="yes" if message.decision == "approve" else "no",
            )
        finally:
            self.cancel_guard.clear()
            self._response_turn_id = None

    async def _handle_response_retry(self, message: ResponseRetryMessage) -> None:
        self._require_session()
        if self._stt_finalize_task is not None or self._response_turn_id is not None:
            await self._send_error("response_in_progress")
            return
        if not self.llm_service.enabled or self.voice_session is None:
            await self._send_error("llm_configuration_error")
            return

        turn = await self.persistence.get_owned_turn(
            self.db,
            self.principal,
            session_id=self._active_session_id(),
            turn_id=message.turn_id,
        )
        metadata = turn.metadata_json if turn is not None else None
        stored_transcript = metadata.get("transcript") if metadata else None
        llm_metadata = metadata.get("llm") if metadata else None
        llm_status = llm_metadata.get("status") if isinstance(llm_metadata, dict) else None
        if (
            turn is None
            or turn.response_id != message.original_response_id
            or not isinstance(stored_transcript, str)
            or stored_transcript != message.transcript
            or llm_status not in {"failed", "cancelled"}
        ):
            await self._send_error("response_retry_not_available")
            return

        response_id = uuid.uuid4()
        turn.response_id = response_id
        turn.metadata_json = {
            **(metadata or {}),
            "llm": {
                **(llm_metadata or {}),
                "status": "retrying",
                "original_response_id": str(message.original_response_id),
            },
        }
        self._last_response_id = response_id
        self._response_turn_id = turn.id
        self.cancel_guard.activate(response_id)
        await self.registry.set_response(self.owner, self._active_session_id(), response_id)
        await self.db.commit()
        await self._send(
            server_event(
                "assistant.response.started",
                session_id=self._active_session_id(),
                turn_id=turn.id,
                response_id=response_id,
                retry=True,
            )
        )
        self._retry_response_task = asyncio.create_task(
            self._finish_retry_response(
                session_id=self._active_session_id(),
                turn_id=turn.id,
                response_id=response_id,
                transcript=stored_transcript,
            ),
            name=f"llm-retry-{turn.id}",
        )

    async def _finish_retry_response(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        transcript: str,
    ) -> None:
        try:
            result = await self._stream_llm_response(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                transcript=transcript,
            )
            if result["status"] == "cancelled":
                return
            await self._complete_response_state(response_id, release_pending=False)
            await self._persist_conversation_log(
                turn_id,
                status=("failed" if result["status"] == "failed" else "completed"),
            )
            await self._send(
                server_event(
                    "server.turn.completed",
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    retry=True,
                    llm_enabled=True,
                    llm_status=result["status"],
                )
            )
            await self._create_pending_turn_if_ready()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - isolate retry failures
            await self._complete_response_state(response_id)
            await self._send(
                server_event(
                    "assistant.response.failed",
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    code="llm_provider_error",
                    retryable=True,
                )
            )
        finally:
            if self._retry_response_task is asyncio.current_task():
                self._retry_response_task = None

    async def _handle_response_cancel(self, message: ResponseCancelMessage) -> None:
        self._require_session()
        # A user abort owns the whole continuous-chat pipeline. Barge-in is
        # the one exception: its pending replacement must survive the old
        # response cancellation and be released when cleanup completes.
        if message.reason != "barge_in":
            self._pending_turn_start = None
        active_turn = getattr(self, "active_turn", None)
        old_turn_id = (
            active_turn.id if active_turn is not None else getattr(self, "_response_turn_id", None)
        )
        LOGGER.info(
            "Voice response cancellation received",
            extra={
                "event": "voice.response.cancel.received",
                "session_id": str(self._active_session_id()),
                "response_id": str(message.response_id),
                "reason": message.reason,
                "timestamp_ms": int(time.time() * 1000),
            },
        )
        LOGGER.info(
            "Barge-in cancellation received",
            extra={
                "event": "BARGE_IN_CANCEL_RECEIVED",
                "session_id": str(self._active_session_id()),
                "old_turn_id": str(old_turn_id) if old_turn_id else None,
                "old_response_id": str(message.response_id),
                "reason": message.reason,
            },
        )
        confirmation_store = getattr(self, "confirmation_store", None)
        pending_confirmation = None
        if confirmation_store is not None and self.voice_session is not None:
            pending_confirmation = await confirmation_store.get(
                self._confirmation_scope(self._active_session_id())
            )
        if (
            message.reason != "barge_in"
            and pending_confirmation is not None
            and message.response_id
            in {
                pending_confirmation.original_response_id,
                self._last_response_id,
            }
        ):
            await confirmation_store.transition(
                self._confirmation_scope(self._active_session_id()),
                pending_confirmation.confirmation_id,
                "CANCELLED",
            )
            # A completed action-request response has no active cancellation
            # guard. It still must be possible to cancel its pending mutation.
            if not self.cancel_guard.can_emit(message.response_id):
                await self._send_tool_status(
                    session_id=self._active_session_id(),
                    turn_id=pending_confirmation.original_turn_id,
                    response_id=message.response_id,
                    tool_call_id=pending_confirmation.tool_call_id,
                    tool_name=pending_confirmation.tool_name,
                    status="cancelled",
                    confirmation_id=pending_confirmation.confirmation_id,
                )
                await self._send(
                    server_event(
                        "confirmation.resolved",
                        session_id=self._active_session_id(),
                        response_id=message.response_id,
                        confirmation_id=pending_confirmation.confirmation_id,
                        status="CANCELLED",
                    )
                )
                await self._send(
                    server_event(
                        "response.cancelled",
                        session_id=self._active_session_id(),
                        response_id=message.response_id,
                        reason=message.reason,
                    )
                )
                return
        if message.response_id != self._last_response_id:
            await self._send_error("response_not_active")
            return
        if not self.cancel_guard.cancel(message.response_id):
            await self._send_error("response_not_active")
            return

        self._cancelled_response_ids.add(message.response_id)
        if len(self._cancelled_response_ids) > 64:
            self._cancelled_response_ids = set(list(self._cancelled_response_ids)[-32:])
        self.stats.cancellation_count += 1
        await self._cancel_tts_response(message.response_id)
        # Cancellation must still complete locally when Redis is temporarily
        # unavailable. The durable turn state is committed below, and the
        # registry marker can expire or be cleaned up by the next session.
        with contextlib.suppress(Exception):
            await self.registry.cancel_response(
                self.owner,
                self._active_session_id(),
                message.response_id,
            )
        await self.llm_service.cancel(message.response_id)
        await self._cancel_stt_finalize_task()
        if self.stt_turn is not None:
            await self._close_stt_turn(cancel=True)
        cancelled_turn_id = (
            self.active_turn.id if self.active_turn is not None else self._response_turn_id
        )
        if self.active_turn is not None:
            counters = self.state.abort_turn()
            await self.persistence.finalize_turn(
                self.db,
                self.principal,
                turn_id=counters.turn_id,
                status="cancelled",
                frame_count=counters.frame_count,
                byte_count=counters.byte_count,
                last_sequence=counters.last_sequence_no if counters.frame_count else None,
                declared_duration_ms=None,
                observed_duration_ms=self._elapsed_turn_ms(),
                metadata={"cancel_reason": message.reason},
            )
            self._add_session_totals(counters.frame_count, counters.byte_count)
            self.active_turn = None
            self._turn_started = None
            with contextlib.suppress(Exception):
                await self.registry.clear_turn(
                    self.owner,
                    self._active_session_id(),
                    turn_id=counters.turn_id,
                )
        elif cancelled_turn_id is not None:
            provider_info = self.llm_service.provider_info
            await self.persistence.merge_turn_metadata(
                self.db,
                self.principal,
                turn_id=cancelled_turn_id,
                metadata={
                    "llm": {
                        "status": "cancelled",
                        "provider": (provider_info.provider if provider_info is not None else None),
                        "configured_model": (
                            provider_info.configured_model if provider_info is not None else None
                        ),
                        "prompt_version": VOICE_SYSTEM_PROMPT_VERSION,
                        "cancel_reason": message.reason,
                    }
                },
            )
        self._response_turn_id = None
        self.cancel_guard.clear(message.response_id)
        await self.db.commit()
        if cancelled_turn_id is not None:
            await self._persist_conversation_log(cancelled_turn_id, status="cancelled")
        await self._send(
            server_event(
                "response.cancelled",
                session_id=self._active_session_id(),
                turn_id=cancelled_turn_id,
                response_id=message.response_id,
                reason=message.reason,
            )
        )
        LOGGER.info(
            "Old voice response cancelled",
            extra={
                "event": "OLD_RESPONSE_CANCELLED",
                "session_id": str(self._active_session_id()),
                "old_turn_id": str(cancelled_turn_id) if cancelled_turn_id else None,
                "old_response_id": str(message.response_id),
            },
        )
        await self._create_pending_turn_if_ready()

    async def _handle_response_abort_all(self, message: ResponseAbortAllMessage) -> None:
        self._require_session()
        await self._abort_all_responses(reason=message.reason, emit_cancelled=True)

    async def _abort_all_responses(self, *, reason: str, emit_cancelled: bool) -> None:
        """Cancel the active response and discard any queued replacement turn."""

        self._pending_turn_start = None
        active_turn = self.active_turn
        cancelled_turn_id = (
            active_turn.id if active_turn is not None else self._response_turn_id
        )
        response_ids: set[uuid.UUID] = set()
        if self._last_response_id is not None:
            response_ids.add(self._last_response_id)
        if active_turn is not None:
            response_ids.add(active_turn.response_id)

        for response_id in response_ids:
            self._cancelled_response_ids.add(response_id)
            await self._cancel_tts_response(response_id)
            with contextlib.suppress(Exception):
                await self.registry.cancel_response(
                    self.owner,
                    self._active_session_id(),
                    response_id,
                )
            with contextlib.suppress(Exception):
                await self.llm_service.cancel(response_id)

        await self._cancel_stt_finalize_task()
        if self.stt_turn is not None:
            await self._close_stt_turn(cancel=True)

        if active_turn is not None:
            try:
                counters = self.state.abort_turn()
            except StateTransitionError:
                counters = None
            if counters is not None:
                await self.persistence.finalize_turn(
                    self.db,
                    self.principal,
                    turn_id=counters.turn_id,
                    status="cancelled",
                    frame_count=counters.frame_count,
                    byte_count=counters.byte_count,
                    last_sequence=counters.last_sequence_no if counters.frame_count else None,
                    declared_duration_ms=None,
                    observed_duration_ms=self._elapsed_turn_ms(),
                    metadata={"cancel_reason": reason},
                )
                self._add_session_totals(counters.frame_count, counters.byte_count)
                with contextlib.suppress(Exception):
                    await self.registry.clear_turn(
                        self.owner,
                        self._active_session_id(),
                        turn_id=counters.turn_id,
                    )
        elif self._response_turn_id is not None:
            provider_info = self.llm_service.provider_info
            await self.persistence.merge_turn_metadata(
                self.db,
                self.principal,
                turn_id=self._response_turn_id,
                metadata={
                    "llm": {
                        "status": "cancelled",
                        "provider": provider_info.provider if provider_info is not None else None,
                        "configured_model": (
                            provider_info.configured_model if provider_info is not None else None
                        ),
                        "prompt_version": VOICE_SYSTEM_PROMPT_VERSION,
                        "cancel_reason": reason,
                    }
                },
            )

        if cancelled_turn_id is not None:
            self.active_turn = None
            self._turn_started = None
            with contextlib.suppress(Exception):
                await self.registry.clear_turn(
                    self.owner,
                    self._active_session_id(),
                    turn_id=cancelled_turn_id,
                )
            await self._persist_conversation_log(cancelled_turn_id, status="cancelled")

        for response_id in response_ids:
            self.cancel_guard.clear(response_id)
            with contextlib.suppress(Exception):
                await self.registry.clear_response(
                    self.owner,
                    self._active_session_id(),
                    response_id,
                )
        self._response_turn_id = None
        self._last_response_id = None
        self._turn_started = None
        await self.db.commit()

        if emit_cancelled and response_ids:
            response_id = next(iter(response_ids))
            await self._send(
                server_event(
                    "response.cancelled",
                    session_id=self._active_session_id(),
                    turn_id=cancelled_turn_id,
                    response_id=response_id,
                    reason=reason,
                )
            )

    async def _handle_ping(self, message: ClientPingMessage) -> None:
        self.stats.heartbeat_count += 1
        if self.voice_session is not None:
            self.voice_session.last_activity_at = self._now_datetime()
            await self.registry.refresh(self.owner, self._active_session_id())
        await self._send(
            server_event(
                "server.pong",
                session_id=self._active_session_id(),
                client_timestamp_ms=message.client_timestamp_ms,
                server_timestamp_ms=int(time.time() * 1000),
            )
        )

    async def _handle_session_end(self, message: SessionEndMessage) -> None:
        self._require_session()
        self._session_status = "completed"
        self._close_code = 1000
        self._close_reason = message.reason
        await self._send(
            server_event(
                "server.session.ending",
                session_id=self._active_session_id(),
                reason=message.reason,
            )
        )
        self._closing.set()

    async def _watchdog_loop(self) -> None:
        interval = min(max(self.settings.voice_heartbeat_interval_seconds, 1), 5)
        try:
            while not self._closing.is_set():
                await asyncio.sleep(interval)
                now = time.monotonic()
                if now - self._last_activity > self.settings.voice_idle_timeout_seconds:
                    await self._timeout("idle_timeout")
                    return
                if now - self._last_ping > self.settings.voice_heartbeat_timeout_seconds:
                    await self._timeout("heartbeat_timeout")
                    return
                if now - self._connection_started > self.settings.voice_max_session_seconds:
                    await self._timeout("session_timeout")
                    return
                if (
                    self._turn_started is not None
                    and self._stt_finalize_task is None
                    and now - self._turn_started > self.settings.voice_max_turn_seconds
                ):
                    await self._timeout("turn_timeout")
                    return
                if now - self._last_auth_check >= self.settings.voice_heartbeat_interval_seconds:
                    self._last_auth_check = now
                    if not await self._auth_still_valid():
                        return
        except asyncio.CancelledError:
            return

    async def _auth_still_valid(self) -> bool:
        try:
            async with self.session_factory() as auth_db:
                await self.auth_service.resolve_access_token(
                    auth_db,
                    self.access_token,
                )
        except (AuthenticationError, AuthConfigurationError):
            LOGGER.warning(
                "VOICE_AUTH_REVALIDATION_FAILED",
                extra={
                    "event": "voice.auth.revalidation.failed",
                    "reason": "authentication_expired_or_revoked",
                    "session_id": str(self._active_session_id())
                    if self._active_session_id()
                    else None,
                    "user_id": str(self.principal.user_id),
                    "device_id": str(self.principal.device_id),
                    "monotonic_ms": round(time.monotonic() * 1000, 1),
                },
            )
            await self._protocol_failure("authentication_expired_or_revoked", close_code=1008)
            return False
        except SQLAlchemyError:
            LOGGER.warning(
                "VOICE_AUTH_REVALIDATION_UNAVAILABLE",
                extra={
                    "event": "voice.auth.revalidation.failed",
                    "reason": "authentication_service_unavailable",
                    "session_id": str(self._active_session_id())
                    if self._active_session_id()
                    else None,
                    "user_id": str(self.principal.user_id),
                    "device_id": str(self.principal.device_id),
                    "monotonic_ms": round(time.monotonic() * 1000, 1),
                },
            )
            await self._protocol_failure("authentication_revalidation_unavailable", close_code=1013)
            return False
        return True

    async def _timeout(self, reason: str) -> None:
        self._session_status = "timed_out"
        self._close_code = 1000
        self._close_reason = reason
        await self._send_error(f"voice_{reason}")
        self._closing.set()
        try:
            await self.websocket.close(code=1000, reason=reason[:120])
        except (RuntimeError, WebSocketDisconnect):
            pass

    async def _protocol_failure(self, code: str, *, close_code: int = 1002) -> None:
        if self._closing.is_set():
            return
        self.stats.error_count += 1
        self._session_status = "failed"
        self._close_code = close_code
        self._close_reason = code
        await self._send_error(code)
        self._closing.set()
        try:
            await self.websocket.close(code=close_code, reason=code[:120])
        except (RuntimeError, WebSocketDisconnect):
            pass

    async def _send_error(self, code: str) -> None:
        await self._send(
            server_event(
                "server.error",
                session_id=self._active_session_id(),
                code=code,
                message="Voice gateway request rejected.",
            )
        )

    def _log_connection_closed(self) -> None:
        if self._connection_close_logged:
            return
        self._connection_close_logged = True
        LOGGER.info(
            "Voice WebSocket connection closed",
            extra={
                "event": "voice.connection.closed",
                "session_id": str(self._active_session_id()) if self._active_session_id() else None,
                "close_code": self._close_code,
                "close_reason": self._close_reason or "connection_closed",
                "backend_pid": os.getpid(),
                "monotonic_ms": round(time.monotonic() * 1000, 1),
            },
        )

    async def _send(self, event: dict[str, Any]) -> None:
        if self._closing.is_set() and event.get("type") not in {
            "server.error",
            "server.session.ended",
        }:
            return
        trace_send_lock = event.get("type") == "transcript.final"
        wait_started_ns = time.perf_counter_ns() if trace_send_lock else None
        if trace_send_lock and wait_started_ns is not None:
            self._trace_latency(
                session_id=event.get("session_id") or self._active_session_id(),
                turn_id=event.get("turn_id")
                or (self.active_turn.id if self.active_turn is not None else None),
                response_id=event.get("response_id")
                or (self.active_turn.response_id if self.active_turn is not None else None),
                component="gateway_queue",
                event="transcript_send_lock_wait_started",
                monotonic_ns=wait_started_ns,
                metadata={"lock": "websocket_send"},
            )
        acquired = False
        try:
            await self._send_lock.acquire()
            acquired = True
            if trace_send_lock and wait_started_ns is not None:
                acquired_ns = time.perf_counter_ns()
                wait_ms = (acquired_ns - wait_started_ns) / 1_000_000
                self._trace_latency(
                    session_id=event.get("session_id") or self._active_session_id(),
                    turn_id=event.get("turn_id")
                    or (self.active_turn.id if self.active_turn is not None else None),
                    response_id=event.get("response_id")
                    or (self.active_turn.response_id if self.active_turn is not None else None),
                    component="gateway_queue",
                    event="transcript_send_lock_acquired",
                    monotonic_ns=acquired_ns,
                    duration_ms=wait_ms,
                    metadata={"lock": "websocket_send", "queue_wait_ms": wait_ms},
                )
            try:
                await self.websocket.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                self._closing.set()
        finally:
            if acquired:
                self._send_lock.release()

    async def shutdown(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._closing.set()
        self._log_connection_closed()
        current = asyncio.current_task()
        gateway_tasks = [
            task
            for task in (self._processor_task, self._watchdog_task, self._receive_task)
            if task is not None and task is not current
        ]
        for task in gateway_tasks:
            if task is not None and task is not current and not task.done():
                task.cancel()
        if gateway_tasks:
            # Do not let the route's database-session context close while a
            # cancelled processor still owns work that can touch that session.
            await asyncio.gather(*gateway_tasks, return_exceptions=True)
        await self._cancel_stt_finalize_task()
        retry_task = self._retry_response_task
        self._retry_response_task = None
        if retry_task is not None and retry_task is not current:
            if not retry_task.done():
                retry_task.cancel()
            await asyncio.gather(retry_task, return_exceptions=True)
        for response_id, tts_task in list(self._tts_tasks.items()):
            if tts_task is not current and not tts_task.done():
                tts_task.cancel()
            await asyncio.gather(tts_task, return_exceptions=True)
            self._tts_tasks.pop(response_id, None)
        if self.stt_turn is not None:
            await self._close_stt_turn(cancel=True)
        await self._finalize_active_turn()
        if self.voice_session is not None:
            confirmation_store = getattr(self, "confirmation_store", None)
            if confirmation_store is not None:
                with contextlib.suppress(Exception):
                    await confirmation_store.cancel_scope(
                        self._confirmation_scope(self._active_session_id())
                    )
            await self.persistence.finalize_session(
                self.db,
                self.principal,
                session_id=self._active_session_id(),
                status=self._session_status,
                close_code=self._close_code,
                close_reason=self._close_reason,
                total_frames=self._session_total_frames,
                total_bytes=self._session_total_bytes,
                error_count=self.stats.error_count,
            )
            record_audit(
                self.db,
                "VOICE_SESSION_ENDED",
                user_id=self.principal.user_id,
                device_id=self.principal.device_id,
                metadata={
                    "session_id": str(self._active_session_id()),
                    "status": self._session_status,
                    "frames": self.stats.frames_accepted,
                    "queue_high_water_mark": self.stats.queue_high_water_mark,
                    "queue_overflow_count": self.stats.queue_overflow_count,
                },
                request=self.websocket,
            )
            await self.db.commit()
            try:
                await self.registry.release(self.owner, self._active_session_id())
            except VoiceRegistryError:
                pass
            else:
                LOGGER.info(
                    "Voice session registry released",
                    extra={
                        "event": "voice.session.registry.released",
                        "session_id": str(self._active_session_id()),
                        "user_id": str(self.principal.user_id),
                        "device_id": str(self.principal.device_id),
                        "timestamp_ms": int(time.time() * 1000),
                        "monotonic_ms": round(time.monotonic() * 1000, 1),
                    },
                )
            await self._send(
                server_event(
                    "server.session.ended",
                    session_id=self._active_session_id(),
                    reason=self._close_reason or "connection_closed",
                )
            )
        self.state.close()

    async def _finalize_active_turn(self) -> None:
        if self.active_turn is None or self.voice_session is None:
            return
        try:
            counters = self.state.abort_turn()
        except StateTransitionError:
            self.active_turn = None
            return
        await self.persistence.finalize_turn(
            self.db,
            self.principal,
            turn_id=counters.turn_id,
            status="timed_out" if self._session_status == "timed_out" else "disconnected",
            frame_count=counters.frame_count,
            byte_count=counters.byte_count,
            last_sequence=counters.last_sequence_no if counters.frame_count else None,
            declared_duration_ms=None,
            observed_duration_ms=self._elapsed_turn_ms(),
            error_count=self.stats.error_count,
            gap_count=self.stats.gap_count,
            duplicate_count=self.stats.duplicate_frame_count,
        )
        self._add_session_totals(counters.frame_count, counters.byte_count)
        self.active_turn = None
        self._turn_started = None

    def _require_session(self) -> None:
        if self.voice_session is None or self.state.session_id is None:
            raise StateTransitionError("voice session is not ready")

    def _active_session_id(self) -> uuid.UUID | None:
        """Return the cached session identity without touching expired ORM state."""

        session_id = getattr(self, "_session_id", None)
        if session_id is not None:
            return session_id
        state = getattr(self, "state", None)
        state_session_id = getattr(state, "session_id", None)
        if state_session_id is not None:
            return state_session_id
        return None

    def _add_session_totals(self, frame_count: int, byte_count: int) -> None:
        """Update durable totals and scalar mirrors used by error/cleanup paths."""

        total_frames = getattr(self, "_session_total_frames", 0)
        total_bytes = getattr(self, "_session_total_bytes", 0)
        self._session_total_frames = total_frames + frame_count
        self._session_total_bytes = total_bytes + byte_count
        if self.voice_session is not None:
            self.voice_session.total_frames = self._session_total_frames
            self.voice_session.total_bytes = self._session_total_bytes

    def _elapsed_turn_ms(self) -> int | None:
        if self._turn_started is None:
            return None
        return max(0, int((time.monotonic() - self._turn_started) * 1000))

    def _safe_llm_session_info(self) -> dict[str, Any]:
        info = self.llm_service.provider_info
        if not self.llm_service.enabled or info is None:
            return {"enabled": False}
        return {
            "enabled": True,
            "provider": info.provider,
            "model": info.configured_model,
            "api_family": info.api_family,
            "live_verified": info.live_verified,
            "capabilities": info.capabilities.model_dump(),
        }

    def _now_datetime(self):
        return self._application_clock().now_utc()

    def _application_clock(self) -> Clock:
        """Return the server clock used for persistence and session lifecycle."""

        return getattr(self, "clock", SystemClock())

    def _trusted_user_clock(self) -> Clock:
        """Return device time for user-facing temporal tools when available."""

        return getattr(self, "_device_clock", None) or self._application_clock()

    def _refresh_device_time_context(
        self,
        payload: DeviceTimeContextPayload | None,
        *,
        legacy_timezone: str | None = None,
    ) -> None:
        payload_value = payload.model_dump() if payload is not None else None
        self._device_time_context = build_device_time_context(
            payload_value,
            fallback_clock=getattr(self, "clock", SystemClock()),
            fallback_timezone=self.settings.voice_default_timezone,
            legacy_timezone=legacy_timezone,
        )
        self._device_clock = DeviceEpochClock(self._device_time_context.device_epoch_ms)
        LOGGER.info(
            "TIME_CONTEXT",
            extra={
                "event": "time.context",
                "source": self._device_time_context.source,
                "timezone": self._device_time_context.timezone_id,
                "locale": self._device_time_context.locale,
                "device_epoch_ms": self._device_time_context.device_epoch_ms,
                "user_id": str(self.principal.user_id),
                "device_id": str(self.principal.device_id),
                "session_id": str(self._session_id) if self._session_id else None,
            },
        )

    def _time_context_for_timezone(self, timezone_name: str) -> DeviceTimeContext | None:
        context = getattr(self, "_device_time_context", None)
        if context is None:
            return None
        valid_name = valid_timezone(timezone_name) or context.timezone_id
        zone = context.zone if valid_name == context.timezone_id else ZoneInfo(valid_name)
        offset = context.instant_utc.astimezone(zone).utcoffset()
        return DeviceTimeContext(
            device_epoch_ms=context.device_epoch_ms,
            timezone_id=valid_name,
            utc_offset=format_utc_offset(offset),
            locale=context.locale,
            source=context.source,
        )

    def _user_timezone(self) -> str:
        device_context = getattr(self, "_device_time_context", None)
        if device_context is not None:
            return device_context.timezone_id
        voice_session = getattr(self, "voice_session", None)
        metadata = (
            getattr(voice_session, "client_metadata", None) if voice_session is not None else None
        )
        if isinstance(metadata, dict):
            timezone_name = metadata.get("timezone")
            if isinstance(timezone_name, str) and timezone_name.strip():
                return timezone_name.strip()
        return self.settings.voice_default_timezone.strip()

    async def _persist_final_message_if_supported(
        self,
        *,
        turn_id: uuid.UUID,
        role: str,
        content: str,
        content_json: dict[str, Any] | None = None,
        model: str | None = None,
    ):
        persist = getattr(self.persistence, "persist_final_message", None)
        if persist is None:
            return None
        message, _ = await persist(
            self.db,
            self.principal,
            turn_id=turn_id,
            role=role,
            content=content,
            content_json=content_json,
            model=model,
        )
        return message

    async def _run_tts_queue(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        queue: asyncio.Queue[str | None],
    ) -> None:
        sequence = 0
        started = False
        first_audio_traced = False
        sent_pcm_bytes = 0
        pacing_started_at: float | None = None
        generation_ms_total = 0.0
        audio_duration_ms_total = 0.0
        try:
            while True:
                sentence = await queue.get()
                if sentence is None:
                    break
                if not self.cancel_guard.can_emit(response_id):
                    raise TTSCancelledError("TTS response is no longer current")
                self._capture_turn_timing(turn_id, "tts_requested_at")
                self._trace_latency(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    component="tts",
                    event="gateway_tts_request_observed",
                    metadata={
                        "sentence_bytes": len(sentence.encode("utf-8")),
                        "duration_basis": "correlation_only",
                    },
                )
                async for chunk in self.tts_service.stream(
                    text=sentence,
                    response_id=str(response_id),
                    session_id=str(session_id),
                    turn_id=str(turn_id),
                ):
                    if not self.cancel_guard.can_emit(response_id):
                        raise TTSCancelledError("TTS response is no longer current")
                    if not started:
                        started = True
                        self._tts_timing_state(turn_id)
                        LOGGER.info(
                            "TTS_START",
                            extra={
                                "event": "tts.start",
                                "session_id": str(session_id),
                                "turn_id": str(turn_id),
                                "response_id": str(response_id),
                                "sample_rate_hz": self.settings.tts_api_sample_rate_hz,
                                "channels": 1,
                                "encoding": "pcm16",
                            },
                        )
                        await self._send_tts_event(
                            "tts.started",
                            session_id=session_id,
                            turn_id=turn_id,
                            response_id=response_id,
                        )
                    if chunk and not first_audio_traced:
                        first_audio_traced = True
                        self._capture_turn_timing(turn_id, "tts_first_audio_at")
                        self._trace_latency(
                            session_id=session_id,
                            turn_id=turn_id,
                            response_id=response_id,
                            component="tts",
                            event="gateway_tts_first_audio_observed",
                            metadata={
                                "chunk_bytes": len(chunk),
                                "duration_basis": "correlation_only",
                            },
                        )
                    await self._send_tts_audio(
                        session_id=session_id,
                        turn_id=turn_id,
                        response_id=response_id,
                        sequence=sequence,
                        sample_rate_hz=self.settings.tts_api_sample_rate_hz,
                        flags=TTS_FRAME_START if sequence == 0 else 0,
                        payload=chunk,
                    )
                    sequence += 1
                    if chunk:
                        timing_state = self._tts_timing_state(turn_id)
                        timing = timing_state.tts
                        assert timing is not None
                        timing["frames_sent"] += 1
                        timing["pcm_bytes_sent"] += len(chunk)
                        if pacing_started_at is None:
                            pacing_started_at = time.monotonic()
                        sent_pcm_bytes += len(chunk)
                        delay = tts_pacing_delay_seconds(
                            sent_pcm_bytes=sent_pcm_bytes,
                            started_at=pacing_started_at,
                            now=time.monotonic(),
                            sample_rate_hz=self.settings.tts_api_sample_rate_hz,
                        )
                        if delay > 0:
                            await asyncio.sleep(delay)
                stream_metrics = getattr(self.tts_service, "last_stream_metrics", None)
                if stream_metrics is not None:
                    generation_ms_total += stream_metrics.generation_ms
                    audio_duration_ms_total += stream_metrics.audio_duration_ms
            if started and self.cancel_guard.can_emit(response_id):
                timing_state = self._tts_timing_state(turn_id)
                timing = timing_state.tts
                assert timing is not None
                timing["tts_generation_ms"] = (
                    round(generation_ms_total, 3) if generation_ms_total > 0 else None
                )
                timing["tts_audio_duration_ms"] = (
                    round(audio_duration_ms_total, 3) if audio_duration_ms_total > 0 else None
                )
                timing["tts_rtf"] = (
                    round(generation_ms_total / audio_duration_ms_total, 6)
                    if generation_ms_total > 0 and audio_duration_ms_total > 0
                    else None
                )
                LOGGER.info(
                    "TTS_RESPONSE_METRICS",
                    extra={
                        "event": "tts.response.metrics",
                        "session_id": str(session_id),
                        "turn_id": str(turn_id),
                        "response_id": str(response_id),
                        "tts_generation_ms": timing["tts_generation_ms"],
                        "tts_audio_duration_ms": timing["tts_audio_duration_ms"],
                        "tts_rtf": timing["tts_rtf"],
                        "pcm_bytes": timing["pcm_bytes_sent"],
                    },
                )
                await self._send_tts_audio(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    sequence=sequence,
                    sample_rate_hz=self.settings.tts_api_sample_rate_hz,
                    flags=TTS_FRAME_END,
                    payload=b"",
                )
                await self._send_tts_event(
                    "tts.completed",
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                )
                self._trace_latency(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    component="tts",
                    event="gateway_tts_generation_observed",
                    metadata={"duration_basis": "correlation_only"},
                )
        except TTSCancelledError:
            if started:
                await self._send_tts_event(
                    "tts.cancelled",
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                )
        except TTSError as error:
            await self._send_tts_event(
                "tts.failed",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                code=type(error).__name__,
            )
        except Exception:  # noqa: BLE001 - TTS must not fail the LLM response
            LOGGER.exception(
                "TTS response failed",
                extra={
                    "event": "tts.response.failed",
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                    "response_id": str(response_id),
                },
            )
            await self._send_tts_event(
                "tts.failed",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                code="tts_failed",
            )

    async def _speak_text(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        text: str,
    ) -> None:
        tts_service = getattr(self, "tts_service", None)
        if tts_service is None or not tts_service.enabled:
            return
        active_queue = getattr(self, "_tts_queues", {}).get(response_id)
        if active_queue is not None:
            segmenter = SentenceSegmenter(max_chars=self.settings.tts_max_sentence_chars)
            for sentence in segmenter.push(text) + segmenter.flush():
                await active_queue.put(sentence)
            return
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        segmenter = SentenceSegmenter(max_chars=self.settings.tts_max_sentence_chars)
        for sentence in segmenter.push(text) + segmenter.flush():
            await queue.put(sentence)
        await queue.put(None)
        await self._run_tts_queue(
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
            queue=queue,
        )

    async def _send_tts_event(
        self,
        event_type: str,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        code: str | None = None,
    ) -> None:
        await self._send(
            server_event(
                event_type,
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                code=code,
                sample_rate_hz=self.settings.tts_api_sample_rate_hz,
            )
        )

    async def _send_tts_audio(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
        sequence: int,
        sample_rate_hz: int,
        flags: int,
        payload: bytes,
    ) -> None:
        frame = (
            struct.pack(
                ">4sBBII16s",
                TTS_FRAME_MAGIC,
                TTS_FRAME_VERSION,
                flags,
                sample_rate_hz,
                sequence,
                response_id.bytes,
            )
            + payload
        )
        if self._closing.is_set():
            return
        async with self._send_lock:
            try:
                await self.websocket.send_bytes(frame)
                LOGGER.info(
                    "TTS PCM frame sent",
                    extra={
                        "event": "tts.frame.sent",
                        "session_id": str(session_id),
                        "turn_id": str(turn_id),
                        "response_id": str(response_id),
                        "sequence": sequence,
                        "payload_bytes": len(payload),
                        "flags": flags,
                        "sample_rate_hz": sample_rate_hz,
                    },
                )
            except (RuntimeError, WebSocketDisconnect, OSError) as error:
                LOGGER.warning(
                    "TTS_SEND_FAILURE",
                    extra={
                        "event": "tts.send.failed",
                        "session_id": str(session_id),
                        "turn_id": str(turn_id),
                        "response_id": str(response_id),
                        "sequence": sequence,
                        "exception": type(error).__name__,
                        "message": str(error)[:240],
                    },
                )
                self._closing.set()

    async def _persist_conversation_log(
        self,
        turn_id: uuid.UUID,
        *,
        status: str,
    ) -> None:
        """Project a committed turn without affecting the voice response path."""

        session_id = self._active_session_id()
        if session_id is None:
            return
        self._capture_turn_timing(turn_id, "turn_completed_at")
        timing_state = self._timing_state(turn_id)
        timing_payload = build_timing_payload(timing_state.points, tts=timing_state.tts)
        try:
            await self.conversation_logger.persist_turn(
                self.db,
                user_id=self.principal.user_id,
                device_id=self.principal.device_id,
                session_id=session_id,
                turn_id=turn_id,
                enabled=self.settings.conversation_logging_enabled,
                status=status,
                timing_payload=timing_payload,
            )
        except Exception:  # noqa: BLE001 - logging must never break voice delivery
            LOGGER.exception(
                "Conversation log projection failed",
                extra={
                    "event": "conversation.log.failed",
                    "user_id": str(self.principal.user_id),
                    "device_id": str(self.principal.device_id),
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                },
            )
        finally:
            # A terminal turn must not leave timing state available to a
            # future turn or reconnect. The persisted JSONL record is the
            # authoritative copy.
            getattr(self, "_turn_timings", {}).pop(turn_id, None)

    async def _cancel_tts_response(self, response_id: uuid.UUID) -> None:
        task = self._tts_tasks.get(response_id)
        if task is None or task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._tts_tasks.pop(response_id, None)

    async def _memory_user_enabled(self) -> bool:
        try:
            async with self.db.begin_nested():
                user = await self.db.scalar(select(User).where(User.id == self.principal.user_id))
        except SQLAlchemyError:
            return False
        return bool(user is not None and user.memory_enabled)

    async def _memory_excluded_for_session(self) -> bool:
        """Read exclusion from the owner-scoped row, not a stale gateway cache."""

        session_id = self._active_session_id()
        if session_id is None:
            metadata = getattr(self, "_session_client_metadata", None)
            if metadata is None:
                voice_session = getattr(self, "voice_session", None)
                metadata = getattr(voice_session, "client_metadata", None)
            return isinstance(metadata, dict) and metadata.get("memory_excluded") is True
        try:
            async with self.db.begin_nested():
                row = (
                    await self.db.execute(
                        select(VoiceSession.client_metadata).where(
                            VoiceSession.id == session_id,
                            VoiceSession.user_id == self.principal.user_id,
                        )
                    )
                ).first()
        except SQLAlchemyError:
            # A failed policy lookup must fail closed for memory reads/writes.
            return True
        if row is None:
            return True
        metadata = row[0]
        fresh_metadata = metadata if isinstance(metadata, dict) else {}
        self._session_client_metadata = fresh_metadata
        # Keep the ORM object aligned for the next gateway commit while
        # preserving locally staged device/time metadata from this connection.
        voice_session = getattr(self, "voice_session", None)
        if voice_session is not None:
            local_metadata = dict(voice_session.client_metadata or {})
            if "memory_excluded" in fresh_metadata:
                local_metadata["memory_excluded"] = fresh_metadata["memory_excluded"]
            else:
                local_metadata.pop("memory_excluded", None)
            voice_session.client_metadata = local_metadata
        return fresh_metadata.get("memory_excluded") is True

    async def _conversation_history_for_turn(
        self,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
    ) -> tuple[LLMMessage, ...]:
        """Load a bounded, owner-scoped history window for the next prompt."""

        list_history = getattr(MemoryRepository, "list_recent_session_messages", None)
        if list_history is None:
            return ()
        try:
            messages = await list_history(
                MemoryRepository(),
                self.db,
                self.principal,
                session_id=session_id,
                exclude_turn_id=turn_id,
                limit=20,
            )
        except Exception:  # noqa: BLE001 - history is an enhancement, not a blocker
            LOGGER.warning(
                "Voice conversation history unavailable; continuing without it",
                extra={
                    "event": "voice.conversation_history.unavailable",
                    "session_id": str(session_id),
                    "turn_id": str(turn_id),
                },
            )
            return ()
        return tuple(
            LLMMessage(role=LLMRole(message.role), content=message.content or "")
            for message in messages
            if message.role in {"user", "assistant"} and (message.content or "").strip()
        )

    async def _memory_context_for_transcript(
        self,
        transcript: str,
        *,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        response_id: uuid.UUID,
    ) -> str | None:
        if self.settings.memory_retrieval_mode == "off":
            return None
        service = getattr(self.websocket.app.state, "memory_service", None)
        if service is None or await self._memory_excluded_for_session():
            return None
        with latency_span(
            self._trace_latency,
            component="postgres",
            event="memory_retrieval_policy_lookup",
            session_id=session_id,
            turn_id=turn_id,
            response_id=response_id,
        ):
            memory_enabled = await self._memory_user_enabled()
        if not memory_enabled:
            return None

        def trace_retrieval_stage(stage: str, duration_ms: float, metadata: dict[str, Any]) -> None:
            ended_ns = time.perf_counter_ns()
            started_ns = int(metadata.pop("started_monotonic_ns", 0)) or int(
                ended_ns - max(0.0, duration_ms) * 1_000_000
            )
            if stage.endswith("_db_query"):
                metadata["execution_ms"] = duration_ms
                metadata["db_pool_wait_included"] = True
                metadata["db_pool_wait_separately_measured"] = False
            self._trace_latency(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                component="rag",
                event=f"{stage}_started",
                monotonic_ns=started_ns,
            )
            self._trace_latency(
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                component="rag",
                event=f"{stage}_completed",
                monotonic_ns=ended_ns,
                duration_ms=duration_ms,
                metadata=metadata,
            )

        try:
            with latency_span(
                self._trace_latency,
                component="rag",
                event="memory_context_pipeline",
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
            ):
                result = await service.retrieve(
                    self.db,
                    user_id=self.principal.user_id,
                    query=transcript,
                    now=self._now_datetime(),
                    trace=trace_retrieval_stage,
                )
        except (MemoryProviderError, SQLAlchemyError):
            # Memory is an enhancement; a provider outage must not prevent
            # the committed transcript from reaching the configured LLM.
            return None
        if self.settings.memory_retrieval_mode == "shadow" or not result.memories:
            return None
        return (
            assemble_context(
                result.memories,
                max_chars=self.settings.memory_context_max_chars,
            ).text
            or None
        )


def _safe_client_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "client_version",
        "app_build",
        "platform",
        "locale",
        "timezone",
        "memory_excluded",
    }
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in allowed:
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            result[key] = str(value)[:128] if isinstance(value, str) else value
    return result


def _confirmation_due_at_utc(arguments: dict[str, Any]) -> str | None:
    value = arguments.get("due_at")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC).isoformat()


def _confirmation_due_at_local(pending: PendingConfirmation) -> str | None:
    due_at_utc = _confirmation_due_at_utc(pending.validated_tool_arguments)
    if due_at_utc is None:
        return None
    try:
        return format_local_due_at(datetime.fromisoformat(due_at_utc), pending.user_timezone)
    except (TypeError, ValueError):
        return None
