from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.dependencies import DatabaseSessionDependency, get_current_principal
from app.core.clock import SystemClock
from app.models import Task, User
from app.schemas import TaskCreateRequest, TaskResponse, TaskUpdateRequest
from app.services.auth import AuthPrincipal
from app.services.ownership import get_owned_task, record_ownership_denial
from app.services.task_due_dates import TaskDueDateResolutionError, normalize_absolute_due_at

router = APIRouter(prefix="/tasks", tags=["tasks"])


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


async def _normalize_due_at(value: datetime | None, *, timezone_name: str) -> datetime | None:
    if value is None:
        return None
    return normalize_absolute_due_at(
        value,
        now_utc=SystemClock().now_utc(),
        timezone_name=timezone_name,
        label="task due date",
    )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateRequest,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> Task:
    timezone_name = payload.timezone or await _user_timezone(session, principal)
    try:
        due_at = await _normalize_due_at(payload.due_at, timezone_name=timezone_name)
    except TaskDueDateResolutionError as error:
        raise temporal_error(error) from error
    task = Task(
        user_id=principal.user_id,
        title=payload.title,
        description=payload.description,
        status="pending",
        priority=payload.priority,
        due_at=due_at,
        local_due_at=due_at.astimezone(ZoneInfo(timezone_name)) if due_at is not None else None,
        timezone=timezone_name,
        timezone_source="explicit" if payload.timezone else "backend",
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    priority: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = None,
) -> list[Task]:
    query = (
        select(Task)
        .where(Task.user_id == principal.user_id)
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(limit)
    )
    if status_filter is not None:
        query = query.where(Task.status == status_filter)
    if priority is not None:
        query = query.where(Task.priority == priority)
    if before is not None:
        query = query.where(Task.created_at < before)
    return list((await session.scalars(query)).all())


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> Task:
    task = await get_owned_task(session, user_id=principal.user_id, task_id=task_id)
    if task is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="task",
            resource_id=task_id,
        )
        raise not_found()
    return task


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdateRequest,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> Task:
    task = await get_owned_task(session, user_id=principal.user_id, task_id=task_id)
    if task is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="task",
            resource_id=task_id,
        )
        raise not_found()
    timezone_name = payload.timezone or task.timezone or await _user_timezone(session, principal)
    try:
        due_at = (
            await _normalize_due_at(payload.due_at, timezone_name=timezone_name)
            if "due_at" in payload.model_fields_set
            else task.due_at
        )
    except TaskDueDateResolutionError as error:
        raise temporal_error(error) from error
    if payload.title is not None:
        task.title = payload.title
    if "description" in payload.model_fields_set:
        task.description = payload.description
    if payload.status is not None:
        task.status = payload.status
    if payload.priority is not None:
        task.priority = payload.priority
    task.timezone = timezone_name
    task.due_at = due_at
    task.local_due_at = (
        due_at.astimezone(ZoneInfo(timezone_name)) if due_at is not None else None
    )
    if payload.timezone is not None:
        task.timezone_source = "explicit"
    _set_completed_at(task, payload.status)
    await session.commit()
    await session.refresh(task)
    return task


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> Task:
    task = await get_owned_task(session, user_id=principal.user_id, task_id=task_id)
    if task is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="task",
            resource_id=task_id,
        )
        raise not_found()
    task.status = "completed"
    task.completed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> None:
    task = await get_owned_task(session, user_id=principal.user_id, task_id=task_id)
    if task is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="task",
            resource_id=task_id,
        )
        raise not_found()
    await session.delete(task)
    await session.commit()


def _set_completed_at(task: Task, new_status: str | None) -> None:
    if new_status == "completed":
        task.completed_at = task.completed_at or datetime.now(UTC)
    elif new_status is not None:
        task.completed_at = None
