from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthSession, Device


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
