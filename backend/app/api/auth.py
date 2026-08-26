from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.dependencies import (
    AuthServiceDependency,
    DatabaseSessionDependency,
    get_current_principal,
    get_current_user,
)
from app.models import AuthSession, User
from app.schemas import (
    AuthSessionResponse,
    LoginRequest,
    LogoutResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth import (
    AuthConfigurationError,
    AuthenticationError,
    AuthPrincipal,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "AUTHENTICATION_FAILED", "message": "Authentication failed."},
        headers={"WWW-Authenticate": "Bearer"},
    )


def issued_response(issued) -> TokenResponse:
    return TokenResponse(
        token_type="bearer",
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        user=issued.user,
        device=issued.device,
        session=issued.session,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> User:
    try:
        return await auth_service.register_user(
            session,
            email=payload.email,
            password=payload.password,
            request=request,
        )
    except ValueError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ACCOUNT_ALREADY_EXISTS", "message": "Account already exists."},
        ) from error
    except SQLAlchemyError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_SERVICE_UNAVAILABLE",
                "message": "Authentication service unavailable.",
            },
        ) from error


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> TokenResponse:
    try:
        issued = await auth_service.login(
            session,
            email=payload.email,
            password=payload.password,
            device_identifier=payload.device_identifier,
            platform=payload.platform,
            device_name=payload.device_name,
            device_metadata=payload.device_metadata,
            request=request,
        )
    except AuthenticationError as error:
        await session.rollback()
        raise invalid_credentials() from error
    except AuthConfigurationError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_CONFIGURATION_UNAVAILABLE",
                "message": "Authentication is not configured.",
            },
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_SERVICE_UNAVAILABLE",
                "message": "Authentication service unavailable.",
            },
        ) from error
    return issued_response(issued)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> TokenResponse:
    try:
        issued = await auth_service.refresh(
            session,
            refresh_token=payload.refresh_token,
            request=request,
        )
    except AuthenticationError as error:
        await session.rollback()
        raise invalid_credentials() from error
    except AuthConfigurationError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_CONFIGURATION_UNAVAILABLE",
                "message": "Authentication is not configured.",
            },
        ) from error
    return issued_response(issued)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> LogoutResponse:
    await auth_service.logout(session, principal, request=request)
    return LogoutResponse(status="logged_out")


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user


@router.get("/sessions", response_model=list[AuthSessionResponse])
async def list_sessions(
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> list[AuthSession]:
    return await auth_service.list_sessions(session, principal)


@router.post("/sessions/{session_id}/revoke", response_model=LogoutResponse)
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> LogoutResponse:
    revoked = await auth_service.revoke_session(
        session,
        principal,
        session_id,
        request=request,
    )
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found."},
        )
    return LogoutResponse(status="session_revoked")
