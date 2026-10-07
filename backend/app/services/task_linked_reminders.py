"""Shared helper linking one-shot push reminders to clock-timed tasks."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.models.resources import Reminder, Task

LOGGER = logging.getLogger("voice-assistance-backend")

SyncReason = Literal[
    "due_set",
    "due_changed",
    "due_cleared",
    "completed",
    "cancelled",
    "deleted",
]


def has_real_clock(due_at: datetime | None, timezone_name: str | None) -> bool:
    """Determine whether a due timestamp contains a specific clock time.

    Date-only deadlines in this system are assigned 23:59 (end-of-day, either in
    local timezone or UTC). They denote all-day calendar targets and must not fire
    notification banners.
    """
    if due_at is None:
        return False
    if due_at.hour == 23 and due_at.minute == 59:
        return False
    try:
        tz = ZoneInfo((timezone_name or "UTC").strip())
    except (ZoneInfoNotFoundError, ValueError, AttributeError):
        tz = ZoneInfo("UTC")
    local_dt = due_at.astimezone(tz)
    return not (local_dt.hour == 23 and local_dt.minute == 59)


async def _get_scheduled_reminders(
    session: Any, task_id: uuid.UUID, user_id: uuid.UUID
) -> list[Reminder]:
    if hasattr(session, "scalars"):
        query = select(Reminder).where(
            Reminder.task_id == task_id,
            Reminder.user_id == user_id,
            Reminder.status == "scheduled",
        )
        return list((await session.scalars(query)).all())

    # Fallback for mock/test sessions
    items: list[Any] = []
    if hasattr(session, "reminders"):
        items.extend(session.reminders)
    if hasattr(session, "tasks"):
        items.extend(session.tasks)
    return [
        item
        for item in items
        if isinstance(item, Reminder)
        and item.task_id == task_id
        and item.user_id == user_id
        and item.status == "scheduled"
    ]


async def sync_linked_reminder_for_task(
    session: Any,
    task: Task,
    *,
    reason: str = "due_set",
) -> Reminder | None:
    """Synchronize at most one active one-shot push reminder for a task.

    Rules:
    - Reason in (completed, cancelled, deleted, due_cleared) or task status in
      (completed, cancelled) cancels any active scheduled reminder.
    - Tasks without a real clock (missing due_at or local 23:59 EOD) cancel any
      active scheduled reminder and do not create a reminder.
    - Active tasks with a real clock create or reschedule a one-shot push
      reminder matching task.due_at.
    """
    if task.id is None:
        task.id = uuid.uuid4()

    scheduled = await _get_scheduled_reminders(session, task.id, task.user_id)

    should_notify = (
        reason not in {"due_cleared", "completed", "cancelled", "deleted"}
        and task.status not in {"completed", "cancelled"}
        and has_real_clock(task.due_at, task.timezone)
    )

    if not should_notify:
        for reminder in scheduled:
            reminder.status = "cancelled"
            reminder.next_attempt_at = None
            reminder.locked_at = None
            reminder.locked_by = None
            reminder.lease_expires_at = None
        if hasattr(session, "flush"):
            res = session.flush()
            if asyncio.iscoroutine(res):
                await res
        return None

    assert task.due_at is not None
    timezone_name = task.timezone or "UTC"
    try:
        tz = ZoneInfo(timezone_name.strip())
    except (ZoneInfoNotFoundError, ValueError, AttributeError):
        tz = ZoneInfo("UTC")

    local_trigger_at = task.local_due_at or task.due_at.astimezone(tz)

    if scheduled:
        reminder = scheduled[0]
        reminder.title = task.title
        reminder.body = task.description
        reminder.trigger_at = task.due_at
        reminder.local_trigger_at = local_trigger_at
        reminder.timezone = timezone_name
        reminder.timezone_source = task.timezone_source or "device"
        reminder.recurrence_rule = None
        reminder.delivery_channel = "push"
        reminder.status = "scheduled"
        reminder.next_attempt_at = task.due_at
        reminder.failure_code = None
        reminder.failure_reason = None
        reminder.attempt_count = 0
        reminder.occurrence_count = 0
        reminder.plan_id = task.plan_id
        reminder.planning_action_id = task.planning_action_id

        # Cancel any unexpected duplicate scheduled reminders
        for extra in scheduled[1:]:
            extra.status = "cancelled"
            extra.next_attempt_at = None
            extra.locked_at = None
            extra.locked_by = None
            extra.lease_expires_at = None
    else:
        reminder = Reminder(
            user_id=task.user_id,
            task_id=task.id,
            plan_id=task.plan_id,
            planning_action_id=task.planning_action_id,
            source_turn_id=task.source_turn_id,
            title=task.title,
            body=task.description,
            trigger_at=task.due_at,
            local_trigger_at=local_trigger_at,
            timezone=timezone_name,
            timezone_source=task.timezone_source or "device",
            recurrence_rule=None,
            status="scheduled",
            delivery_channel="push",
            next_attempt_at=task.due_at,
        )
        session.add(reminder)

    if hasattr(session, "flush"):
        res = session.flush()
        if asyncio.iscoroutine(res):
            await res

    return reminder
