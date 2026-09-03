from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.core.clock import FrozenClock
from app.llm.task_tools import register_task_tools
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


def test_relative_expression_requires_an_explicit_clock_time() -> None:
    with pytest.raises(TaskDueDateResolutionError, match="clock time"):
        resolve_task_due_at(
            due_at=datetime(2025, 8, 15, 9, tzinfo=UTC),
            due_expression=None,
            source_transcript="Remind me tomorrow.",
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
async def test_create_task_freezes_resolved_due_at_before_confirmation() -> None:
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
        source_transcript="Remind me to call Rahul tomorrow at 9 AM.",
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
    assert captured[0].due_at == datetime(2026, 9, 4, 3, 30, tzinfo=UTC)
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
    assert database.tasks[0].due_at == datetime(2026, 9, 4, 3, 30, tzinfo=UTC)
