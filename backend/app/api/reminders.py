from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.dependencies import DatabaseSessionDependency, get_current_principal
from app.core.clock import SystemClock
from app.models import Reminder, User
from app.schemas import ReminderCreateRequest, ReminderResponse, ReminderUpdateRequest
from app.services.auth import AuthPrincipal
from app.services.ownership import (
    get_owned_reminder,
    get_owned_task,
    record_ownership_denial,
)
from app.services.recurrence import RecurrenceResolutionError, validate_recurrence_rule
from app.services.task_due_dates import TaskDueDateResolutionError, normalize_absolute_due_at

router = APIRouter(prefix="/reminders", tags=["reminders"])


def not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found."},
    )


def temporal_error(error: TaskDueDateResolutionError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "TEMPORAL_RESOLUTION_REQUIRED", "message": str(error)},
    )


async def _user_timezone(session, principal: AuthPrincipal) -> str:
    timezone = await session.scalar(select(User.timezone).where(User.id == principal.user_id))
    return timezone or "UTC"


def _public_response(reminder: Reminder) -> ReminderResponse:
    public_status = (
        "scheduled" if reminder.status in {"processing", "retry_wait"} else reminder.status
    )
    return ReminderResponse(
        id=reminder.id,
        task_id=reminder.task_id,
        title=reminder.title,
        body=reminder.body,
        trigger_at=reminder.trigger_at,
        timezone=reminder.timezone,
        recurrence_rule=reminder.recurrence_rule,
        status=public_status,
        delivery_channel=reminder.delivery_channel,
        created_at=reminder.created_at,
        updated_at=reminder.updated_at,
        sent_at=reminder.sent_at,
        failure_code="delivery_failed" if reminder.status == "failed" else None,
    )


async def _normalize_trigger(value: datetime, *, timezone_name: str) -> datetime:
    return normalize_absolute_due_at(
        value,
        now_utc=SystemClock().now_utc(),
        timezone_name=timezone_name,
        label="reminder trigger time",
    )


async def _validate_task_scope(
    session, principal: AuthPrincipal, task_id: uuid.UUID | None
) -> None:
    if task_id is None:
        return
    if await get_owned_task(session, user_id=principal.user_id, task_id=task_id) is None:
        raise not_found()


@router.get("", response_model=list[ReminderResponse])
async def list_reminders(
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    upcoming: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = None,
) -> list[ReminderResponse]:
    query = (
        select(Reminder)
        .where(Reminder.user_id == principal.user_id)
        .order_by(Reminder.trigger_at.asc(), Reminder.id.asc())
        .limit(limit)
    )
    if status_filter == "scheduled":
        query = query.where(Reminder.status.in_(("scheduled", "processing", "retry_wait")))
    elif status_filter is not None:
        query = query.where(Reminder.status == status_filter)
    if upcoming:
        query = query.where(Reminder.trigger_at > SystemClock().now_utc())
    if before is not None:
        query = query.where(Reminder.created_at < before)
    return [_public_response(item) for item in (await session.scalars(query)).all()]


@router.post("", response_model=ReminderResponse, status_code=status.HTTP_201_CREATED)
async def create_reminder(
    payload: ReminderCreateRequest,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> ReminderResponse:
    try:
        recurrence_rule = validate_recurrence_rule(payload.recurrence_rule)
    except RecurrenceResolutionError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_RECURRENCE", "message": str(error)},
        ) from error
    timezone_name = payload.timezone or await _user_timezone(session, principal)
    try:
        trigger_at = await _normalize_trigger(payload.trigger_at, timezone_name=timezone_name)
    except TaskDueDateResolutionError as error:
        raise temporal_error(error) from error
    await _validate_task_scope(session, principal, payload.task_id)
    reminder = Reminder(
        user_id=principal.user_id,
        task_id=payload.task_id,
        title=payload.title,
        body=payload.body,
        trigger_at=trigger_at,
        timezone=timezone_name,
        recurrence_rule=recurrence_rule,
        status="scheduled",
        delivery_channel=payload.delivery_channel,
        next_attempt_at=trigger_at,
    )
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return _public_response(reminder)


@router.get("/{reminder_id}", response_model=ReminderResponse)
async def get_reminder(
    reminder_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> ReminderResponse:
    reminder = await get_owned_reminder(session, user_id=principal.user_id, reminder_id=reminder_id)
    if reminder is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="reminder",
            resource_id=reminder_id,
        )
        raise not_found()
    return _public_response(reminder)


@router.patch("/{reminder_id}", response_model=ReminderResponse)
async def update_reminder(
    reminder_id: uuid.UUID,
    payload: ReminderUpdateRequest,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> ReminderResponse:
    reminder = await get_owned_reminder(session, user_id=principal.user_id, reminder_id=reminder_id)
    if reminder is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="reminder",
            resource_id=reminder_id,
        )
        raise not_found()
    try:
        recurrence_rule = (
            validate_recurrence_rule(payload.recurrence_rule)
            if "recurrence_rule" in payload.model_fields_set
            else reminder.recurrence_rule
        )
    except RecurrenceResolutionError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_RECURRENCE", "message": str(error)},
        ) from error
    timezone_name = payload.timezone or reminder.timezone
    try:
        trigger_at = (
            await _normalize_trigger(payload.trigger_at, timezone_name=timezone_name)
            if payload.trigger_at is not None
            else reminder.trigger_at
        )
    except TaskDueDateResolutionError as error:
        raise temporal_error(error) from error
    await _validate_task_scope(session, principal, payload.task_id)
    if payload.title is not None:
        reminder.title = payload.title
    if "body" in payload.model_fields_set:
        reminder.body = payload.body
    if "task_id" in payload.model_fields_set:
        reminder.task_id = payload.task_id
    reminder.timezone = timezone_name
    recurrence_changed = "recurrence_rule" in payload.model_fields_set
    should_reschedule = payload.trigger_at is not None or (
        recurrence_changed and recurrence_rule is not None
    )
    if should_reschedule:
        if (
            payload.trigger_at is None
            and recurrence_rule is not None
            and reminder.trigger_at <= SystemClock().now_utc()
        ):
            raise temporal_error(
                TaskDueDateResolutionError(
                    "a future trigger time is required when enabling recurrence"
                )
            )
        reminder.recurrence_rule = recurrence_rule
        reminder.trigger_at = trigger_at
        reminder.delivery_id = str(uuid.uuid4())
        reminder.status = "scheduled"
        reminder.sent_at = None
        reminder.attempt_count = 0
        reminder.occurrence_count = 0
        reminder.next_attempt_at = trigger_at
        reminder.failure_code = None
        reminder.failure_reason = None
        reminder.dead_lettered_at = None
    if recurrence_changed:
        reminder.recurrence_rule = recurrence_rule
    if payload.status == "cancelled":
        reminder.status = "cancelled"
        reminder.next_attempt_at = None
        reminder.locked_at = None
        reminder.locked_by = None
        reminder.lease_expires_at = None
    elif payload.status == "scheduled":
        reminder.status = "scheduled"
        reminder.next_attempt_at = reminder.trigger_at
    await session.commit()
    await session.refresh(reminder)
    return _public_response(reminder)


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reminder(
    reminder_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> None:
    reminder = await get_owned_reminder(session, user_id=principal.user_id, reminder_id=reminder_id)
    if reminder is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="reminder",
            resource_id=reminder_id,
        )
        raise not_found()
    reminder.status = "cancelled"
    reminder.next_attempt_at = None
    reminder.locked_at = None
    reminder.locked_by = None
    reminder.lease_expires_at = None
    await session.commit()
