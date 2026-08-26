from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import (
    AuthServiceDependency,
    DatabaseSessionDependency,
    get_current_principal,
)
from app.schemas import DeviceRegisterRequest, DeviceResponse, LogoutResponse
from app.services.auth import AuthenticationError, AuthPrincipal

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/register", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def register_device(
    payload: DeviceRegisterRequest,
    request: Request,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> DeviceResponse:
    try:
        device, created = await auth_service.register_device(
            session,
            principal,
            device_identifier=payload.device_identifier,
            platform=payload.platform,
            name=payload.name,
            device_metadata=payload.metadata,
            request=request,
        )
    except AuthenticationError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DEVICE_NOT_AVAILABLE", "message": "Device is not available."},
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DEVICE_NOT_AVAILABLE", "message": "Device is not available."},
        ) from error
    response = DeviceResponse.model_validate(device)
    if not created:
        return response
    return response


@router.get("", response_model=list[DeviceResponse])
async def list_devices(
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> list:
    return await auth_service.list_devices(session, principal)


@router.post("/{device_id}/revoke", response_model=LogoutResponse)
async def revoke_device(
    device_id: uuid.UUID,
    request: Request,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    session: DatabaseSessionDependency,
    auth_service: AuthServiceDependency,
) -> LogoutResponse:
    revoked = await auth_service.revoke_device(
        session,
        principal,
        device_id,
        request=request,
    )
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found."},
        )
    return LogoutResponse(status="device_revoked")
