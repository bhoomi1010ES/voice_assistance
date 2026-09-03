from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.llm.errors import LLMToolError, LLMToolTemporalResolutionError
from app.llm.tool_loop import ToolExecutionContext, ToolRegistry
from app.models import Task
from app.services.task_due_dates import TaskDueDateResolutionError, resolve_task_due_at


class CreateTaskArguments(BaseModel):
    """Only model-controlled task fields; ownership is server-controlled."""

    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=1, max_length=255)
    due_at: datetime | None = None
    due_expression: StrictStr | None = Field(default=None, max_length=256)
    notes: StrictStr | None = Field(default=None, max_length=100_000)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


def normalize_create_task_arguments(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> CreateTaskArguments:
    """Resolve relative task dates before confirmation or idempotency is applied."""

    if not isinstance(arguments, CreateTaskArguments):
        raise LLMToolError("The task tool received an invalid argument model.")
    try:
        due_at = resolve_task_due_at(
            due_at=arguments.due_at,
            due_expression=arguments.due_expression,
            source_transcript=context.source_transcript,
            now_utc=context.clock.now_utc(),
            timezone_name=context.user_timezone,
        )
    except TaskDueDateResolutionError as error:
        raise LLMToolTemporalResolutionError(str(error)) from error
    return arguments.model_copy(update={"due_at": due_at, "due_expression": None})


async def create_task_handler(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    if context.db is None:
        raise LLMToolError("The task tool requires a database session.")
    if not isinstance(arguments, CreateTaskArguments):
        raise LLMToolError("The task tool received an invalid argument model.")

    task = Task(
        user_id=context.user_id,
        title=arguments.title,
        description=arguments.notes,
        due_at=arguments.due_at,
    )
    context.db.add(task)
    await context.db.flush()
    return {
        "task_id": str(task.id),
        "title": task.title,
        "due_at": task.due_at.isoformat() if task.due_at is not None else None,
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
