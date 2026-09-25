from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator
from sqlalchemy import or_, select

from app.llm.errors import LLMToolError, LLMToolTemporalResolutionError
from app.llm.tool_loop import ToolExecutionContext, ToolRegistry
from app.models import Reminder, Task
from app.services.recurrence import RecurrenceResolutionError, validate_recurrence_rule
from app.services.structured_reads import normalize_query_terms, resolve_local_day_bounds
from app.services.task_due_dates import TaskDueDateResolutionError, resolve_task_due_at

LOGGER = logging.getLogger("voice-assistance-backend")


class CreateReminderArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=1, max_length=255)
    body: StrictStr | None = Field(default=None, max_length=100_000)
    trigger_at: datetime | None = None
    trigger_expression: StrictStr | None = Field(default=None, max_length=256)
    task_id: uuid.UUID | None = None
    recurrence_rule: StrictStr | None = Field(default=None, max_length=512)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class UpdateReminderArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reminder_id: uuid.UUID
    title: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    body: StrictStr | None = Field(default=None, max_length=100_000)
    trigger_at: datetime | None = None
    trigger_expression: StrictStr | None = Field(default=None, max_length=256)
    task_id: uuid.UUID | None = None
    recurrence_rule: StrictStr | None = Field(default=None, max_length=512)


class DeleteReminderArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reminder_id: uuid.UUID


class ListRemindersArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["scheduled", "sent", "failed", "cancelled"] | None = None
    upcoming: StrictBool = False
    limit: int = Field(default=20, ge=1, le=50)
    date_window: Literal["today", "tomorrow"] | None = None
    next_only: StrictBool = False
    search_terms: list[StrictStr] = Field(default_factory=list, max_length=3)

    @field_validator("search_terms")
    @classmethod
    def validate_search_terms(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 48 for value in values):
            raise ValueError("search terms must contain 1 to 48 characters")
        if any(not all(char.isalnum() or char in " '-" for char in value) for value in values):
            raise ValueError("search terms contain unsupported characters")
        return values


def _resolve_trigger(
    context: ToolExecutionContext,
    *,
    trigger_at: datetime | None,
    trigger_expression: str | None,
) -> datetime | None:
    try:
        resolved = resolve_task_due_at(
            due_at=trigger_at,
            due_expression=trigger_expression,
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
                        if trigger_at is not None
                        else "explicit_expression"
                        if trigger_expression is not None
                        else "transcript"
                    ),
                },
            )
        return resolved
    except TaskDueDateResolutionError as error:
        raise LLMToolTemporalResolutionError(str(error)) from error


def _normalize_recurrence(value: str | None) -> str | None:
    try:
        return validate_recurrence_rule(value)
    except RecurrenceResolutionError as error:
        raise LLMToolError(f"invalid_recurrence: {error}") from error


def normalize_create_reminder_arguments(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> CreateReminderArguments:
    if not isinstance(arguments, CreateReminderArguments):
        raise LLMToolError("The reminder tool received an invalid argument model.")
    recurrence_rule = _normalize_recurrence(arguments.recurrence_rule)
    trigger_at = _resolve_trigger(
        context,
        trigger_at=arguments.trigger_at,
        trigger_expression=arguments.trigger_expression,
    )
    if trigger_at is None:
        raise LLMToolTemporalResolutionError("reminder trigger time is required")
    return arguments.model_copy(
        update={
            "trigger_at": trigger_at,
            "trigger_expression": None,
            "recurrence_rule": recurrence_rule,
        }
    )


def normalize_update_reminder_arguments(
    context: ToolExecutionContext,
    arguments: BaseModel,
) -> UpdateReminderArguments:
    if not isinstance(arguments, UpdateReminderArguments):
        raise LLMToolError("The reminder tool received an invalid argument model.")
    recurrence_rule = _normalize_recurrence(arguments.recurrence_rule)
    if (
        arguments.trigger_at is None
        and arguments.trigger_expression is None
        and not context.source_transcript
    ):
        return arguments.model_copy(update={"recurrence_rule": recurrence_rule})
    trigger_at = _resolve_trigger(
        context,
        trigger_at=arguments.trigger_at,
        trigger_expression=arguments.trigger_expression,
    )
    if trigger_at is None:
        raise LLMToolTemporalResolutionError("reminder trigger time is required")
    return arguments.model_copy(
        update={
            "trigger_at": trigger_at,
            "trigger_expression": None,
            "recurrence_rule": recurrence_rule,
        }
    )


async def _owned_task(context: ToolExecutionContext, task_id: uuid.UUID | None) -> None:
    if task_id is None:
        return
    assert context.db is not None
    task = await context.db.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == context.user_id)
    )
    if task is None:
        raise LLMToolError("task_not_found")


async def create_reminder_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, CreateReminderArguments):
        raise LLMToolError("The reminder tool requires a database session.")
    await _owned_task(context, arguments.task_id)
    recurrence_rule = _normalize_recurrence(arguments.recurrence_rule)
    assert arguments.trigger_at is not None
    reminder = Reminder(
        user_id=context.user_id,
        task_id=arguments.task_id,
        title=arguments.title,
        body=arguments.body,
        trigger_at=arguments.trigger_at,
        local_trigger_at=arguments.trigger_at.astimezone(ZoneInfo(context.user_timezone)),
        timezone=context.user_timezone,
        timezone_source=context.timezone_source,
        recurrence_rule=recurrence_rule,
        status="scheduled",
        delivery_channel="push",
        next_attempt_at=arguments.trigger_at,
    )
    context.db.add(reminder)
    await context.db.flush()
    return _reminder_result(reminder)


async def update_reminder_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, UpdateReminderArguments):
        raise LLMToolError("The reminder tool requires a database session.")
    recurrence_changed = "recurrence_rule" in arguments.model_fields_set
    reminder = await context.db.scalar(
        select(Reminder).where(
            Reminder.id == arguments.reminder_id,
            Reminder.user_id == context.user_id,
        )
    )
    if reminder is None:
        raise LLMToolError("reminder_not_found")
    recurrence_rule = (
        _normalize_recurrence(arguments.recurrence_rule)
        if recurrence_changed
        else reminder.recurrence_rule
    )
    await _owned_task(context, arguments.task_id)
    if arguments.title is not None:
        reminder.title = arguments.title
    if "body" in arguments.model_fields_set:
        reminder.body = arguments.body
    if "task_id" in arguments.model_fields_set:
        reminder.task_id = arguments.task_id
    should_reschedule = bool({"trigger_at", "trigger_expression"} & arguments.model_fields_set) or (
        recurrence_changed and recurrence_rule is not None
    )
    if should_reschedule:
        trigger_at = arguments.trigger_at or reminder.trigger_at
        if recurrence_rule is not None and trigger_at <= context.clock.now_utc():
            raise LLMToolTemporalResolutionError(
                "a future trigger time is required when enabling recurrence"
            )
        reminder.trigger_at = trigger_at
        reminder.local_trigger_at = trigger_at.astimezone(ZoneInfo(context.user_timezone))
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
    reminder.timezone = context.user_timezone
    reminder.timezone_source = context.timezone_source
    await context.db.flush()
    return _reminder_result(reminder)


async def delete_reminder_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, DeleteReminderArguments):
        raise LLMToolError("The reminder tool requires a database session.")
    reminder = await context.db.scalar(
        select(Reminder).where(
            Reminder.id == arguments.reminder_id,
            Reminder.user_id == context.user_id,
        )
    )
    if reminder is None:
        raise LLMToolError("reminder_not_found")
    reminder.status = "cancelled"
    reminder.next_attempt_at = None
    reminder.locked_at = None
    reminder.locked_by = None
    reminder.lease_expires_at = None
    await context.db.flush()
    return {"reminder_id": str(reminder.id), "status": reminder.status}


async def list_reminders_handler(
    context: ToolExecutionContext, arguments: BaseModel
) -> dict[str, Any]:
    if context.db is None or not isinstance(arguments, ListRemindersArguments):
        raise LLMToolError("The reminder tool requires a database session.")
    query = select(Reminder).where(Reminder.user_id == context.user_id)
    if arguments.status == "scheduled":
        query = query.where(Reminder.status.in_(("scheduled", "processing", "retry_wait")))
    elif arguments.status is not None:
        query = query.where(Reminder.status == arguments.status)
    now_utc = context.clock.now_utc()
    if arguments.date_window is not None:
        start_utc, end_utc = resolve_local_day_bounds(
            now_utc=now_utc,
            timezone_name=context.user_timezone,
            window=arguments.date_window,
        )
        query = query.where(Reminder.trigger_at >= start_utc, Reminder.trigger_at < end_utc)
    if arguments.upcoming or arguments.next_only:
        query = query.where(Reminder.trigger_at > now_utc)
    terms = normalize_query_terms(arguments.search_terms)
    if terms:
        query = query.where(
            or_(
                *(
                    predicate
                    for term in terms
                    for predicate in (
                        Reminder.title.contains(term, autoescape=True),
                        Reminder.body.contains(term, autoescape=True),
                    )
                )
            )
        )
    query = query.order_by(Reminder.trigger_at.asc(), Reminder.id.asc())
    query = query.limit(1 if arguments.next_only else arguments.limit)
    reminders = list((await context.db.scalars(query)).all())
    return {"reminders": [_reminder_result(reminder) for reminder in reminders]}


def _reminder_result(reminder: Reminder) -> dict[str, Any]:
    return {
        "reminder_id": str(reminder.id),
        "title": reminder.title,
        "body": reminder.body,
        "trigger_at": reminder.trigger_at.isoformat(),
        "local_trigger_at": (
            reminder.local_trigger_at.isoformat() if reminder.local_trigger_at is not None else None
        ),
        "timezone": reminder.timezone,
        "timezone_source": reminder.timezone_source,
        "status": reminder.status,
    }


def register_reminder_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="create_reminder",
        description="Create a push reminder for the authenticated user after confirmation.",
        arguments_model=CreateReminderArguments,
        handler=create_reminder_handler,
        argument_normalizer=normalize_create_reminder_arguments,
        required_scopes=frozenset({"reminders:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=1,
    )
    registry.register(
        name="update_reminder",
        description="Reschedule or update an owned reminder after confirmation.",
        arguments_model=UpdateReminderArguments,
        handler=update_reminder_handler,
        argument_normalizer=normalize_update_reminder_arguments,
        required_scopes=frozenset({"reminders:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=2,
    )
    registry.register(
        name="delete_reminder",
        description="Cancel an owned reminder after confirmation.",
        arguments_model=DeleteReminderArguments,
        handler=delete_reminder_handler,
        required_scopes=frozenset({"reminders:write"}),
        read_only=False,
        requires_confirmation=True,
        max_calls_per_turn=1,
    )
    registry.register(
        name="list_reminders",
        description="List reminders owned by the authenticated user.",
        arguments_model=ListRemindersArguments,
        handler=list_reminders_handler,
        required_scopes=frozenset({"reminders:read"}),
        read_only=True,
        max_calls_per_turn=2,
    )
