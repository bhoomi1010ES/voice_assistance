from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator
from sqlalchemy import or_, select

from app.llm.errors import LLMToolError, LLMToolTemporalResolutionError
from app.llm.tool_loop import ToolExecutionContext, ToolRegistry
from app.models import ConversationTurn, Task
from app.services.structured_reads import normalize_query_terms, resolve_local_day_bounds
from app.services.task_due_dates import TaskDueDateResolutionError, resolve_task_due_at

LOGGER = logging.getLogger("voice-assistance-backend")


class CreateTaskArguments(BaseModel):
    """Only model-controlled task fields; ownership is server-controlled."""

    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=1, max_length=255)
    due_at: datetime | None = None
    due_expression: StrictStr | None = Field(default=None, max_length=256)
    notes: StrictStr | None = Field(default=None, max_length=100_000)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class UpdateTaskArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID
    title: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    notes: StrictStr | None = Field(default=None, max_length=100_000)
    status: Literal["pending", "in_progress", "completed", "cancelled"] | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    due_at: datetime | None = None
    due_expression: StrictStr | None = Field(default=None, max_length=256)


class CompleteTaskArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID


class ListTasksArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "in_progress", "completed", "cancelled"] | None = None
    limit: int = Field(default=20, ge=1, le=50)
    date_window: Literal["today", "tomorrow"] | None = None
    next_only: StrictBool = False
    active_only: StrictBool = False
    search_terms: list[StrictStr] = Field(default_factory=list, max_length=3)

    @field_validator("search_terms")
    @classmethod
    def validate_search_terms(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 48 for value in values):
            raise ValueError("search terms must contain 1 to 48 characters")
        if any(not all(char.isalnum() or char in " '-" for char in value) for value in values):
            raise ValueError("search terms contain unsupported characters")
        return values


def _resolve_due(
    context: ToolExecutionContext,
    *,
    due_at: datetime | None,
    due_expression: str | None,
) -> datetime | None:
    try:
        resolved = resolve_task_due_at(
            due_at=due_at,
            due_expression=due_expression,
            source_transcript=context.source_transcript,
            now_utc=context.clock.now_utc(),
            timezone_name=context.user_timezone,
        )
        if resolved is not None:
            LOGGER.info(
                "DATETIME_RESOLUTION",
                extra={
                    "event": "datetime.resolution",
                    "timezone": context.user_timezone,
                    "timezone_source": context.timezone_source,
                    "resolution_status": "resolved",
                    "resolution_basis": (
                        "explicit_datetime"
                        if due_at is not None
                        else "explicit_expression"
                        if due_expression is not None
                        else "transcript"
                    ),
                },
            )
        return resolved
    except TaskDueDateResolutionError as error:
        raise LLMToolTemporalResolutionError(str(error)) from error


def normalize_create_task_arguments(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> CreateTaskArguments:
    if not isinstance(arguments, CreateTaskArguments):
        raise LLMToolError("The task tool received an invalid argument model.")
    due_at = _resolve_due(
        context,
        due_at=arguments.due_at,
        due_expression=arguments.due_expression,
    )
    return arguments.model_copy(update={"due_at": due_at, "due_expression": None})


def normalize_update_task_arguments(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> UpdateTaskArguments:
    if not isinstance(arguments, UpdateTaskArguments):
        raise LLMToolError("The task tool received an invalid argument model.")
    if (
        arguments.due_at is None
        and arguments.due_expression is None
        and not context.source_transcript
    ):
        return arguments
    due_at = _resolve_due(
        context,
        due_at=arguments.due_at,
        due_expression=arguments.due_expression,
    )
    return arguments.model_copy(update={"due_at": due_at, "due_expression": None})


async def create_task_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, CreateTaskArguments):
        raise LLMToolError("The task tool requires a database session.")
    source_turn_id = None
    if hasattr(context.db, "scalar"):
        source_turn_id = await context.db.scalar(
            select(ConversationTurn.id).where(
                ConversationTurn.id == context.turn_id,
                ConversationTurn.user_id == context.user_id,
            )
        )
    task = Task(
        user_id=context.user_id,
        title=arguments.title,
        description=arguments.notes,
        priority=arguments.priority,
        due_at=arguments.due_at,
        local_due_at=(
            arguments.due_at.astimezone(ZoneInfo(context.user_timezone))
            if arguments.due_at is not None
            else None
        ),
        timezone=context.user_timezone,
        timezone_source=context.timezone_source,
        source_turn_id=source_turn_id,
    )
    context.db.add(task)
    await context.db.flush()
    return _task_result(task)


async def update_task_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, UpdateTaskArguments):
        raise LLMToolError("The task tool requires a database session.")
    task = await context.db.scalar(
        select(Task).where(Task.id == arguments.task_id, Task.user_id == context.user_id)
    )
    if task is None:
        raise LLMToolError("task_not_found")
    if arguments.title is not None:
        task.title = arguments.title
    if "notes" in arguments.model_fields_set:
        task.description = arguments.notes
    if arguments.status is not None:
        task.status = arguments.status
        task.completed_at = datetime.now(UTC) if arguments.status == "completed" else None
    if arguments.priority is not None:
        task.priority = arguments.priority
    if {"due_at", "due_expression"} & arguments.model_fields_set:
        task.due_at = arguments.due_at
        task.local_due_at = (
            arguments.due_at.astimezone(ZoneInfo(context.user_timezone))
            if arguments.due_at is not None
            else None
        )
    task.timezone = context.user_timezone
    task.timezone_source = context.timezone_source
    await context.db.flush()
    return _task_result(task)


async def complete_task_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, CompleteTaskArguments):
        raise LLMToolError("The task tool requires a database session.")
    task = await context.db.scalar(
        select(Task).where(Task.id == arguments.task_id, Task.user_id == context.user_id)
    )
    if task is None:
        raise LLMToolError("task_not_found")
    task.status = "completed"
    task.completed_at = datetime.now(UTC)
    await context.db.flush()
    return _task_result(task)


async def list_tasks_handler(context: ToolExecutionContext, arguments: BaseModel) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, ListTasksArguments):
        raise LLMToolError("The task tool requires a database session.")
    query = select(Task).where(Task.user_id == context.user_id)
    if arguments.status is not None:
        query = query.where(Task.status == arguments.status)
    elif arguments.active_only:
        query = query.where(Task.status.in_(("pending", "in_progress")))
    now_utc = context.clock.now_utc()
    if arguments.date_window is not None:
        start_utc, end_utc = resolve_local_day_bounds(
            now_utc=now_utc,
            timezone_name=context.user_timezone,
            window=arguments.date_window,
        )
        query = query.where(Task.due_at >= start_utc, Task.due_at < end_utc)
    if arguments.next_only:
        query = query.where(Task.due_at > now_utc)
    terms = normalize_query_terms(arguments.search_terms)
    if terms:
        query = query.where(
            or_(
                *(
                    predicate
                    for term in terms
                    for predicate in (
                        Task.title.contains(term, autoescape=True),
                        Task.description.contains(term, autoescape=True),
                    )
                )
            )
        )
    if arguments.date_window is not None or arguments.next_only:
        query = query.order_by(Task.due_at.asc().nulls_last(), Task.id.asc())
    else:
        query = query.order_by(Task.created_at.desc(), Task.id.desc())
    query = query.limit(1 if arguments.next_only else arguments.limit)
    tasks = list((await context.db.scalars(query)).all())
    return {"tasks": [_task_result(task) for task in tasks]}


def _task_result(task: Task) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "due_at": task.due_at.isoformat() if task.due_at is not None else None,
        "local_due_at": task.local_due_at.isoformat() if task.local_due_at is not None else None,
        "timezone": task.timezone,
        "timezone_source": task.timezone_source,
    }


def register_task_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="create_task",
        description="Create a task for the authenticated user after confirmation.",
        arguments_model=CreateTaskArguments,
        handler=create_task_handler,
        argument_normalizer=normalize_create_task_arguments,
        required_scopes=frozenset({"tasks:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=1,
    )
    registry.register(
        name="update_task",
        description="Update an owned task after confirmation.",
        arguments_model=UpdateTaskArguments,
        handler=update_task_handler,
        argument_normalizer=normalize_update_task_arguments,
        required_scopes=frozenset({"tasks:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=2,
    )
    registry.register(
        name="complete_task",
        description="Mark an owned task complete after confirmation.",
        arguments_model=CompleteTaskArguments,
        handler=complete_task_handler,
        required_scopes=frozenset({"tasks:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=1,
    )
    registry.register(
        name="list_tasks",
        description="List tasks owned by the authenticated user.",
        arguments_model=ListTasksArguments,
        handler=list_tasks_handler,
        required_scopes=frozenset({"tasks:read"}),
        read_only=True,
        max_calls_per_turn=2,
    )
