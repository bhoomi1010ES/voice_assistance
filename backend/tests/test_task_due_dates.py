from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.core.clock import FrozenClock
from app.llm.reminder_tools import CreateReminderArguments, normalize_create_reminder_arguments
from app.llm.task_tools import (
    CreateTaskArguments,
    normalize_create_task_arguments,
    register_task_tools,
)
from app.llm.tool_loop import (
    InMemoryToolIdempotencyStore,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
)
from app.llm.types import LLMToolCall
from app.services.task_due_dates import TaskDueDateResolutionError, resolve_task_due_at


def _clock(value: str) -> FrozenClock:
    return FrozenClock(datetime.fromisoformat(value))


def test_tomorrow_at_nine_uses_user_timezone_and_ignores_stale_model_timestamp() -> None:
    resolved = resolve_task_due_at(
        due_at=datetime(2025, 8, 15, 9, tzinfo=UTC),
        due_expression=None,
        source_transcript="Remind me to call Rahul tomorrow at 9 a.m.",
        now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
        timezone_name="Asia/Kolkata",
    )

    assert resolved == datetime(2026, 9, 4, 3, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("today at 11:45 PM", datetime(2026, 9, 3, 18, 15, tzinfo=UTC)),
        ("in 2 hours", datetime(2026, 9, 3, 20, 0, tzinfo=UTC)),
        ("Friday at 9 AM", datetime(2026, 9, 4, 3, 30, tzinfo=UTC)),
        ("September 10, 2026 at 4 PM", datetime(2026, 9, 10, 10, 30, tzinfo=UTC)),
        ("tomorrow", datetime(2026, 9, 4, 18, 29, tzinfo=UTC)),
        ("day after tomorrow", datetime(2026, 9, 5, 18, 29, tzinfo=UTC)),
        ("today", datetime(2026, 9, 3, 18, 29, tzinfo=UTC)),
        ("tonight", datetime(2026, 9, 3, 18, 29, tzinfo=UTC)),
        ("this evening", datetime(2026, 9, 3, 18, 29, tzinfo=UTC)),
        ("13 September", datetime(2026, 9, 13, 18, 29, tzinfo=UTC)),
        ("13th September", datetime(2026, 9, 13, 18, 29, tzinfo=UTC)),
        ("September 13", datetime(2026, 9, 13, 18, 29, tzinfo=UTC)),
        ("2 October", datetime(2026, 10, 2, 18, 29, tzinfo=UTC)),
        ("2nd October", datetime(2026, 10, 2, 18, 29, tzinfo=UTC)),
        ("October 2", datetime(2026, 10, 2, 18, 29, tzinfo=UTC)),
        ("2 Oct", datetime(2026, 10, 2, 18, 29, tzinfo=UTC)),
        ("in 30 minutes", datetime(2026, 9, 3, 18, 30, tzinfo=UTC)),
    ],
)
def test_supported_relative_and_absolute_expressions(expression: str, expected: datetime) -> None:
    assert (
        resolve_task_due_at(
            due_at=None,
            due_expression=expression,
            source_transcript=None,
            now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
            timezone_name="Asia/Kolkata",
        )
        == expected
    )


def test_task_without_date_keeps_due_at_empty() -> None:
    assert (
        resolve_task_due_at(
            due_at=None,
            due_expression=None,
            source_transcript="Create a task to submit the report.",
            now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
            timezone_name="Asia/Kolkata",
        )
        is None
    )


@pytest.mark.parametrize(
    ("clock_text", "expected"),
    [
        ("at 2.45 PM", datetime(2026, 10, 7, 9, 15, tzinfo=UTC)),
        ("at 2:45 PM", datetime(2026, 10, 7, 9, 15, tzinfo=UTC)),
        ("at 2.45 p.m.", datetime(2026, 10, 7, 9, 15, tzinfo=UTC)),
        ("2.45 PM", datetime(2026, 10, 7, 9, 15, tzinfo=UTC)),
        ("around 14.45", datetime(2026, 10, 7, 9, 15, tzinfo=UTC)),
        ("by 14:45", datetime(2026, 10, 7, 9, 15, tzinfo=UTC)),
        ("at 2.45 AM", datetime(2026, 10, 6, 21, 15, tzinfo=UTC)),
        ("at 12.00 AM", datetime(2026, 10, 6, 18, 30, tzinfo=UTC)),
        ("at 12.00 PM", datetime(2026, 10, 7, 6, 30, tzinfo=UTC)),
    ],
)
def test_dotted_and_colon_clock_times_preserve_minutes_and_meridiem(
    clock_text: str, expected: datetime
) -> None:
    assert (
        resolve_task_due_at(
            due_at=None,
            due_expression=None,
            source_transcript=f"Remind me to call Parth {clock_text} on 7 October",
            now_utc=datetime(2026, 10, 6, 18, tzinfo=UTC),
            timezone_name="Asia/Kolkata",
        )
        == expected
    )


@pytest.mark.parametrize(
    ("prefix", "clock_text"),
    [
        (prefix, clock_text)
        for prefix in ("at ", "")
        for clock_text in (
            "2.75 PM",
            "2:75 PM",
            "2.5 PM",
            "2:5 PM",
            "2.450 PM",
            "2:45:30 PM",
            "2.45.30 PM",
            "2..45 PM",
            "2:abc PM",
            "123 PM",
            "25.45",
            "13.45 PM",
        )
        if prefix or "PM" in clock_text
    ],
)
def test_malformed_clock_is_rejected_instead_of_creating_a_different_time(
    clock_text: str,
    prefix: str,
) -> None:
    with pytest.raises(TaskDueDateResolutionError):
        resolve_task_due_at(
            due_at=None,
            due_expression=None,
            source_transcript=f"Remind me to call Parth {prefix}{clock_text} on 7 October",
            now_utc=datetime(2026, 10, 6, 18, tzinfo=UTC),
            timezone_name="Asia/Kolkata",
        )


def test_dotted_explicit_time_still_rejects_a_past_deadline() -> None:
    with pytest.raises(TaskDueDateResolutionError, match="future"):
        resolve_task_due_at(
            due_at=None,
            due_expression=None,
            source_transcript="Remind me to call Parth on 7 October at 2.45 PM",
            now_utc=datetime(2026, 10, 7, 9, 16, tzinfo=UTC),
            timezone_name="Asia/Kolkata",
        )


def test_unrelated_decimal_does_not_become_a_clock_time() -> None:
    assert resolve_task_due_at(
        due_at=None,
        due_expression=None,
        source_transcript="Create a task to buy 2.45 kg of rice on 7 October",
        now_utc=datetime(2026, 10, 6, 18, tzinfo=UTC),
        timezone_name="Asia/Kolkata",
    ) == datetime(2026, 10, 7, 18, 29, tzinfo=UTC)


@pytest.mark.parametrize("tool_name", ["task", "reminder"])
def test_task_and_reminder_tools_resolve_the_reported_dotted_time(tool_name: str) -> None:
    context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        scopes=frozenset({"tasks:write", "reminders:write"}),
        clock=_clock("2026-10-07T09:08:12.164+00:00"),
        user_timezone="Asia/Kolkata",
        source_transcript="Remind me to call Parth on 7 October at 2.45 PM",
    )
    expected = datetime(2026, 10, 7, 9, 15, tzinfo=UTC)
    if tool_name == "task":
        result = normalize_create_task_arguments(context, CreateTaskArguments(title="Call Parth"))
        assert result.due_at == expected
    else:
        result = normalize_create_reminder_arguments(
            context, CreateReminderArguments(title="Call Parth")
        )
        assert result.trigger_at == expected


@pytest.mark.parametrize("clock", ["four PM", "four p.m.", "4 p.m.", "four P. M."])
def test_spoken_and_abbreviated_clock_hours_use_local_time(clock):
    assert resolve_task_due_at(
        due_at=None,
        due_expression=None,
        source_transcript=f"Remind me to write Friday at {clock}",
        now_utc=datetime(2026, 10, 6, 12, tzinfo=UTC),
        timezone_name="Asia/Kolkata",
    ) == datetime(2026, 10, 9, 10, 30, tzinfo=UTC)


def test_date_only_task_uses_end_of_day_in_device_timezone() -> None:
    resolved = resolve_task_due_at(
        due_at=None,
        due_expression=None,
        source_transcript="Create a task to submit the report tomorrow.",
        now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
        timezone_name="Asia/Kolkata",
    )

    assert resolved == datetime(2026, 9, 4, 18, 29, tzinfo=UTC)


def test_past_yearless_date_fails_using_current_year_policy() -> None:
    with pytest.raises(TaskDueDateResolutionError, match="future"):
        resolve_task_due_at(
            due_at=None,
            due_expression=None,
            source_transcript="Create a task on 2 September.",
            now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
            timezone_name="Asia/Kolkata",
        )


def test_invalid_ordinal_date_fails_safely() -> None:
    with pytest.raises(TaskDueDateResolutionError, match="invalid"):
        resolve_task_due_at(
            due_at=None,
            due_expression=None,
            source_transcript="Create a task on 32nd October.",
            now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
            timezone_name="Asia/Kolkata",
        )


def test_ambiguous_numeric_date_fails_safely() -> None:
    with pytest.raises(TaskDueDateResolutionError, match="ambiguous"):
        resolve_task_due_at(
            due_at=None,
            due_expression=None,
            source_transcript="Create a task for 10/11.",
            now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
            timezone_name="Asia/Kolkata",
        )


def test_past_and_naive_due_dates_fail_safely() -> None:
    with pytest.raises(TaskDueDateResolutionError, match="future"):
        resolve_task_due_at(
            due_at=datetime(2026, 9, 3, 17, 59, tzinfo=UTC),
            due_expression=None,
            source_transcript=None,
            now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
            timezone_name="UTC",
        )
    with pytest.raises(TaskDueDateResolutionError, match="timezone-aware"):
        resolve_task_due_at(
            due_at=datetime(2026, 9, 4, 9, 0),
            due_expression=None,
            source_transcript=None,
            now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
            timezone_name="UTC",
        )


def test_new_york_dst_transition_is_resolved_as_local_time() -> None:
    resolved = resolve_task_due_at(
        due_at=None,
        due_expression="tomorrow at 9 AM",
        source_transcript=None,
        now_utc=datetime(2026, 3, 7, 17, 0, tzinfo=UTC),
        timezone_name="America/New_York",
    )

    assert resolved == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)


def test_invalid_timezone_fails_without_mutation() -> None:
    with pytest.raises(TaskDueDateResolutionError, match="timezone is invalid"):
        resolve_task_due_at(
            due_at=None,
            due_expression="tomorrow at 9 AM",
            source_transcript=None,
            now_utc=datetime(2026, 9, 3, 18, 0, tzinfo=UTC),
            timezone_name="Not/A_Timezone",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_transcript", "expected_due_at"),
    [
        (
            "Remind me to call Rahul tomorrow at 9 AM.",
            datetime(2026, 9, 4, 3, 30, tzinfo=UTC),
        ),
        (
            "Create a task to submit the report tomorrow.",
            datetime(2026, 9, 4, 18, 29, tzinfo=UTC),
        ),
    ],
)
async def test_create_task_freezes_resolved_due_at_before_confirmation(
    source_transcript: str,
    expected_due_at: datetime,
) -> None:
    class FakeDatabase:
        def __init__(self) -> None:
            self.tasks = []

        def add(self, task) -> None:
            self.tasks.append(task)

        async def flush(self) -> None:
            for task in self.tasks:
                if task.id is None:
                    task.id = uuid.uuid4()

    registry = ToolRegistry()
    register_task_tools(registry)
    database = FakeDatabase()
    captured = []

    async def save_confirmation(_call, arguments, _tool) -> bool:
        captured.append(arguments)
        return True

    request_context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        scopes=frozenset({"tasks:write"}),
        db=database,
        clock=_clock("2026-09-03T18:00:00+00:00"),
        user_timezone="Asia/Kolkata",
        source_transcript=source_transcript,
        confirmation_requested=save_confirmation,
    )
    call = LLMToolCall(
        tool_call_id="call-relative-date",
        name="create_task",
        arguments={"title": "Call Rahul", "due_at": "2025-08-15T09:00:00Z"},
    )
    executor = ToolExecutor(registry, idempotency_store=InMemoryToolIdempotencyStore())

    pending = await executor.execute(call, context=request_context)

    assert pending.error_code == "llm_tool_confirmation_required"
    assert pending.executed is False
    assert database.tasks == []
    assert captured[0].due_at == expected_due_at
    assert captured[0].due_expression is None

    approved = await executor.execute(
        LLMToolCall(
            tool_call_id=call.tool_call_id,
            name=call.name,
            arguments=captured[0].model_dump(mode="json"),
        ),
        context=replace(
            request_context,
            confirmed_tool_call_ids=frozenset({call.tool_call_id}),
            source_transcript=None,
        ),
    )

    assert approved.success is True
    assert approved.executed is True
    assert database.tasks[0].due_at == expected_due_at
