from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.api.dependencies import DatabaseSessionDependency, get_current_principal
from app.models import VoiceSession
from app.schemas import SessionUpdateRequest, VoiceSessionResponse
from app.services.auth import AuthPrincipal
from app.services.ownership import get_owned_voice_session, record_ownership_denial

router = APIRouter(prefix="/sessions", tags=["sessions"])


def not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found."},
    )


@router.get("", response_model=list[VoiceSessionResponse])
async def list_sessions(
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> list[VoiceSession]:
    return list(
        (
            await session.scalars(
                select(VoiceSession)
                .where(VoiceSession.user_id == principal.user_id)
                .order_by(VoiceSession.started_at.desc())
            )
        ).all()
    )


@router.get("/{session_id}", response_model=VoiceSessionResponse)
async def get_session(
    session_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> VoiceSession:
    voice_session = await get_owned_voice_session(
        session,
        user_id=principal.user_id,
        session_id=session_id,
    )
    if voice_session is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="voice_session",
            resource_id=session_id,
        )
        raise not_found()
    return voice_session


@router.patch("/{session_id}", response_model=VoiceSessionResponse)
async def update_session(
    session_id: uuid.UUID,
    payload: SessionUpdateRequest,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> VoiceSession:
    voice_session = await get_owned_voice_session(
        session,
        user_id=principal.user_id,
        session_id=session_id,
    )
    if voice_session is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="voice_session",
            resource_id=session_id,
        )
        raise not_found()
    if payload.client_metadata is not None:
        voice_session.client_metadata = payload.client_metadata
    await session.commit()
    await session.refresh(voice_session)
    return voice_session


@router.post("/{session_id}/cancel", response_model=VoiceSessionResponse)
async def cancel_session(
    session_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> VoiceSession:
    voice_session = await get_owned_voice_session(
        session,
        user_id=principal.user_id,
        session_id=session_id,
    )
    if voice_session is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="voice_session",
            resource_id=session_id,
        )
        raise not_found()
    if voice_session.ended_at is None:
        now = datetime.now(UTC)
        voice_session.status = "failed"
        voice_session.ended_at = now
        voice_session.last_activity_at = now
        voice_session.close_reason = "cancelled_by_user"
    await session.commit()
    await session.refresh(voice_session)
    return voice_session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> None:
    voice_session = await get_owned_voice_session(
        session,
        user_id=principal.user_id,
        session_id=session_id,
    )
    if voice_session is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="voice_session",
            resource_id=session_id,
        )
        raise not_found()
    if voice_session.ended_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SESSION_ACTIVE", "message": "Active sessions cannot be deleted."},
        )
    await session.delete(voice_session)
    await session.commit()
