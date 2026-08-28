from __future__ import annotations

from fastapi import APIRouter, WebSocket
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import extract_bearer_token
from app.services.audit import record_audit
from app.services.auth import AuthConfigurationError, AuthenticationError, AuthService
from app.websocket.gateway import VoiceGateway

router = APIRouter(tags=["voice"])


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
        try:
            async with session_factory() as db:
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
            pass
    try:
        await websocket.close(code=1008, reason="Authentication failed")
    except RuntimeError:
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
        async with session_factory() as auth_db:
            principal = await AuthService(websocket.app.state.settings).resolve_access_token(
                auth_db,
                access_token,
            )
    except (AuthenticationError, AuthConfigurationError):
        await _reject_handshake(websocket, reason="invalid_or_revoked_token")
        return
    except SQLAlchemyError:
        await _reject_handshake(websocket, reason="authentication_service_unavailable")
        return

    await websocket.accept()
    async with session_factory() as db:
        gateway = VoiceGateway(
            websocket,
            db=db,
            session_factory=session_factory,
            settings=websocket.app.state.settings,
            principal=principal,
            access_token=access_token,
        )
        await gateway.run()
