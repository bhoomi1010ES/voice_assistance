from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.llm.context import VOICE_SYSTEM_PROMPT_VERSION, build_voice_llm_request
from app.llm.errors import LLMError
from app.llm.service import LLMService
from app.llm.tool_loop import (
    LLMToolLoop,
    ToolExecutionContext,
    ToolIdempotencyStore,
    ToolRegistry,
    create_default_tool_registry,
)
from app.llm.types import LLMEvent, LLMUsage
from app.models import ConversationTurn, VoiceSession
from app.services.audit import record_audit
from app.services.auth import (
    AuthConfigurationError,
    AuthenticationError,
    AuthPrincipal,
    AuthService,
)
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
from app.stt.base import STTCancelledError, STTError
from app.stt.service import (
    STTService,
    STTTranscriptEvent,
    STTTranscriptResult,
    STTTurn,
)
from app.websocket.binary import BinaryPcmFrame, BinaryProtocolError, decode_pcm_frame
from app.websocket.cancellation import CancellationGuard
from app.websocket.protocol import (
    AudioCommitMessage,
    ClientPingMessage,
    ControlMessageType,
    ProtocolError,
    ResponseCancelMessage,
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
        self.tool_registry = tool_registry or create_default_tool_registry()
        self.tool_loop = LLMToolLoop(
            settings,
            llm_service,
            self.tool_registry,
            idempotency_store=tool_idempotency_store
            or PostgresToolIdempotencyStore(db),
        )
        self.persistence = VoicePersistence()
        self.registry = VoiceRegistry(
            websocket.app.state.infrastructure.redis,
            ttl_seconds=settings.voice_max_session_seconds + settings.voice_reconnect_grace_seconds,
            lease_ttl_seconds=(
                settings.voice_heartbeat_timeout_seconds
                + settings.voice_reconnect_grace_seconds
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
        self.voice_session: VoiceSession | None = None
        self.active_turn: ConversationTurn | None = None
        self.stt_turn: STTTurn | None = None
        self._stt_event_task: asyncio.Task[None] | None = None
        self._stt_finalize_task: asyncio.Task[None] | None = None
        self._stt_finalize_cancel_requested = False
        self._stt_enabled = False
        self._stt_language: str | None = None
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
            await self.shutdown()
            try:
                await self.websocket.close(
                    code=self._close_code or 1000,
                    reason=(self._close_reason or "connection_closed")[:120],
                )
            except RuntimeError:
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
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.stats.queue_overflow_count += 1
            await self._protocol_failure("voice_queue_overflow", close_code=1013)
            return False
        self.stats.queue_high_water_mark = max(self.stats.queue_high_water_mark, self.queue.qsize())
        return True

    async def _process_loop(self) -> None:
        while not self._closing.is_set():
            try:
                item = await self.queue.get()
            except asyncio.CancelledError:
                return
            try:
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
        elif isinstance(message, ClientPingMessage):
            await self._handle_ping(message)
        elif isinstance(message, SessionEndMessage):
            await self._handle_session_end(message)

    async def _handle_session_start(self, message: SessionStartMessage) -> None:
        if self.state.state != VoiceState.AUTHENTICATED:
            raise StateTransitionError("session already started")
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
                client_metadata=_safe_client_metadata(message.client_metadata),
            )

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

    async def _handle_turn_start(self, message: TurnStartMessage) -> None:
        self._require_session()
        if self._stt_finalize_task is not None or self._response_turn_id is not None:
            await self._send_error("response_in_progress")
            return
        if self.active_turn is not None:
            raise StateTransitionError("a turn is already active")
        response_id = uuid.uuid4()
        turn = await self.persistence.create_turn(
            self.db,
            self.principal,
            voice_session=self.voice_session,
            response_id=response_id,
            client_turn_id=message.client_turn_id,
        )
        self.state.start_turn(turn.id, response_id)
        await self.registry.set_turn(self.owner, self.voice_session.id, turn.id, response_id)
        self.active_turn = turn
        self._last_response_id = response_id
        self._turn_started = time.monotonic()
        self.cancel_guard.activate(response_id)
        LOGGER.info(
            "Voice turn started",
            extra={
                "event": "voice.turn.started",
                "session_id": str(self.voice_session.id),
                "turn_id": str(turn.id),
                "response_id": str(response_id),
                "turn_number": turn.turn_number,
                "timestamp_ms": int(time.time() * 1000),
                "monotonic_ms": round(self._turn_started * 1000, 1),
            },
        )
        if self._stt_enabled:
            try:
                self.stt_turn = await self.stt_service.start_turn(
                    session_id=self.voice_session.id,
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
                session_id=self.voice_session.id,
                turn_id=turn.id,
                response_id=response_id,
                turn_number=turn.turn_number,
                sequence_start=0,
            )
        )

    async def _handle_binary(self, frame: BinaryPcmFrame) -> None:
        self._require_session()
        if self._stt_finalize_task is not None:
            await self._send_error("turn_finalizing")
            return
        self.state.accept_frame(frame)
        self.stats.frames_accepted += 1
        self.stats.bytes_received += frame.payload_length
        self.voice_session.last_activity_at = self._now_datetime()
        if frame.sequence_no == 0 or frame.sequence_no % 50 == 0:
            LOGGER.info(
                "Voice PCM frame accepted",
                extra={
                    "event": "voice.pcm.accepted",
                    "session_id": str(self.voice_session.id),
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
        LOGGER.info(
            "Voice audio commit received",
            extra={
                "event": "voice.audio.commit.received",
                "session_id": str(self.voice_session.id),
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
            self._stt_finalize_task = asyncio.create_task(
                self._finish_audio_commit(message, self.stt_turn),
                name=f"stt-finalize-{self.active_turn.id}",
            )
            self._stt_finalize_cancel_requested = False
            return
        await self._finish_audio_commit(message, None)

    async def _finish_audio_commit(
        self,
        message: AudioCommitMessage,
        stt_turn: STTTurn | None,
    ) -> None:
        stt_result: STTTranscriptResult | None = None
        stt_error: STTError | None = None
        try:
            LOGGER.info(
                "Voice turn finalization started",
                extra={
                    "event": "voice.turn.finalization.started",
                    "session_id": str(self.voice_session.id),
                    "turn_id": str(self.active_turn.id) if self.active_turn else None,
                    "response_id": str(self.active_turn.response_id) if self.active_turn else None,
                    "timestamp_ms": int(time.time() * 1000),
                },
            )
            if stt_turn is not None:
                try:
                    stt_result = await stt_turn.finalize()
                except STTCancelledError:
                    if self._stt_finalize_cancel_requested or self._closing.is_set():
                        return
                    await self._fail_active_turn("stt_cancelled", status="cancelled")
                    return
                except STTError as error:
                    stt_error = error
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
            self.voice_session.total_frames += counters.frame_count
            self.voice_session.total_bytes += counters.byte_count
            self.voice_session.last_activity_at = self._now_datetime()
            self.active_turn = None
            self._response_turn_id = counters.turn_id
            self._turn_started = None
            await self.registry.clear_turn(self.owner, self.voice_session.id)
            await self.registry.refresh(self.owner, self.voice_session.id)
            await self.db.commit()
            if stt_result is not None:
                await self._send_transcript_event(stt_result.event)
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
            await self._close_stt_turn()
            if stt_error is not None:
                await self._complete_response_state(counters.response_id)
                await self._send(
                    server_event(
                        "server.turn.failed",
                        session_id=self.voice_session.id,
                        turn_id=counters.turn_id,
                        response_id=counters.response_id,
                        turn_number=counters.turn_number,
                        code=stt_error.code,
                    )
                )
                return

            llm_result: dict[str, Any] = {"status": "disabled"}
            if stt_result is not None:
                confirmation_result = await self._resolve_pending_confirmation(
                    session_id=self.voice_session.id,
                    turn_id=counters.turn_id,
                    response_id=counters.response_id,
                    transcript=stt_result.event.text,
                )
                if confirmation_result is not None:
                    llm_result = confirmation_result
                elif self.llm_service.enabled:
                    llm_result = await self._stream_llm_response(
                        session_id=self.voice_session.id,
                        turn_id=counters.turn_id,
                        response_id=counters.response_id,
                        transcript=stt_result.event.text,
                    )
                if llm_result["status"] == "cancelled":
                    return

            await self._complete_response_state(counters.response_id)
            await self._send(
                server_event(
                    "server.turn.completed",
                    session_id=self.voice_session.id,
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
        except asyncio.CancelledError:
            return
        except (VoiceRegistryError, VoiceSessionConflict):
            await self._protocol_failure("voice_registry_unavailable", close_code=1013)
        except SQLAlchemyError:
            await self.db.rollback()
            self.stats.error_count += 1
            await self._protocol_failure("voice_persistence_unavailable", close_code=1011)
        except Exception:  # noqa: BLE001 - isolate background turn completion failures
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
        started = time.monotonic()
        first_event_at: float | None = None
        first_text_at: float | None = None
        text_parts: list[str] = []
        usage: LLMUsage | None = None
        terminal_event: LLMEvent | None = None
        failure_code: str | None = None
        attempt_count = 0
        request_started_times: list[float] = []
        tool_call_at: float | None = None
        tool_execution_started_at: float | None = None
        tool_execution_finished_at: float | None = None

        def on_tool_execution_started(_call, timestamp: float) -> None:
            nonlocal tool_execution_started_at
            tool_execution_started_at = tool_execution_started_at or timestamp

        def on_tool_execution_finished(_call, timestamp: float) -> None:
            nonlocal tool_execution_finished_at
            tool_execution_finished_at = timestamp

        try:
            tool_registry = getattr(self, "tool_registry", None)
            request = build_voice_llm_request(
                self.settings,
                session_id=session_id,
                turn_id=turn_id,
                response_id=response_id,
                transcript=transcript,
                allowed_tools=(tool_registry.definitions() if tool_registry else ()),
            )
            if tool_registry is None:
                event_stream = self.llm_service.stream(request)
            else:
                context = ToolExecutionContext(
                    user_id=self.principal.user_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    scopes=frozenset({"tasks:write"}),
                    db=self.db,
                    clock=self._application_clock(),
                    user_timezone=self._user_timezone(),
                    source_transcript=transcript,
                    confirmation_requested=self._persist_confirmation_request,
                    tool_execution_started=on_tool_execution_started,
                    tool_execution_finished=on_tool_execution_finished,
                )
                event_stream = self.tool_loop.stream(request, context=context)
            async for event in event_stream:
                if not self.cancel_guard.can_emit(response_id):
                    return {"status": "cancelled"}
                attempt_count = max(attempt_count, event.attempt)
                if event.event_type == "request_started":
                    request_started_times.append(event.monotonic_seconds)
                    continue
                first_event_at = first_event_at or event.monotonic_seconds
                if event.event_type == "text_delta" and event.delta:
                    first_text_at = first_text_at or event.monotonic_seconds
                    text_parts.append(event.delta)
                    await self._send(
                        server_event(
                            "assistant.text.delta",
                            session_id=session_id,
                            turn_id=turn_id,
                            response_id=response_id,
                            sequence=event.sequence,
                            delta=event.delta,
                            provider=event.provider,
                            model=event.configured_model,
                        )
                    )
                    continue
                if event.event_type.startswith("tool_call_"):
                    # Tool-loop events stay server-side. Only confirmed final
                    # assistant text is forwarded to the mobile client.
                    if event.event_type == "tool_call_completed":
                        tool_call_at = tool_call_at or event.monotonic_seconds
                    continue
                if event.event_type == "confirmation_required":
                    return {"status": "confirmation_required"}
                if event.event_type == "usage":
                    usage = event.usage
                    continue
                if event.event_type == "response_failed":
                    terminal_event = event
                    failure_code = event.error_code or "llm_provider_error"
                    break
                if event.event_type == "response_completed":
                    terminal_event = event
                    if event.text is not None:
                        text_parts = [event.text]
                    break
        except LLMError as error:
            failure_code = error.code

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
                            terminal_event.returned_model
                            if terminal_event is not None
                            else None
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
            user_timezone=self._user_timezone(),
        )
        stored = await store.create_or_get(pending)
        if stored.tool_name != tool.name or stored.tool_call_id != call.tool_call_id:
            return False
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
        await self._send(
            server_event(
                "confirmation.required",
                session_id=stored.session_id,
                turn_id=stored.original_turn_id,
                response_id=stored.original_response_id,
                confirmation_id=stored.confirmation_id,
                tool_name=stored.tool_name,
                validated_arguments=stored.validated_tool_arguments,
                timezone=stored.user_timezone,
                due_at_utc=_confirmation_due_at_utc(stored.validated_tool_arguments),
                due_at_local=_confirmation_due_at_local(stored),
                expires_at=stored.expires_at.isoformat(),
                status=stored.status,
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
            },
        )
        return True

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

        if pending.status in {"REJECTED", "CANCELLED", "CONSUMED"}:
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

        if resolution == "REJECTED":
            await store.transition(scope, pending.confirmation_id, "REJECTED")
            text = "Okay, I won't create that reminder."
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
        context = ToolExecutionContext(
            user_id=self.principal.user_id,
            session_id=session_id,
            turn_id=claimed.original_turn_id,
            response_id=response_id,
            scopes=frozenset({"tasks:write"}),
            confirmed_tool_call_ids=frozenset({claimed.tool_call_id}),
            db=self.db,
            clock=self._application_clock(),
            user_timezone=claimed.user_timezone,
            cancellation_check=lambda: not self.cancel_guard.can_emit(response_id),
        )
        result = await self.tool_loop.executor.execute(call, context=context)
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
            title = str(pending.validated_tool_arguments.get("title", "that reminder"))
            return f"Done. I'll remind you to {title}."
        return f"Done. I completed {pending.tool_name.replace('_', ' ')}."

    @staticmethod
    def _confirmation_failure_text(pending: PendingConfirmation) -> str:
        if pending.tool_name == "create_task":
            return "I couldn't create that reminder."
        return f"I couldn't complete {pending.tool_name.replace('_', ' ')}."

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
                    "idempotency_key": [
                        str(value) for value in pending.idempotency_key
                    ],
                    "tool_execution_count": execution_count,
                    "replayed": replayed,
                    "final_response": final_response,
                    "error_code": error_code,
                }
            },
        )
        await self.db.commit()

    async def _complete_response_state(self, response_id: uuid.UUID) -> None:
        if self.voice_session is not None:
            await self.registry.clear_response(
                self.owner,
                self.voice_session.id,
                response_id,
            )
            await self.registry.refresh(self.owner, self.voice_session.id)
        self.cancel_guard.clear()
        self._response_turn_id = None

    @staticmethod
    def _duration_ms(started: float, ended: float | None) -> float | None:
        if ended is None:
            return None
        return round(max(0.0, (ended - started) * 1000), 1)

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
            self.voice_session.total_frames += counters.frame_count
            self.voice_session.total_bytes += counters.byte_count
        turn_id = self.active_turn.id
        response_id = self.active_turn.response_id
        self.active_turn = None
        self._turn_started = None
        self.cancel_guard.clear()
        await self._close_stt_turn(cancel=True)
        await self.registry.clear_turn(self.owner, self.voice_session.id)
        await self.registry.clear_response(
            self.owner,
            self.voice_session.id,
            response_id,
        )
        await self.db.commit()
        await self._send(
            server_event(
                "server.turn.failed",
                session_id=self.voice_session.id,
                turn_id=turn_id,
                response_id=response_id,
                code=code,
            )
        )

    async def _handle_response_cancel(self, message: ResponseCancelMessage) -> None:
        self._require_session()
        LOGGER.info(
            "Voice response cancellation received",
            extra={
                "event": "voice.response.cancel.received",
                "session_id": str(self.voice_session.id),
                "response_id": str(message.response_id),
                "reason": message.reason,
                "timestamp_ms": int(time.time() * 1000),
            },
        )
        confirmation_store = getattr(self, "confirmation_store", None)
        pending_confirmation = None
        if confirmation_store is not None and self.voice_session is not None:
            pending_confirmation = await confirmation_store.get(
                self._confirmation_scope(self.voice_session.id)
            )
        if pending_confirmation is not None and message.response_id in {
            pending_confirmation.original_response_id,
            self._last_response_id,
        }:
            await confirmation_store.transition(
                self._confirmation_scope(self.voice_session.id),
                pending_confirmation.confirmation_id,
                "CANCELLED",
            )
            # A completed action-request response has no active cancellation
            # guard. It still must be possible to cancel its pending mutation.
            if not self.cancel_guard.can_emit(message.response_id):
                await self._send(
                    server_event(
                        "confirmation.resolved",
                        session_id=self.voice_session.id,
                        response_id=message.response_id,
                        confirmation_id=pending_confirmation.confirmation_id,
                        status="CANCELLED",
                    )
                )
                await self._send(
                    server_event(
                        "response.cancelled",
                        session_id=self.voice_session.id,
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

        self.stats.cancellation_count += 1
        await self.registry.cancel_response(
            self.owner,
            self.voice_session.id,
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
            self.voice_session.total_frames += counters.frame_count
            self.voice_session.total_bytes += counters.byte_count
            self.active_turn = None
            self._turn_started = None
            await self.registry.clear_turn(self.owner, self.voice_session.id)
        elif cancelled_turn_id is not None:
            provider_info = self.llm_service.provider_info
            await self.persistence.merge_turn_metadata(
                self.db,
                self.principal,
                turn_id=cancelled_turn_id,
                metadata={
                    "llm": {
                        "status": "cancelled",
                        "provider": (
                            provider_info.provider if provider_info is not None else None
                        ),
                        "configured_model": (
                            provider_info.configured_model if provider_info is not None else None
                        ),
                        "prompt_version": VOICE_SYSTEM_PROMPT_VERSION,
                        "cancel_reason": message.reason,
                    }
                },
            )
        self._response_turn_id = None
        self.cancel_guard.clear()
        await self.db.commit()
        await self._send(
            server_event(
                "response.cancelled",
                session_id=self.voice_session.id,
                turn_id=cancelled_turn_id,
                response_id=message.response_id,
                reason=message.reason,
            )
        )

    async def _handle_ping(self, message: ClientPingMessage) -> None:
        self.stats.heartbeat_count += 1
        if self.voice_session is not None:
            self.voice_session.last_activity_at = self._now_datetime()
            await self.registry.refresh(self.owner, self.voice_session.id)
        await self._send(
            server_event(
                "server.pong",
                session_id=self.voice_session.id if self.voice_session else None,
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
                session_id=self.voice_session.id,
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
            await self._protocol_failure("authentication_expired_or_revoked", close_code=1008)
            return False
        except SQLAlchemyError:
            await self._protocol_failure("authentication_revalidation_unavailable", close_code=1013)
            return False
        return True

    async def _timeout(self, reason: str) -> None:
        self._session_status = "timed_out"
        self._close_code = 1000
        self._close_reason = reason
        await self._send_error(f"voice_{reason}")
        self._closing.set()
        await self.websocket.close(code=1000, reason=reason[:120])

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
        except RuntimeError:
            pass

    async def _send_error(self, code: str) -> None:
        await self._send(
            server_event(
                "server.error",
                session_id=self.voice_session.id if self.voice_session else None,
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
                "session_id": str(self.voice_session.id) if self.voice_session else None,
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
        async with self._send_lock:
            try:
                await self.websocket.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                self._closing.set()

    async def shutdown(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._closing.set()
        self._log_connection_closed()
        current = asyncio.current_task()
        for task in (self._processor_task, self._watchdog_task, self._receive_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        await self._cancel_stt_finalize_task()
        if self.stt_turn is not None:
            await self._close_stt_turn(cancel=True)
        await self._finalize_active_turn()
        if self.voice_session is not None:
            confirmation_store = getattr(self, "confirmation_store", None)
            if confirmation_store is not None:
                with contextlib.suppress(Exception):
                    await confirmation_store.cancel_scope(
                        self._confirmation_scope(self.voice_session.id)
                    )
            await self.persistence.finalize_session(
                self.db,
                self.principal,
                session_id=self.voice_session.id,
                status=self._session_status,
                close_code=self._close_code,
                close_reason=self._close_reason,
                total_frames=self.voice_session.total_frames,
                total_bytes=self.voice_session.total_bytes,
                error_count=self.stats.error_count,
            )
            record_audit(
                self.db,
                "VOICE_SESSION_ENDED",
                user_id=self.principal.user_id,
                device_id=self.principal.device_id,
                metadata={
                    "session_id": str(self.voice_session.id),
                    "status": self._session_status,
                    "frames": self.stats.frames_accepted,
                    "queue_high_water_mark": self.stats.queue_high_water_mark,
                    "queue_overflow_count": self.stats.queue_overflow_count,
                },
                request=self.websocket,
            )
            await self.db.commit()
            try:
                await self.registry.release(self.owner, self.voice_session.id)
            except VoiceRegistryError:
                pass
            else:
                LOGGER.info(
                    "Voice session registry released",
                    extra={
                        "event": "voice.session.registry.released",
                        "session_id": str(self.voice_session.id),
                        "user_id": str(self.principal.user_id),
                        "device_id": str(self.principal.device_id),
                        "timestamp_ms": int(time.time() * 1000),
                        "monotonic_ms": round(time.monotonic() * 1000, 1),
                    },
                )
            await self._send(
                server_event(
                    "server.session.ended",
                    session_id=self.voice_session.id,
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
        self.voice_session.total_frames += counters.frame_count
        self.voice_session.total_bytes += counters.byte_count
        self.active_turn = None
        self._turn_started = None

    def _require_session(self) -> None:
        if self.voice_session is None or self.state.session_id is None:
            raise StateTransitionError("voice session is not ready")

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
        return getattr(self, "clock", SystemClock())

    def _user_timezone(self) -> str:
        metadata = self.voice_session.client_metadata if self.voice_session is not None else None
        if isinstance(metadata, dict):
            timezone_name = metadata.get("timezone")
            if isinstance(timezone_name, str) and timezone_name.strip():
                return timezone_name.strip()
        return self.settings.voice_default_timezone.strip()


def _safe_client_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {"client_version", "app_build", "platform", "locale", "timezone"}
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
