from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm.exc import StaleDataError

from app.api.dependencies import DatabaseSessionDependency, get_current_principal
from app.models import Plan, PlanContextItem, PlanningAction, Task
from app.planning.service import PlanningError, get_owned_plan
from app.schemas.planning import (
    PlanContextResponse,
    PlanCreateRequest,
    PlanningReceiptResponse,
    PlanResponse,
    PlanUpdateRequest,
)
from app.services.auth import AuthPrincipal

router = APIRouter(prefix="/plans", tags=["plans"])
Principal = Annotated[AuthPrincipal, Depends(get_current_principal)]


def conflict() -> HTTPException:
    return HTTPException(
        409, detail={"code": "REVISION_CONFLICT", "message": "Reload the resource before editing."}
    )


async def owned(db, user_id, plan_id) -> Plan:
    try:
        return await get_owned_plan(db, user_id, plan_id)
    except PlanningError as error:
        raise HTTPException(404, detail={"code": "RESOURCE_NOT_FOUND"}) from error


async def validate_group(db, user_id, plan_id) -> None:
    if plan_id is not None:
        plan = await owned(db, user_id, plan_id)
        if plan.status != "active":
            raise HTTPException(422, detail={"code": "PLAN_NOT_ACTIVE"})


async def commit_revision(db) -> None:
    try:
        await db.commit()
    except StaleDataError as error:
        await db.rollback()
        raise conflict() from error


@router.post("", response_model=PlanResponse, status_code=201)
async def create_plan(
    payload: PlanCreateRequest, session: DatabaseSessionDependency, principal: Principal
):
    plan = Plan(user_id=principal.user_id, **payload.model_dump())
    session.add(plan)
    await session.commit()
    await session.refresh(plan)
    return plan


@router.get("", response_model=list[PlanResponse])
async def list_plans(
    session: DatabaseSessionDependency,
    principal: Principal,
    status: Literal["active", "completed", "archived"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    query = (
        select(Plan)
        .where(Plan.user_id == principal.user_id)
        .order_by(Plan.created_at.desc(), Plan.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        query = query.where(Plan.status == status)
    return list((await session.scalars(query)).all())


@router.get("/{plan_id}")
async def get_plan(
    plan_id: uuid.UUID,
    session: DatabaseSessionDependency,
    principal: Principal,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    plan = await owned(session, principal.user_id, plan_id)
    context = await session.scalars(
        select(PlanContextItem)
        .where(
            PlanContextItem.user_id == principal.user_id,
            PlanContextItem.plan_id == plan_id,
            PlanContextItem.status == "active",
        )
        .order_by(PlanContextItem.created_at, PlanContextItem.id)
        .limit(limit)
        .offset(offset)
    )
    counts = await session.execute(
        select(Task.status, func.count(Task.id))
        .where(
            Task.user_id == principal.user_id,
            Task.plan_id == plan_id,
        )
        .group_by(Task.status)
    )
    return {
        "plan": PlanResponse.model_validate(plan),
        "context": [PlanContextResponse.model_validate(item) for item in context.all()],
        "task_counts": dict(counts.all()),
    }


@router.patch("/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: uuid.UUID,
    payload: PlanUpdateRequest,
    session: DatabaseSessionDependency,
    principal: Principal,
):
    plan = await owned(session, principal.user_id, plan_id)
    if plan.revision != payload.expected_revision:
        raise conflict()
    for key, value in payload.model_dump(exclude_unset=True, exclude={"expected_revision"}).items():
        setattr(plan, key, value)
    await commit_revision(session)
    await session.refresh(plan)
    return plan


@router.get("/{plan_id}/actions", response_model=list[PlanningReceiptResponse])
async def list_actions(
    plan_id: uuid.UUID,
    session: DatabaseSessionDependency,
    principal: Principal,
    limit: int = Query(50, ge=1, le=100),
    before: datetime | None = None,
    offset: int = Query(0, ge=0),
):
    await owned(session, principal.user_id, plan_id)
    query = (
        select(PlanningAction)
        .where(PlanningAction.user_id == principal.user_id, PlanningAction.plan_id == plan_id)
        .order_by(PlanningAction.created_at.desc(), PlanningAction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if before is not None:
        query = query.where(PlanningAction.created_at < before)
    return list((await session.scalars(query)).all())
