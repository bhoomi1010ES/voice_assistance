from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.memory.policy import ExtractionCandidate
from app.memory.types import MemorySourceKind, MemoryType
from app.memory.writer import MemoryWriter
from app.models import PlanContextItem, User, VoiceSession
from app.services.auth import AuthPrincipal

LOGGER = logging.getLogger("voice-assistance-backend")


class MemoryPromotionError(ValueError):
    """Refusal to promote plan context to memory due to privacy or policy boundaries."""


async def can_promote_context_to_memory(
    db: AsyncSession,
    settings: Settings,
    principal: AuthPrincipal,
    *,
    source_session_id: uuid.UUID | None = None,
) -> tuple[bool, str]:
    """Check memory consent, user setting, and session privacy boundaries."""
    if not settings.memory_write_enabled:
        return False, "memory_writes_disabled"

    user = await db.scalar(select(User).where(User.id == principal.user_id))
    if user is None or not user.memory_enabled:
        return False, "user_memory_disabled"

    if source_session_id is not None:
        voice = await db.scalar(
            select(VoiceSession).where(
                VoiceSession.id == source_session_id,
                VoiceSession.user_id == principal.user_id,
            )
        )
        if voice is not None and (voice.client_metadata or {}).get("memory_excluded"):
            return False, "memory_excluded_session"

    return True, "allowed"


async def promote_plan_context_to_memory(
    db: AsyncSession,
    settings: Settings,
    principal: AuthPrincipal,
    *,
    plan_id: uuid.UUID,
    context_item_id: uuid.UUID,
    source_session_id: uuid.UUID | None = None,
    source_turn_id: uuid.UUID | None = None,
) -> tuple[Any, bool]:
    """Promote an owned PlanContextItem to long-term memory under strict privacy controls.

    Enforces:
    - Plan and Context item ownership by principal.
    - Global memory write toggle (settings.memory_write_enabled).
    - User account consent (User.memory_enabled).
    - Private / excluded session suppression (VoiceSession.memory_excluded).
    """
    item = await db.scalar(
        select(PlanContextItem).where(
            PlanContextItem.id == context_item_id,
            PlanContextItem.plan_id == plan_id,
            PlanContextItem.user_id == principal.user_id,
            PlanContextItem.status == "active",
        )
    )
    if item is None:
        raise MemoryPromotionError("plan_context_not_found")

    allowed, reason = await can_promote_context_to_memory(
        db, settings, principal, source_session_id=source_session_id
    )
    if not allowed:
        LOGGER.info(
            "Plan context promotion denied: %s (item=%s, user=%s)",
            reason,
            context_item_id,
            principal.user_id,
        )
        raise MemoryPromotionError(reason)

    candidate = ExtractionCandidate(
        content=item.content,
        memory_type=MemoryType.FACT,
        confidence=1.0,
        salience=0.8,
        object_json=item.value_json,
    )

    writer = MemoryWriter(settings)
    memory_item, created = await writer.write_candidate(
        session=db,
        user_id=principal.user_id,
        candidate=candidate,
        source_kind=MemorySourceKind.EXPLICIT_TOOL,
        source_turn_id=source_turn_id or item.source_turn_id,
        source_session_id=source_session_id,
    )
    return memory_item, created
