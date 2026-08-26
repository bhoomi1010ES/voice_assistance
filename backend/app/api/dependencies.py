from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db
from app.models import User
from app.services.audit import record_audit
from app.services.auth import (
    AuthConfigurationError,
    AuthenticationError,
    AuthPrincipal,
    AuthService,
)

bearer_scheme = HTTPBearer(auto_error=False)
CredentialsDependency = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]
DatabaseSessionDependency = Annotated[AsyncSession, Depends(get_db)]


def authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "AUTHENTICATION_FAILED", "message": "Authentication failed."},
        headers={"WWW-Authenticate": "Bearer"},
    )


def authorization_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "FORBIDDEN", "message": "The requested resource is not available."},
    )


def get_auth_service(request: Request) -> AuthService:
    return AuthService(request.app.state.settings)


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


async def _record_unauthorized(
    session: AsyncSession,
    request: Request,
    *,
    reason: str,
) -> None:
    try:
        record_audit(
            session,
            "UNAUTHORIZED_ACCESS",
            metadata={"reason": reason, "path": request.url.path},
            request=request,
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - authentication failure must remain a rejection
        await session.rollback()


async def get_current_principal(
    request: Request,
    credentials: CredentialsDependency,
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> AuthPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        await _record_unauthorized(session, request, reason="missing_or_malformed_authorization")
        raise authentication_error()
    try:
        return await auth_service.resolve_access_token(session, credentials.credentials)
    except AuthConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_CONFIGURATION_UNAVAILABLE",
                "message": "Authentication is not configured.",
            },
        ) from error
    except AuthenticationError:
        await _record_unauthorized(session, request, reason="invalid_or_revoked_token")
        raise authentication_error() from None
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_SERVICE_UNAVAILABLE",
                "message": "Authentication service unavailable.",
            },
        ) from error


async def get_current_user(
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    session: DatabaseSessionDependency,
) -> User:
    user = await session.get(User, principal.user_id)
    if user is None or user.status != "active":
        raise authentication_error()
    return user
