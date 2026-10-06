from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuthSession,
    Device,
    Plan,
    PlanningAction,
    PlanningBatch,
    PlanningSession,
    User,
    VoiceSession,
)
from app.services.auth import AuthPrincipal


class PlanningError(ValueError):
    pass


def recognize_mode_control(transcript: str) -> str | None:
    """Whole direct utterances only: quotes, questions and mixed content abstain."""
    text = re.sub(r"[.!]+$", "", transcript.strip().lower()).strip()
    text = re.sub(r"\s+", " ", text)
    if text in {
        "plan mode",
        "switch to plan mode",
        "switch to planning mode",
        "enable plan mode",
        "enable planning mode",
        "turn on plan mode",
        "turn on planning mode",
        "let's plan this",
        "lets plan this",
    }:
        return "plan"
    if text in {
        "disable plan mode",
        "disable planning mode",
        "turn off plan mode",
        "turn off planning mode",
        "stop planning",
        "normal mode",
        "switch to normal mode",
    }:
        return "normal"
    return None


def recognize_plan_selection(transcript: str) -> str | None:
    text = transcript.strip().rstrip(".")
    if text.lower() in {"clear active plan", "deselect plan"}:
        return ""
    match = re.fullmatch(r"(?:select|use|switch to) plan (.{1,255})", text, flags=re.IGNORECASE)
    return match[1].strip() if match else None


async def resolve_plan_selection(
    db: AsyncSession, user_id: uuid.UUID, name: str
) -> uuid.UUID | None:
    if not name:
        return None
    try:
        plan_id = uuid.UUID(name)
    except ValueError:
        found = list(
            (
                await db.scalars(
                    select(Plan.id)
                    .where(
                        Plan.user_id == user_id,
                        Plan.status == "active",
                        func.lower(Plan.name) == name.lower(),
                    )
                    .limit(2)
                )
            ).all()
        )
        if not found:
            raise PlanningError("plan_not_found") from None
        if len(found) > 1:
            raise PlanningError("plan_ambiguous") from None
        return found[0]
    plan = await get_owned_plan(db, user_id, plan_id)
    if plan.status != "active":
        raise PlanningError("plan_not_active")
    return plan.id


async def get_owned_plan(db: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID) -> Plan:
    plan = await db.scalar(select(Plan).where(Plan.id == plan_id, Plan.user_id == user_id))
    if plan is None:
        raise PlanningError("plan_not_found")
    return plan


async def locked_state(
    db: AsyncSession, principal: AuthPrincipal, session_id: uuid.UUID, *, create: bool = True
) -> PlanningSession | None:
    # All controls, lifecycle revocations and future execution transactions lock
    # voice then planning rows in this order. Hold through commit, never extraction.
    voice = await db.scalar(
        select(VoiceSession)
        .where(
            VoiceSession.id == session_id,
            VoiceSession.user_id == principal.user_id,
            VoiceSession.device_id == principal.device_id,
            VoiceSession.auth_session_id == principal.session_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if voice is None or voice.status != "active":
        raise PlanningError("session_not_available")
    auth = await db.scalar(
        select(AuthSession)
        .join(User, User.id == AuthSession.user_id)
        .join(
            Device,
            (Device.id == AuthSession.device_id) & (Device.user_id == AuthSession.user_id),
        )
        .where(
            User.status == "active",
            Device.revoked_at.is_(None),
            AuthSession.device_id == principal.device_id,
            AuthSession.id == principal.session_id,
            AuthSession.user_id == principal.user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    if auth is None:
        raise PlanningError("session_not_available")
    state = await db.scalar(
        select(PlanningSession)
        .where(
            PlanningSession.session_id == session_id,
            PlanningSession.user_id == principal.user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is None and create:
        state = PlanningSession(
            session_id=session_id,
            user_id=principal.user_id,
            mode="normal",
            state_version=1,
            policy_version="plan-v1",
        )
        db.add(state)
        await db.flush()
    return state


async def snapshot(db: AsyncSession, state: PlanningSession, *, available: bool) -> dict:
    plan = None
    if state.active_plan_id is not None:
        plan = await get_owned_plan(db, state.user_id, state.active_plan_id)
    return {
        "mode": state.mode,
        "active_plan_id": state.active_plan_id,
        "active_plan_name": plan.name if plan else None,
        "plan_revision": plan.revision if plan else None,
        "state_version": state.state_version,
        "policy_version": state.policy_version,
        "enabled_at": state.enabled_at,
        "available": available,
        "automatic_actions_available": False,
    }


async def change_state(
    db: AsyncSession,
    principal: AuthPrincipal,
    session_id: uuid.UUID,
    *,
    expected_version: int,
    enabled: bool,
    mode: str | None = None,
    plan_id: uuid.UUID | None = None,
    select_plan: bool = False,
    policy_version: str = "plan-v1",
) -> dict:
    state = await locked_state(db, principal, session_id)
    if state.state_version != expected_version:
        raise PlanningError("planning_state_conflict")
    if (mode == "plan" or select_plan) and not enabled:
        raise PlanningError("planning_unavailable")
    voice = await db.scalar(select(VoiceSession).where(VoiceSession.id == session_id))
    if (mode == "plan" or select_plan) and (voice.client_metadata or {}).get("memory_excluded"):
        raise PlanningError("planning_private_session")
    if select_plan and state.mode != "plan":
        raise PlanningError("planning_mode_required")
    if select_plan and plan_id is not None:
        plan = await get_owned_plan(db, principal.user_id, plan_id)
        if plan.status != "active":
            raise PlanningError("plan_not_active")
    if select_plan:
        state.active_plan_id = plan_id
    if mode is not None:
        state.mode = mode
        if mode == "plan":
            state.policy_version = policy_version
            state.enabled_at = datetime.now(UTC)
        else:
            state.disabled_at = datetime.now(UTC)
            state.active_plan_id = None
            await cancel_pending(db, principal.user_id, session_id)
    state.state_version += 1
    await db.flush()
    return await snapshot(db, state, available=enabled)


async def cancel_pending(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
    from sqlalchemy import update

    batches = select(PlanningBatch.id).where(
        PlanningBatch.user_id == user_id, PlanningBatch.session_id == session_id
    )
    await db.execute(
        update(PlanningAction)
        .where(
            PlanningAction.user_id == user_id,
            PlanningAction.batch_id.in_(batches),
            PlanningAction.status == "pending",
        )
        .values(status="cancelled", reason="consent_revoked")
    )
    await db.execute(
        update(PlanningBatch)
        .where(
            PlanningBatch.user_id == user_id,
            PlanningBatch.session_id == session_id,
            PlanningBatch.status.in_(("pending", "validated")),
        )
        .values(status="cancelled")
    )


async def revoke_session_state(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
    # Caller already owns the voice-row lock, including terminal session states.
    state = await db.scalar(
        select(PlanningSession)
        .where(
            PlanningSession.session_id == session_id,
            PlanningSession.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is not None:
        state.mode = "normal"
        state.active_plan_id = None
        state.disabled_at = datetime.now(UTC)
        state.state_version += 1
        await cancel_pending(db, user_id, session_id)


async def revoke_auth_state(
    db: AsyncSession, user_id: uuid.UUID, auth_session_id: uuid.UUID
) -> None:
    voices = await db.scalars(
        select(VoiceSession)
        .where(
            VoiceSession.user_id == user_id,
            VoiceSession.auth_session_id == auth_session_id,
        )
        .order_by(VoiceSession.id)
        .with_for_update()
    )
    for voice in voices.all():
        await revoke_session_state(db, user_id, voice.id)


@asynccontextmanager
async def execution_barrier(
    db: AsyncSession, principal: AuthPrincipal, session_id: uuid.UUID, expected_version: int
):
    """Future executor seam; caller commits mutation/receipt inside this guard.

    This is a freshness barrier, never a tool authorization grant. PM-1 has no
    caller executing inferred actions. Disabled acknowledgement follows commit,
    so an older automatic commit cannot land after that acknowledgement.
    """
    state = await locked_state(db, principal, session_id, create=False)
    if state is None or state.mode != "plan" or state.state_version != expected_version:
        raise PlanningError("planning_consent_revoked")
    voice = await db.scalar(select(VoiceSession).where(VoiceSession.id == session_id))
    if (voice.client_metadata or {}).get("memory_excluded"):
        raise PlanningError("planning_private_session")
    yield state
