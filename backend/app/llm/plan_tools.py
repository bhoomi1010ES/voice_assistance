from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from sqlalchemy import select

from app.llm.errors import LLMToolError, LLMToolTemporalResolutionError
from app.llm.tool_loop import ToolExecutionContext, ToolRegistry
from app.models import ConversationTurn, Plan, PlanContextItem, VoiceSession
from app.services.task_due_dates import TaskDueDateResolutionError, resolve_task_due_at

LOGGER = logging.getLogger("voice-assistance-backend")


class CreatePlanArguments(BaseModel):
    """Only model-controlled plan fields; ownership and IDs are server-controlled."""

    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=255)
    goal: StrictStr | None = Field(default=None, max_length=100_000)
    deadline_at: datetime | None = None
    deadline_expression: StrictStr | None = Field(default=None, max_length=256)
    timezone: StrictStr | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class UpdatePlanArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: uuid.UUID
    name: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    goal: StrictStr | None = Field(default=None, max_length=100_000)
    status: Literal["active", "completed", "archived"] | None = None
    deadline_at: datetime | None = None
    deadline_expression: StrictStr | None = Field(default=None, max_length=256)
    timezone: StrictStr | None = Field(default=None, max_length=64)
    expected_revision: int | None = None


class AddPlanContextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: uuid.UUID
    content: StrictStr = Field(min_length=1, max_length=2048)
    kind: Literal["note", "requirement", "decision", "resource"] = "note"
    value: dict[str, Any] | None = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


class GetPlanArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: uuid.UUID


def normalize_create_plan_arguments(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> CreatePlanArguments:
    if not isinstance(arguments, CreatePlanArguments):
        raise LLMToolError("The plan tool received an invalid argument model.")
    deadline_at = None
    if arguments.deadline_at is not None or arguments.deadline_expression is not None:
        try:
            deadline_at = resolve_task_due_at(
                due_at=arguments.deadline_at,
                due_expression=arguments.deadline_expression,
                source_transcript=context.source_transcript,
                now_utc=context.clock.now_utc(),
                timezone_name=arguments.timezone or context.user_timezone,
            )
        except TaskDueDateResolutionError as error:
            raise LLMToolTemporalResolutionError(str(error)) from error
    return arguments.model_copy(update={"deadline_at": deadline_at, "deadline_expression": None})


def normalize_update_plan_arguments(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> UpdatePlanArguments:
    if not isinstance(arguments, UpdatePlanArguments):
        raise LLMToolError("The plan tool received an invalid argument model.")
    deadline_at = None
    if arguments.deadline_at is not None or arguments.deadline_expression is not None:
        try:
            deadline_at = resolve_task_due_at(
                due_at=arguments.deadline_at,
                due_expression=arguments.deadline_expression,
                source_transcript=context.source_transcript,
                now_utc=context.clock.now_utc(),
                timezone_name=arguments.timezone or context.user_timezone,
            )
        except TaskDueDateResolutionError as error:
            raise LLMToolTemporalResolutionError(str(error)) from error
        return arguments.model_copy(update={"deadline_at": deadline_at, "deadline_expression": None})
    return arguments


async def create_plan_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, CreatePlanArguments):
        raise LLMToolError("The plan tool requires a database session.")
    source_turn_id = None
    if hasattr(context.db, "scalar"):
        source_turn_id = await context.db.scalar(
            select(ConversationTurn.id).where(
                ConversationTurn.id == context.turn_id,
                ConversationTurn.user_id == context.user_id,
            )
        )
    source_session_id = None
    if hasattr(context.db, "scalar"):
        source_session_id = await context.db.scalar(
            select(VoiceSession.id).where(
                VoiceSession.id == context.session_id,
                VoiceSession.user_id == context.user_id,
            )
        )
    plan = Plan(
        user_id=context.user_id,
        name=arguments.name,
        goal=arguments.goal,
        deadline_at=arguments.deadline_at,
        timezone=arguments.timezone or context.user_timezone,
        source_session_id=source_session_id,
        source_turn_id=source_turn_id,
    )
    context.db.add(plan)
    await context.db.flush()
    return _plan_result(plan)


async def update_plan_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, UpdatePlanArguments):
        raise LLMToolError("The plan tool requires a database session.")
    plan = await context.db.scalar(
        select(Plan).where(Plan.id == arguments.plan_id, Plan.user_id == context.user_id)
    )
    if plan is None:
        raise LLMToolError("plan_not_found")
    if arguments.expected_revision is not None and plan.revision != arguments.expected_revision:
        raise LLMToolError("revision_conflict")
    if arguments.name is not None:
        plan.name = arguments.name
    if "goal" in arguments.model_fields_set:
        plan.goal = arguments.goal
    if arguments.status is not None:
        plan.status = arguments.status
    if {"deadline_at", "deadline_expression"} & arguments.model_fields_set:
        plan.deadline_at = arguments.deadline_at
    if arguments.timezone is not None:
        plan.timezone = arguments.timezone
    await context.db.flush()
    return _plan_result(plan)


async def add_plan_context_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, AddPlanContextArguments):
        raise LLMToolError("The plan tool requires a database session.")
    plan = await context.db.scalar(
        select(Plan.id).where(Plan.id == arguments.plan_id, Plan.user_id == context.user_id)
    )
    if plan is None:
        raise LLMToolError("plan_not_found")
    dedupe_key = hashlib.sha256(arguments.content.strip().casefold().encode()).hexdigest()[:64]
    existing = await context.db.scalar(
        select(PlanContextItem).where(
            PlanContextItem.plan_id == arguments.plan_id,
            PlanContextItem.user_id == context.user_id,
            PlanContextItem.dedupe_key == dedupe_key,
            PlanContextItem.status == "active",
        )
    )
    if existing is not None:
        if arguments.value is not None:
            existing.value_json = arguments.value
            await context.db.flush()
        return _context_result(existing, is_duplicate=True)
    source_turn_id = None
    if hasattr(context.db, "scalar"):
        source_turn_id = await context.db.scalar(
            select(ConversationTurn.id).where(
                ConversationTurn.id == context.turn_id,
                ConversationTurn.user_id == context.user_id,
            )
        )
    item = PlanContextItem(
        user_id=context.user_id,
        plan_id=arguments.plan_id,
        kind=arguments.kind,
        content=arguments.content,
        value_json=arguments.value,
        dedupe_key=dedupe_key,
        source_turn_id=source_turn_id,
    )
    context.db.add(item)
    await context.db.flush()
    return _context_result(item, is_duplicate=False)


async def get_plan_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, GetPlanArguments):
        raise LLMToolError("The plan tool requires a database session.")
    plan = await context.db.scalar(
        select(Plan).where(Plan.id == arguments.plan_id, Plan.user_id == context.user_id)
    )
    if plan is None:
        raise LLMToolError("plan_not_found")
    return _plan_result(plan)


def _plan_result(plan: Plan) -> dict[str, Any]:
    return {
        "plan_id": str(plan.id),
        "name": plan.name,
        "goal": plan.goal,
        "status": plan.status,
        "deadline_at": plan.deadline_at.isoformat() if plan.deadline_at is not None else None,
        "timezone": plan.timezone,
        "revision": plan.revision,
    }


def _context_result(item: PlanContextItem, is_duplicate: bool = False) -> dict[str, Any]:
    return {
        "context_id": str(item.id),
        "plan_id": str(item.plan_id),
        "kind": item.kind,
        "content": item.content,
        "status": "duplicate" if is_duplicate else "created",
        "item_status": item.status,
        "revision": item.revision,
    }


def register_plan_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="create_plan",
        description="Create an owned plan for the authenticated user after confirmation.",
        arguments_model=CreatePlanArguments,
        handler=create_plan_handler,
        argument_normalizer=normalize_create_plan_arguments,
        required_scopes=frozenset({"plans:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=1,
    )
    registry.register(
        name="update_plan",
        description="Update an owned plan after confirmation.",
        arguments_model=UpdatePlanArguments,
        handler=update_plan_handler,
        argument_normalizer=normalize_update_plan_arguments,
        required_scopes=frozenset({"plans:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=2,
    )
    registry.register(
        name="add_plan_context",
        description="Add a sourced context note to an owned plan after confirmation.",
        arguments_model=AddPlanContextArguments,
        handler=add_plan_context_handler,
        required_scopes=frozenset({"plans:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=4,
    )
    registry.register(
        name="get_plan",
        description="Retrieve an owned plan by ID.",
        arguments_model=GetPlanArguments,
        handler=get_plan_handler,
        required_scopes=frozenset({"plans:read"}),
        read_only=True,
        max_calls_per_turn=2,
    )
