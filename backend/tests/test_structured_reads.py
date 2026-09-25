from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.clock import FrozenClock
from app.llm.reminder_tools import ListRemindersArguments, list_reminders_handler
from app.llm.task_tools import ListTasksArguments, list_tasks_handler
from app.llm.tool_loop import ToolExecutionContext
from app.routing.formatters import format_structured_read_answer
from app.services.structured_reads import resolve_local_day_bounds


class RecordingDatabase:
    def __init__(self) -> None:
        self.statement = None

    async def scalars(self, statement):
        self.statement = statement
        return SimpleNamespace(all=lambda: [])


def _context(database: RecordingDatabase, *, instant: datetime, timezone: str):
    return ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        scopes=frozenset({"tasks:read", "reminders:read"}),
        db=database,
        clock=FrozenClock(instant),
        user_timezone=timezone,
    )


@pytest.mark.parametrize(
    ("instant", "window", "expected_hours"),
    [
        ("2026-03-08T12:00:00+00:00", "today", 23),
        ("2026-11-01T12:00:00+00:00", "today", 25),
        ("2026-03-07T12:00:00+00:00", "tomorrow", 23),
    ],
)
def test_local_calendar_windows_use_dst_correct_utc_bounds(
    instant: str, window: str, expected_hours: int
) -> None:
    start, end = resolve_local_day_bounds(
        now_utc=datetime.fromisoformat(instant),
        timezone_name="America/New_York",
        window=window,
    )

    assert start.tzinfo == UTC
    assert end.tzinfo == UTC
    assert (end - start).total_seconds() == expected_hours * 60 * 60


@pytest.mark.asyncio
async def test_task_read_query_is_owner_scoped_bounded_to_local_day_and_next_ordered() -> None:
    database = RecordingDatabase()
    context = _context(
        database,
        instant=datetime(2026, 3, 8, 15, tzinfo=UTC),
        timezone="America/New_York",
    )

    await list_tasks_handler(
        context,
        ListTasksArguments(
            date_window="today",
            next_only=True,
            active_only=True,
            search_terms=["meeting"],
        ),
    )

    compiled = database.statement.compile()
    sql = str(compiled).lower()
    values = list(compiled.params.values())
    expected_start, expected_end = resolve_local_day_bounds(
        now_utc=context.clock.now_utc(),
        timezone_name=context.user_timezone,
        window="today",
    )
    assert context.user_id in values
    assert expected_start in values
    assert expected_end in values
    assert "tasks.due_at asc nulls last" in sql
    assert " limit " in sql


@pytest.mark.asyncio
async def test_reminder_read_query_is_owner_scoped_status_filtered_and_windowed() -> None:
    database = RecordingDatabase()
    context = _context(
        database,
        instant=datetime(2026, 11, 1, 15, tzinfo=UTC),
        timezone="America/New_York",
    )

    await list_reminders_handler(
        context,
        ListRemindersArguments(
            status="scheduled",
            date_window="today",
            next_only=True,
            upcoming=True,
            search_terms=["medicine", "pill"],
        ),
    )

    compiled = database.statement.compile()
    sql = str(compiled).lower()
    values = list(compiled.params.values())
    expected_start, expected_end = resolve_local_day_bounds(
        now_utc=context.clock.now_utc(),
        timezone_name=context.user_timezone,
        window="today",
    )
    assert context.user_id in values
    assert expected_start in values
    assert expected_end in values
    assert "reminders.status in" in sql
    assert "reminders.trigger_at asc" in sql
    assert " limit " in sql


@pytest.mark.parametrize(
    ("rows", "arguments", "expected"),
    [
        (
            [],
            {"date_window": "today"},
            "You don't have any tasks scheduled for today.",
        ),
        (
            [
                {"title": "Call Mina", "due_at": None, "local_due_at": None},
                {"title": "Book dentist", "due_at": None, "local_due_at": None},
            ],
            {},
            "You have 2 tasks: Task 'Call Mina'; Task 'Book dentist'.",
        ),
    ],
)
def test_structured_formatter_handles_empty_and_multiple_rows(rows, arguments, expected) -> None:
    import json

    result = format_structured_read_answer(
        tool_name="list_tasks",
        result_content=json.dumps({"ok": True, "result": {"tasks": rows}}),
        success=True,
        read_arguments=arguments,
    )

    assert result == expected


def test_structured_formatter_distinguishes_database_failure_from_empty_rows() -> None:
    result = format_structured_read_answer(
        tool_name="list_reminders",
        result_content='{"ok":false,"error":{"code":"llm_tool_execution_failed"}}',
        success=False,
        read_arguments={"upcoming": True},
    )

    assert result == "I couldn't check your reminders right now."
