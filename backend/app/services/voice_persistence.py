from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConversationTurn, VoiceSession
from app.services.auth import AuthPrincipal


def utc_now() -> datetime:
    return datetime.now(UTC)


class VoicePersistence:
    """Ownership-aware durable metadata operations for the voice gateway."""

    async def create_session(
        self,
        db: AsyncSession,
        principal: AuthPrincipal,
        *,
        protocol_version: int,
        client_metadata: dict[str, Any],
    ) -> VoiceSession:
        session = VoiceSession(
            user_id=principal.user_id,
            device_id=principal.device_id,
            auth_session_id=principal.session_id,
            protocol_version=protocol_version,
            client_metadata=client_metadata,
            status="active",
        )
        db.add(session)
        await db.flush()
        return session

    async def resume_session(
        self,
        db: AsyncSession,
        principal: AuthPrincipal,
        session_id: uuid.UUID,
        *,
        reconnect_grace_seconds: int,
    ) -> VoiceSession | None:
        result = await db.execute(
            select(VoiceSession)
            .where(
                VoiceSession.id == session_id,
                VoiceSession.user_id == principal.user_id,
                VoiceSession.device_id == principal.device_id,
                VoiceSession.auth_session_id == principal.session_id,
            )
            .with_for_update()
        )
        session = result.scalar_one_or_none()
        if session is None or session.status not in {"disconnected", "active"}:
            return None
        if (
            session.status == "disconnected"
            and session.ended_at is not None
            and (utc_now() - session.ended_at).total_seconds() > reconnect_grace_seconds
        ):
            return None
        session.status = "active"
        session.ended_at = None
        session.close_code = None
        session.close_reason = None
        session.last_activity_at = utc_now()
        return session

    async def reap_stale_active_sessions(
        self,
        db: AsyncSession,
        principal: AuthPrincipal,
        *,
        stale_after_seconds: int,
    ) -> list[uuid.UUID]:
        """Close stale sessions for this authenticated user/device only.

        A process restart can bypass the WebSocket ``finally`` block and leave
        an active durable row behind. Reaping is deliberately scoped to the
        connecting principal's device and requires activity older than the
        heartbeat lease before it changes any state.
        """

        cutoff = utc_now() - timedelta(seconds=stale_after_seconds)
        result = await db.scalars(
            select(VoiceSession)
            .where(
                VoiceSession.user_id == principal.user_id,
                VoiceSession.device_id == principal.device_id,
                VoiceSession.status == "active",
                VoiceSession.last_activity_at <= cutoff,
            )
            .with_for_update()
        )
        sessions = list(result.all())
        if not sessions:
            return []

        ended_at = utc_now()
        for session in sessions:
            session.status = "disconnected"
            session.ended_at = ended_at
            session.last_activity_at = ended_at
            session.close_code = 1001
            session.close_reason = "stale_connection_reaped"
        return [session.id for session in sessions]

    async def create_turn(
        self,
        db: AsyncSession,
        principal: AuthPrincipal,
        *,
        voice_session: VoiceSession,
        response_id: uuid.UUID,
        client_turn_id: uuid.UUID | None,
    ) -> ConversationTurn:
        if (
            voice_session.user_id != principal.user_id
            or voice_session.device_id != principal.device_id
            or voice_session.auth_session_id != principal.session_id
        ):
            raise PermissionError("voice session does not belong to the authenticated principal")

        last_turn = await db.scalar(
            select(ConversationTurn.turn_number)
            .where(
                ConversationTurn.session_id == voice_session.id,
                ConversationTurn.user_id == principal.user_id,
            )
            .order_by(ConversationTurn.turn_number.desc())
            .limit(1)
        )
        turn = ConversationTurn(
            session_id=voice_session.id,
            user_id=principal.user_id,
            turn_number=(last_turn or 0) + 1,
            response_id=response_id,
            metadata_json={"client_turn_id": str(client_turn_id)} if client_turn_id else {},
            status="active",
        )
        db.add(turn)
        voice_session.total_turns += 1
        await db.flush()
        return turn

    async def finalize_turn(
        self,
        db: AsyncSession,
        principal: AuthPrincipal,
        *,
        turn_id: uuid.UUID,
        status: str,
        frame_count: int,
        byte_count: int,
        last_sequence: int | None,
        declared_duration_ms: int | None,
        observed_duration_ms: int | None,
        error_count: int = 0,
        gap_count: int = 0,
        duplicate_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationTurn | None:
        turn = await db.scalar(
            select(ConversationTurn)
            .join(
                VoiceSession,
                (VoiceSession.id == ConversationTurn.session_id)
                & (VoiceSession.user_id == ConversationTurn.user_id),
            )
            .where(
                ConversationTurn.id == turn_id,
                ConversationTurn.user_id == principal.user_id,
                VoiceSession.device_id == principal.device_id,
                VoiceSession.auth_session_id == principal.session_id,
            )
        )
        if turn is None or turn.ended_at is not None:
            return turn

        now = utc_now()
        turn.status = status
        turn.committed_at = now if status == "committed" else turn.committed_at
        turn.ended_at = now
        turn.frame_count = frame_count
        turn.byte_count = byte_count
        turn.last_sequence = last_sequence
        turn.first_sequence = 0 if frame_count else None
        turn.declared_duration_ms = declared_duration_ms
        turn.observed_duration_ms = observed_duration_ms
        turn.error_count = error_count
        turn.gap_count = gap_count
        turn.duplicate_count = duplicate_count
        turn.metadata_json = {**(turn.metadata_json or {}), **(metadata or {})}
        return turn

    async def finalize_session(
        self,
        db: AsyncSession,
        principal: AuthPrincipal,
        *,
        session_id: uuid.UUID,
        status: str,
        close_code: int | None,
        close_reason: str | None,
        total_frames: int,
        total_bytes: int,
        error_count: int,
    ) -> VoiceSession | None:
        voice_session = await db.scalar(
            select(VoiceSession).where(
                VoiceSession.id == session_id,
                VoiceSession.user_id == principal.user_id,
                VoiceSession.device_id == principal.device_id,
                VoiceSession.auth_session_id == principal.session_id,
            )
        )
        if voice_session is None or voice_session.ended_at is not None:
            return voice_session

        voice_session.status = status
        voice_session.ended_at = utc_now()
        voice_session.last_activity_at = voice_session.ended_at
        voice_session.close_code = close_code
        voice_session.close_reason = close_reason
        voice_session.total_frames = total_frames
        voice_session.total_bytes = total_bytes
        voice_session.error_count = error_count
        return voice_session

    async def merge_turn_metadata(
        self,
        db: AsyncSession,
        principal: AuthPrincipal,
        *,
        turn_id: uuid.UUID,
        metadata: dict[str, Any],
    ) -> ConversationTurn | None:
        """Merge post-STT response metadata into an owned durable turn."""

        turn = await db.scalar(
            select(ConversationTurn)
            .join(
                VoiceSession,
                (VoiceSession.id == ConversationTurn.session_id)
                & (VoiceSession.user_id == ConversationTurn.user_id),
            )
            .where(
                ConversationTurn.id == turn_id,
                ConversationTurn.user_id == principal.user_id,
                VoiceSession.device_id == principal.device_id,
                VoiceSession.auth_session_id == principal.session_id,
            )
        )
        if turn is None:
            return None
        turn.metadata_json = {**(turn.metadata_json or {}), **metadata}
        return turn
