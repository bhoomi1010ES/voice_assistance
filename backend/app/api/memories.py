from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, select, update

from app.api.dependencies import DatabaseSessionDependency, get_current_principal
from app.memory.policy import ExtractionCandidate, validate_candidate
from app.memory.types import MemorySourceKind, MemoryType
from app.memory.writer import MemoryWriteConflict, MemoryWriter
from app.models import MemoryItem, MemoryJob, User
from app.schemas import (
    MemoryCreateRequest,
    MemoryDeleteAllRequest,
    MemoryResponse,
    MemorySearchRequest,
    MemorySettingsResponse,
    MemorySettingsUpdateRequest,
    MemoryUpdateRequest,
)
from app.services.auth import AuthPrincipal
from app.services.ownership import get_owned_memory, record_ownership_denial

router = APIRouter(prefix="/memories", tags=["memories"])


def not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found."},
    )


def memory_disabled() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "MEMORY_DISABLED", "message": "Memory is disabled for this account."},
    )


async def _owned_user(session, principal: AuthPrincipal) -> User:
    user = await session.get(User, principal.user_id)
    if user is None or user.status != "active":
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTHENTICATION_FAILED", "message": "Authentication failed."},
        )
    return user


@router.get("/settings", response_model=MemorySettingsResponse)
async def get_memory_settings(
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> User:
    return await _owned_user(session, principal)


@router.patch("/settings", response_model=MemorySettingsResponse)
async def update_memory_settings(
    payload: MemorySettingsUpdateRequest,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> User:
    user = await _owned_user(session, principal)
    if payload.enabled is not None:
        user.memory_enabled = payload.enabled
        if not payload.enabled:
            await session.execute(
                update(MemoryJob)
                .where(
                    MemoryJob.user_id == user.id,
                    MemoryJob.status.in_(("pending", "retry_wait")),
                )
                .values(status="cancelled", locked_at=None)
            )
    if payload.timezone is not None:
        user.timezone = payload.timezone
    if payload.locale is not None:
        user.locale = payload.locale
    user.memory_version += 1
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_memories(
    payload: MemoryDeleteAllRequest,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> None:
    user = await _owned_user(session, principal)
    await session.execute(delete(MemoryItem).where(MemoryItem.user_id == user.id))
    user.memory_version += 1
    await session.commit()


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreateRequest,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> MemoryItem:
    user = await _owned_user(session, principal)
    if not user.memory_enabled:
        raise memory_disabled()
    candidate = ExtractionCandidate(
        content=payload.content,
        memory_type=MemoryType(payload.memory_type),
        subject=payload.subject,
        predicate=payload.predicate,
        object_json=payload.object_json,
        confidence=payload.confidence,
        salience=payload.salience,
    )
    try:
        candidate = validate_candidate(candidate, candidate.content)
        memory, _ = await MemoryWriter(request.app.state.settings).write_candidate(
            session,
            user_id=principal.user_id,
            candidate=candidate,
            source_kind=MemorySourceKind.MANUAL_API,
            metadata_json=payload.metadata,
        )
    except (MemoryWriteConflict, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": str(error), "message": "Memory content could not be saved."},
        ) from error
    await session.commit()
    await session.refresh(memory)
    return memory


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    limit: int = Query(default=50, ge=1, le=100),
    before: datetime | None = None,
) -> list[MemoryItem]:
    user = await _owned_user(session, principal)
    query = (
        select(MemoryItem)
        .where(MemoryItem.user_id == user.id, MemoryItem.status == "active")
        .order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
        .limit(limit)
    )
    if before is not None:
        query = query.where(MemoryItem.created_at < before)
    return list((await session.scalars(query)).all())


async def _search_memories(
    query_text: str,
    limit: int,
    request: Request,
    session,
    principal: AuthPrincipal,
) -> list[MemoryItem]:
    user = await _owned_user(session, principal)
    if not user.memory_enabled:
        raise memory_disabled()
    settings = request.app.state.settings
    if settings.memory_retrieval_mode == "off":
        query = (
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user.id,
                MemoryItem.status == "active",
                MemoryItem.content.ilike(f"%{query_text}%"),
            )
            .order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
            .limit(limit)
        )
        return list((await session.scalars(query)).all())
    result = await request.app.state.memory_service.retrieve(
        session,
        user_id=user.id,
        query=query_text,
    )
    if not result.memories:
        return []
    rows = list(
        (
            await session.scalars(
                select(MemoryItem).where(
                    MemoryItem.user_id == user.id,
                    MemoryItem.id.in_([memory.memory_id for memory in result.memories]),
                )
            )
        ).all()
    )
    by_id = {row.id: row for row in rows}
    return [by_id[memory.memory_id] for memory in result.memories if memory.memory_id in by_id]


@router.get("/search", response_model=list[MemoryResponse])
async def search_memories(
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    query: str = Query(min_length=1, max_length=2_000),
    limit: int = Query(default=8, ge=1, le=32),
) -> list[MemoryItem]:
    return await _search_memories(query, limit, request, session, principal)


@router.post("/search", response_model=list[MemoryResponse])
async def search_memories_post(
    payload: MemorySearchRequest,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> list[MemoryItem]:
    return await _search_memories(payload.query, payload.limit, request, session, principal)


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> MemoryItem:
    memory = await get_owned_memory(session, user_id=principal.user_id, memory_id=memory_id)
    if memory is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="memory",
            resource_id=memory_id,
        )
        raise not_found()
    return memory


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: uuid.UUID,
    payload: MemoryUpdateRequest,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> MemoryItem:
    old = await get_owned_memory(session, user_id=principal.user_id, memory_id=memory_id)
    if old is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="memory",
            resource_id=memory_id,
        )
        raise not_found()
    candidate = ExtractionCandidate(
        content=payload.content or old.content,
        memory_type=MemoryType(payload.memory_type or old.memory_type),
        subject=payload.subject if payload.subject is not None else old.subject,
        predicate=payload.predicate if payload.predicate is not None else old.predicate,
        object_json=payload.object_json if payload.object_json is not None else old.object_json,
        confidence=payload.confidence if payload.confidence is not None else old.confidence,
        salience=payload.salience if payload.salience is not None else old.salience,
    )
    old.status = "superseded"
    try:
        candidate = validate_candidate(candidate, candidate.content)
        memory, _ = await MemoryWriter(request.app.state.settings).write_candidate(
            session,
            user_id=principal.user_id,
            candidate=candidate,
            source_kind=MemorySourceKind.MANUAL_API,
            metadata_json=payload.metadata if payload.metadata is not None else old.metadata_json,
        )
    except (MemoryWriteConflict, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": str(error), "message": "Memory content could not be saved."},
        ) from error
    memory.supersedes_id = old.id
    await session.commit()
    await session.refresh(memory)
    return memory


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    request: Request,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> None:
    memory = await get_owned_memory(session, user_id=principal.user_id, memory_id=memory_id)
    if memory is None:
        await record_ownership_denial(
            session,
            request,
            user_id=principal.user_id,
            device_id=principal.device_id,
            resource="memory",
            resource_id=memory_id,
        )
        raise not_found()
    await session.delete(memory)
    await session.commit()
