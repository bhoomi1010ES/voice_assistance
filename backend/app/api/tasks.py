from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.api.dependencies import DatabaseSessionDependency, get_current_principal
from app.models import Task
from app.schemas import TaskCreateRequest, TaskResponse, TaskUpdateRequest
from app.services.auth import AuthPrincipal
from app.services.ownership import get_owned_task, record_ownership_denial

router = APIRouter(prefix="/tasks", tags=["tasks"])


def not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found."},
    )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateRequest,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> Task:
    task = Task(
        user_id=principal.user_id,
        title=payload.title,
        description=payload.description,
        due_at=payload.due_at,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> list[Task]:
    return list(
        (
            await session.scalars(
                select(Task)
                .where(Task.user_id == principal.user_id)
                .order_by(Task.created_at.desc())
            )
        ).all()
    )


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
    if payload.title is not None:
        task.title = payload.title
    if payload.description is not None:
        task.description = payload.description
    if payload.status is not None:
        task.status = payload.status
    if payload.due_at is not None:
        task.due_at = payload.due_at
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
