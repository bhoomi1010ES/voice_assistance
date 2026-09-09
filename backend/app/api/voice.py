from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import extract_bearer_token
from app.core.async_utils import await_cleanup
from app.services.audit import record_audit
from app.services.auth import AuthConfigurationError, AuthenticationError, AuthService
from app.websocket.gateway import VoiceGateway

router = APIRouter(tags=["voice"])


async def _close_session(session: AsyncSession) -> None:
    try:
        await session.rollback()
    finally:
        await session.close()


async def _reject_handshake(
    websocket: WebSocket,
    *,
    reason: str,
    event_type: str = "WEBSOCKET_AUTH_FAILURE",
    user_id=None,
    device_id=None,
) -> None:
    session_factory = websocket.app.state.infrastructure.database.session_factory
    if session_factory is not None:
        db = session_factory()
        try:
            record_audit(
                db,
                event_type,
                user_id=user_id,
                device_id=device_id,
                metadata={"reason": reason, "path": websocket.url.path},
                request=websocket,
            )
            await db.commit()
        except Exception:  # noqa: BLE001 - rejection must not leak internals
            await db.rollback()
        finally:
            with contextlib.suppress(asyncio.CancelledError):
                await await_cleanup(_close_session(db))
    try:
        await websocket.close(code=1008, reason="Authentication failed")
    except (RuntimeError, WebSocketDisconnect):
        pass


@router.websocket("/v1/voice")
async def voice_gateway(websocket: WebSocket) -> None:
    session_factory = websocket.app.state.infrastructure.database.session_factory
    if session_factory is None:
        await _reject_handshake(websocket, reason="database_unavailable")
        return

    access_token = extract_bearer_token(websocket.headers.get("authorization"))
    if access_token is None:
        await _reject_handshake(websocket, reason="missing_or_malformed_authorization")
        return

    try:
        auth_db = session_factory()
        try:
            principal = await AuthService(websocket.app.state.settings).resolve_access_token(
                auth_db,
                access_token,
            )
        finally:
            with contextlib.suppress(asyncio.CancelledError):
                await await_cleanup(_close_session(auth_db))
    except (AuthenticationError, AuthConfigurationError):
        await _reject_handshake(websocket, reason="invalid_or_revoked_token")
        return
    except SQLAlchemyError:
        await _reject_handshake(websocket, reason="authentication_service_unavailable")
        return

    await websocket.accept()
    db = session_factory()
    try:
        gateway = VoiceGateway(
            websocket,
            db=db,
            session_factory=session_factory,
            settings=websocket.app.state.settings,
            principal=principal,
            access_token=access_token,
            stt_service=websocket.app.state.stt_service,
            llm_service=websocket.app.state.llm_service,
        )
        await gateway.run()
    finally:
        with contextlib.suppress(asyncio.CancelledError):
            await await_cleanup(_close_session(db))
