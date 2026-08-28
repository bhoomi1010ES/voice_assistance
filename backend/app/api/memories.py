from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.dependencies import DatabaseSessionDependency, get_current_principal
from app.models import MemoryItem
from app.schemas import MemoryCreateRequest, MemoryResponse, MemoryUpdateRequest
from app.services.auth import AuthPrincipal
from app.services.ownership import (
    get_owned_memory,
    record_ownership_denial,
)

router = APIRouter(prefix="/memories", tags=["memories"])


def not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found."},
    )


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreateRequest,
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> MemoryItem:
    memory = MemoryItem(
        user_id=principal.user_id,
        content=payload.content,
        metadata_json=payload.metadata,
    )
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return memory


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
) -> list[MemoryItem]:
    return list(
        (
            await session.scalars(
                select(MemoryItem)
                .where(MemoryItem.user_id == principal.user_id, MemoryItem.status == "active")
                .order_by(MemoryItem.created_at.desc())
            )
        ).all()
    )


@router.get("/search", response_model=list[MemoryResponse])
async def search_memories(
    session: DatabaseSessionDependency,
    principal: Annotated[AuthPrincipal, Depends(get_current_principal)],
    query: str = Query(min_length=1, max_length=500),
) -> list[MemoryItem]:
    return list(
        (
            await session.scalars(
                select(MemoryItem)
                .where(
                    MemoryItem.user_id == principal.user_id,
                    MemoryItem.status == "active",
                    MemoryItem.content.ilike(f"%{query}%"),
                )
                .order_by(MemoryItem.created_at.desc())
            )
        ).all()
    )


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
    if payload.content is not None:
        memory.content = payload.content
    if payload.metadata is not None:
        memory.metadata_json = payload.metadata
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
