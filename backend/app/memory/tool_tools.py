from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select

from app.llm.errors import LLMToolError
from app.llm.tool_loop import ToolExecutionContext, ToolRegistry
from app.llm.types import LLMToolCall
from app.models import MemoryItem, User

from .extraction import extract_explicit_tool_candidate
from .policy import ExtractionCandidate
from .types import MemorySourceKind, MemoryType
from .writer import MemoryWriter


class MemorySearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: StrictStr = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=8, ge=1, le=8)


class MemorySaveArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: StrictStr = Field(min_length=1, max_length=2_000)
    memory_type: MemoryType = MemoryType.FACT
    subject: StrictStr | None = Field(default=None, max_length=512)
    predicate: StrictStr | None = Field(default=None, max_length=128)


class MemoryForgetArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: uuid.UUID


def build_explicit_memory_save_call(text: str, *, turn_id: uuid.UUID) -> LLMToolCall | None:
    """Translate an explicit, grounded remember command into a server-owned call."""

    candidate = extract_explicit_tool_candidate(text)
    if candidate is None:
        return None
    arguments = MemorySaveArguments(
        content=candidate.content,
        memory_type=candidate.memory_type,
        subject=candidate.subject,
        predicate=candidate.predicate,
    ).model_dump(mode="json", exclude_none=True)
    return LLMToolCall(
        tool_call_id=f"server-memory-save-{turn_id}",
        name="memory_save",
        arguments_json=json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
        arguments=arguments,
    )


async def memory_search_handler(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    if not isinstance(arguments, MemorySearchArguments) or context.db is None:
        raise LLMToolError("Memory search is unavailable.")
    if context.memory_service is None:
        raise LLMToolError("Memory search is unavailable.")
    user = await context.db.get(User, context.user_id)
    if user is None or not user.memory_enabled:
        raise LLMToolError("Memory search is disabled.")
    result = await context.memory_service.retrieve(
        context.db,
        user_id=context.user_id,
        query=arguments.query,
    )
    return {
        "memories": [
            {"id": str(memory.memory_id), "type": memory.memory_type, "content": memory.content}
            for memory in result.memories[: arguments.limit]
        ],
        "status": result.status,
    }


async def memory_save_handler(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    if not isinstance(arguments, MemorySaveArguments) or context.db is None:
        raise LLMToolError("Memory save is unavailable.")
    if context.memory_settings is None or not context.memory_settings.memory_write_enabled:
        raise LLMToolError("Memory save is not enabled.")
    user = await context.db.get(User, context.user_id)
    if user is None or not user.memory_enabled:
        raise LLMToolError("Memory save is disabled.")
    candidate = ExtractionCandidate(
        content=arguments.content,
        memory_type=arguments.memory_type,
        subject=arguments.subject,
        predicate=arguments.predicate,
        confidence=1.0,
        salience=1.0,
    )
    item, created = await MemoryWriter(context.memory_settings).write_candidate(
        context.db,
        user_id=context.user_id,
        candidate=candidate,
        source_kind=MemorySourceKind.EXPLICIT_TOOL,
    )
    return {"memory_id": str(item.id), "created": created}


async def memory_forget_handler(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    if not isinstance(arguments, MemoryForgetArguments) or context.db is None:
        raise LLMToolError("Memory deletion is unavailable.")
    item = await context.db.scalar(
        select(MemoryItem).where(
            MemoryItem.id == arguments.memory_id,
            MemoryItem.user_id == context.user_id,
            MemoryItem.status == "active",
        )
    )
    if item is None:
        return {"deleted": False}
    await context.db.delete(item)
    return {"deleted": True, "memory_id": str(item.id)}


def register_memory_tools(registry: ToolRegistry, *, allow_write: bool) -> None:
    registry.register(
        name="memory_search",
        description="Search the authenticated user's saved memory. Read-only.",
        arguments_model=MemorySearchArguments,
        handler=memory_search_handler,
        required_scopes=frozenset({"memory:read"}),
        read_only=True,
        max_calls_per_turn=2,
    )
    if not allow_write:
        return
    registry.register(
        name="memory_save",
        description="Save a user-approved memory for the authenticated user.",
        arguments_model=MemorySaveArguments,
        handler=memory_save_handler,
        required_scopes=frozenset({"memory:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=2,
    )
    registry.register(
        name="memory_forget",
        description="Forget one saved memory after confirmation.",
        arguments_model=MemoryForgetArguments,
        handler=memory_forget_handler,
        required_scopes=frozenset({"memory:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=2,
    )
