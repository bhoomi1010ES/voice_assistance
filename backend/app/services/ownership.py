from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthSession, Device, MemoryItem, Task, VoiceSession
from app.services.audit import record_audit


async def get_owned_device(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
) -> Device | None:
    """Return a device only when it belongs to the authenticated user."""

    return await session.scalar(
        select(Device).where(Device.id == device_id, Device.user_id == user_id)
    )


async def get_owned_auth_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> AuthSession | None:
    """Return an auth session only when it belongs to the authenticated user."""

    return await session.scalar(
        select(AuthSession).where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
        )
    )


async def get_owned_memory(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    memory_id: uuid.UUID,
) -> MemoryItem | None:
    """Return a memory only when it belongs to the authenticated user."""

    return await session.scalar(
        select(MemoryItem).where(
            MemoryItem.id == memory_id,
            MemoryItem.user_id == user_id,
            MemoryItem.status == "active",
        )
    )


async def get_owned_task(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
) -> Task | None:
    """Return a task only when it belongs to the authenticated user."""

    return await session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))


async def get_owned_voice_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> VoiceSession | None:
    """Return a voice session only when it belongs to the authenticated user."""

    return await session.scalar(
        select(VoiceSession).where(
            VoiceSession.id == session_id,
            VoiceSession.user_id == user_id,
        )
    )


async def record_ownership_denial(
    session: AsyncSession,
    request: Request,
    *,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    resource: str,
    resource_id: uuid.UUID,
) -> None:
    """Record a resource miss without revealing whether another owner exists."""

    try:
        record_audit(
            session,
            "UNAUTHORIZED_ACCESS",
            user_id=user_id,
            device_id=device_id,
            metadata={
                "reason": "resource_not_owned",
                "resource": resource,
                "resource_id": str(resource_id),
            },
            request=request,
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - an authorization miss must remain a safe denial
        await session.rollback()
