from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit import record_audit
from app.services.auth import AuthConfigurationError, AuthenticationError, AuthService

router = APIRouter(tags=["websocket"])


async def _close_with_audit(
    websocket: WebSocket,
    session: AsyncSession | None,
    event_type: str,
    *,
    reason: str,
    user_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
) -> None:
    if session is not None:
        try:
            record_audit(
                session,
                event_type,
                user_id=user_id,
                device_id=device_id,
                metadata={"reason": reason, "path": websocket.url.path},
                request=websocket,
            )
            await session.commit()
        except Exception:  # noqa: BLE001 - connection rejection must remain deterministic
            await session.rollback()
    await websocket.close(code=1008, reason="Authentication failed")


def _bearer_token(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization")
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


@router.websocket("/ws")
async def authenticated_websocket(websocket: WebSocket) -> None:
    session_factory = websocket.app.state.infrastructure.database.session_factory
    if session_factory is None:
        await _close_with_audit(
            websocket,
            None,
            "WEBSOCKET_AUTH_FAILURE",
            reason="database_unavailable",
        )
        return

    async with session_factory() as session:
        token = _bearer_token(websocket)
        try:
            auth_service = AuthService(websocket.app.state.settings)
            if token is None:
                raise AuthenticationError("Missing authorization")
            principal = await auth_service.resolve_access_token(session, token)
        except (AuthenticationError, AuthConfigurationError):
            await _close_with_audit(
                websocket,
                session,
                "WEBSOCKET_AUTH_FAILURE",
                reason="invalid_or_missing_token",
            )
            return
        except Exception:  # noqa: BLE001 - do not leak database/runtime errors through handshake
            await _close_with_audit(
                websocket,
                session,
                "WEBSOCKET_AUTH_FAILURE",
                reason="authentication_service_unavailable",
            )
            return

        await websocket.accept()
        await websocket.send_json({"type": "authenticated", "user_id": str(principal.user_id)})
        try:
            while True:
                payload = await websocket.receive_json()
                try:
                    principal = await auth_service.resolve_access_token(session, token)
                except (AuthenticationError, AuthConfigurationError):
                    await _close_with_audit(
                        websocket,
                        session,
                        "WEBSOCKET_AUTH_FAILURE",
                        reason="session_revoked_or_expired",
                    )
                    return

                if isinstance(payload, dict) and "user_id" in payload:
                    try:
                        requested_user_id = uuid.UUID(str(payload["user_id"]))
                    except (ValueError, TypeError, AttributeError):
                        requested_user_id = None
                    if requested_user_id != principal.user_id:
                        await _close_with_audit(
                            websocket,
                            session,
                            "UNAUTHORIZED_ACCESS",
                            reason="websocket_user_override_attempt",
                            user_id=principal.user_id,
                            device_id=principal.device_id,
                        )
                        return

                message = payload.get("message") if isinstance(payload, dict) else None
                await websocket.send_json(
                    {"type": "ack", "user_id": str(principal.user_id), "message": message}
                )
        except WebSocketDisconnect:
            return
