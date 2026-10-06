from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.api.dependencies import DatabaseSessionDependency, get_current_principal
from app.models import MemoryItem, VoiceSession
from app.schemas import MemoryExclusionRequest, SessionUpdateRequest, VoiceSessionResponse
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
        voice_session = await session.scalar(
            select(VoiceSession)
            .where(
                VoiceSession.id == session_id,
                VoiceSession.user_id == principal.user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if payload.client_metadata.get("memory_excluded"):
            from app.planning.repository import erase_proposal_content
            from app.planning.service import revoke_session_state

            await revoke_session_state(session, principal.user_id, session_id)
            await erase_proposal_content(session, principal.user_id, session_id)
        voice_session.client_metadata = payload.client_metadata
    await session.commit()
    await session.refresh(voice_session)
    return voice_session


@router.put("/{session_id}/memory-exclusion", response_model=VoiceSessionResponse)
async def set_memory_exclusion(
    session_id: uuid.UUID,
    payload: MemoryExclusionRequest,
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
    voice_session = await session.scalar(
        select(VoiceSession)
        .where(
            VoiceSession.id == session_id,
            VoiceSession.user_id == principal.user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if payload.excluded:
        from app.planning.repository import erase_proposal_content
        from app.planning.service import revoke_session_state

        await revoke_session_state(session, principal.user_id, session_id)
        await erase_proposal_content(session, principal.user_id, session_id)
    metadata = dict(voice_session.client_metadata or {})
    was_excluded = metadata.get("memory_excluded") is True
    if payload.excluded and not was_excluded:
        from app.okf.lifecycle import OkfLifecycleService

        await OkfLifecycleService(
            policy_version=request.app.state.settings.okf_policy_version,
            sync_enabled=(
                request.app.state.settings.okf_enabled
                and request.app.state.settings.okf_sync_enabled
            ),
        ).exclude_session(session, user_id=principal.user_id, session_id=session_id)
    metadata["memory_excluded"] = payload.excluded
    voice_session.client_metadata = metadata
    if was_excluded and not payload.excluded:
        from app.memory.repository import MemoryRepository

        await MemoryRepository().bump_memory_version(session, user_id=principal.user_id)
        settings = request.app.state.settings
        if settings.okf_enabled and settings.okf_sync_enabled:
            from app.okf.jobs import enqueue_memory_sync

            memory_ids = tuple(
                (
                    await session.scalars(
                        select(MemoryItem.id).where(
                            MemoryItem.user_id == principal.user_id,
                            MemoryItem.source_session_id == session_id,
                            MemoryItem.status == "active",
                        )
                    )
                ).all()
            )
            for memory_id in memory_ids:
                await enqueue_memory_sync(
                    session,
                    user_id=principal.user_id,
                    memory_id=memory_id,
                    policy_version=settings.okf_policy_version,
                )
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
    from app.okf.lifecycle import OkfLifecycleService

    await OkfLifecycleService(
        policy_version=request.app.state.settings.okf_policy_version,
        sync_enabled=(
            request.app.state.settings.okf_enabled and request.app.state.settings.okf_sync_enabled
        ),
    ).exclude_session(session, user_id=principal.user_id, session_id=session_id)
    await session.delete(voice_session)
    await session.commit()
