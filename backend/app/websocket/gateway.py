from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import ConversationTurn, VoiceSession
from app.services.audit import record_audit
from app.services.auth import (
    AuthConfigurationError,
    AuthenticationError,
    AuthPrincipal,
    AuthService,
)
from app.services.voice_persistence import VoicePersistence
from app.services.voice_registry import (
    VoiceRegistry,
    VoiceRegistryError,
    VoiceRegistryOwner,
    VoiceSessionConflict,
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
    ) -> None:
        self.websocket = websocket
        self.db = db
        self.session_factory = session_factory
        self.settings = settings
        self.principal = principal
        self.access_token = access_token
        self.auth_service = AuthService(settings)
        self.persistence = VoicePersistence()
        self.registry = VoiceRegistry(
            websocket.app.state.infrastructure.redis,
            ttl_seconds=settings.voice_max_session_seconds + settings.voice_reconnect_grace_seconds,
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
        self._last_response_id: uuid.UUID | None = None
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

    async def run(self) -> None:
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
                return

            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                self._close_code = message.get("code")
                self._close_reason = "client_disconnect"
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
        self._session_started = time.monotonic()
        self.state.session_ready(voice_session.id, completed_turns=voice_session.total_turns)
        self._session_status = "completed"
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
            )
        )

    async def _handle_turn_start(self, message: TurnStartMessage) -> None:
        self._require_session()
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
        self.state.accept_frame(frame)
        self.stats.frames_accepted += 1
        self.stats.bytes_received += frame.payload_length
        self.voice_session.last_activity_at = self._now_datetime()

    async def _handle_audio_commit(self, message: AudioCommitMessage) -> None:
        self._require_session()
        if self.active_turn is None:
            raise StateTransitionError("no active turn")
        counters = self.state.commit(
            last_sequence_no=message.last_sequence_no,
            frame_count=message.frame_count,
            byte_count=message.byte_count,
        )
        observed_duration_ms = self._elapsed_turn_ms()
        await self.persistence.finalize_turn(
            self.db,
            self.principal,
            turn_id=counters.turn_id,
            status="committed",
            frame_count=counters.frame_count,
            byte_count=counters.byte_count,
            last_sequence=counters.last_sequence_no,
            declared_duration_ms=message.duration_ms,
            observed_duration_ms=observed_duration_ms,
        )
        self.voice_session.total_frames += counters.frame_count
        self.voice_session.total_bytes += counters.byte_count
        self.voice_session.last_activity_at = self._now_datetime()
        self.active_turn = None
        self._turn_started = None
        self.cancel_guard.clear()
        await self.registry.clear_turn(self.owner, self.voice_session.id)
        await self.registry.refresh(self.owner, self.voice_session.id)
        await self.db.commit()
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
            )
        )

    async def _handle_response_cancel(self, message: ResponseCancelMessage) -> None:
        self._require_session()
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
        cancelled_turn_id = self.active_turn.id if self.active_turn is not None else None
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
        current = asyncio.current_task()
        for task in (self._processor_task, self._watchdog_task, self._receive_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        await self._finalize_active_turn()
        if self.voice_session is not None:
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

    @staticmethod
    def _now_datetime():
        from datetime import UTC, datetime

        return datetime.now(UTC)


def _safe_client_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {"client_version", "app_build", "platform", "locale"}
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in allowed:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = str(value)[:128] if isinstance(value, str) else value
    return result
