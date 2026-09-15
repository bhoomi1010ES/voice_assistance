from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.api.reminders import _public_response
from app.core.clock import DeviceEpochClock, FrozenClock
from app.llm.context import classify_voice_tool_choice
from app.llm.task_tools import CreateTaskArguments
from app.llm.tool_loop import (
    InMemoryToolIdempotencyStore,
    ToolExecutionContext,
    ToolExecutor,
    create_default_tool_registry,
)
from app.llm.types import LLMNamedToolChoice, LLMToolCall
from app.services.device_time import (
    build_device_time_context,
    format_local_time,
    timezone_for_request,
)
from app.services.task_due_dates import resolve_task_due_at
from app.websocket.gateway import VoiceGateway


def _epoch(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)


def _device_context(*, epoch: str, timezone: str) -> dict[str, object]:
    return {
        "device_epoch_ms": _epoch(epoch),
        "timezone_id": timezone,
        "utc_offset": "+00:00",
        "locale": "en-US",
    }


def _resolved(*, expression: str, now: str, timezone: str) -> datetime:
    return resolve_task_due_at(
        due_at=None,
        due_expression=expression,
        source_transcript=None,
        now_utc=datetime.fromisoformat(now).replace(tzinfo=UTC),
        timezone_name=timezone,
    )


@pytest.mark.asyncio
async def test_acceptance_1_current_time_is_device_local_and_read_only() -> None:
    registry = create_default_tool_registry()
    prompt = "What time is it?"
    choice = classify_voice_tool_choice(prompt, registry.definitions())
    assert isinstance(choice, LLMNamedToolChoice)
    assert choice.function.name == "get_current_time"

    context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        clock=FrozenClock(datetime(2026, 9, 11, 7, 49, tzinfo=UTC)),
        user_timezone="Asia/Kolkata",
        device_time_context=build_device_time_context(
            _device_context(epoch="2026-09-11T07:49:00", timezone="Asia/Kolkata"),
            fallback_clock=FrozenClock(datetime(2020, 1, 1, tzinfo=UTC)),
        ),
    )
    result = await ToolExecutor(registry).execute(
        LLMToolCall(tool_call_id="time-1", name="get_current_time", arguments={}),
        context=context,
    )
    payload = json.loads(result.content)["result"]
    assert payload == {
        "local_date": "11 September 2026",
        "local_time": "1:19 PM",
        "timezone": "Asia/Kolkata",
        "utc_offset": "+05:30",
    }
    assert result.success is True
    assert "tasks" not in payload


@pytest.mark.asyncio
async def test_acceptance_2_current_date_is_device_local_and_creates_no_task() -> None:
    registry = create_default_tool_registry()
    choice = classify_voice_tool_choice("What is today's date?", registry.definitions())
    assert isinstance(choice, LLMNamedToolChoice)
    assert choice.function.name == "get_current_date"

    context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        clock=FrozenClock(datetime(2026, 9, 11, 23, 30, tzinfo=UTC)),
        user_timezone="America/New_York",
        device_time_context=build_device_time_context(
            _device_context(epoch="2026-09-11T23:30:00", timezone="America/New_York"),
            fallback_clock=FrozenClock(datetime(2020, 1, 1, tzinfo=UTC)),
        ),
    )
    result = await ToolExecutor(registry).execute(
        LLMToolCall(tool_call_id="date-1", name="get_current_date", arguments={}),
        context=context,
    )
    assert json.loads(result.content)["result"] == {
        "local_date": "11 September 2026",
        "timezone": "America/New_York",
        "utc_offset": "-04:00",
    }


class _TaskDatabase:
    def __init__(self) -> None:
        self.tasks: list[object] = []

    def add(self, task: object) -> None:
        self.tasks.append(task)

    async def flush(self) -> None:
        for task in self.tasks:
            if getattr(task, "id", None) is None:
                task.id = uuid.uuid4()


@pytest.mark.asyncio
async def test_acceptance_3_tomorrow_uses_device_date_and_executes_once_after_confirmation() -> (
    None
):
    database = _TaskDatabase()
    captured: list[CreateTaskArguments] = []
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    turn_id = uuid.uuid4()

    async def save_confirmation(_call, arguments, _tool) -> bool:
        captured.append(arguments)
        return True

    context = ToolExecutionContext(
        user_id=user_id,
        session_id=session_id,
        turn_id=turn_id,
        response_id=uuid.uuid4(),
        scopes=frozenset({"tasks:write"}),
        db=database,
        clock=FrozenClock(datetime(2026, 9, 11, 7, 30, tzinfo=UTC)),
        user_timezone="Asia/Kolkata",
        timezone_source="device",
        source_transcript="Remind me tomorrow at 9 AM.",
        confirmation_requested=save_confirmation,
    )
    executor = ToolExecutor(
        create_default_tool_registry(),
        idempotency_store=InMemoryToolIdempotencyStore(),
    )
    call = LLMToolCall(
        tool_call_id="tomorrow-1",
        name="create_task",
        arguments={"title": "Reminder", "due_at": "2030-01-01T00:00:00Z"},
    )
    pending = await executor.execute(call, context=context)
    assert pending.error_code == "llm_tool_confirmation_required"
    assert database.tasks == []
    assert captured[0].due_at == datetime(2026, 9, 12, 3, 30, tzinfo=UTC)

    approved_context = replace(
        context,
        confirmed_tool_call_ids=frozenset({call.tool_call_id}),
        source_transcript=None,
    )
    approved = await executor.execute(
        LLMToolCall(
            tool_call_id=call.tool_call_id,
            name=call.name,
            arguments=captured[0].model_dump(mode="json"),
        ),
        context=approved_context,
    )
    replay = await executor.execute(
        LLMToolCall(
            tool_call_id=call.tool_call_id,
            name=call.name,
            arguments=captured[0].model_dump(mode="json"),
        ),
        context=approved_context,
    )
    assert approved.success is True and approved.executed is True
    assert replay.success is True and replay.replayed is True
    assert len(database.tasks) == 1
    task = database.tasks[0]
    assert task.due_at == datetime(2026, 9, 12, 3, 30, tzinfo=UTC)
    assert task.local_due_at.isoformat() == "2026-09-12T09:00:00+05:30"
    assert task.timezone_source == "device"


def test_acceptance_4_weekday_uses_next_applicable_future_tuesday() -> None:
    before = _resolved(
        expression="Tuesday at 10 AM",
        now="2026-09-15T02:00:00+00:00",
        timezone="Asia/Kolkata",
    )
    after = _resolved(
        expression="Tuesday at 10 AM",
        now="2026-09-15T06:00:00+00:00",
        timezone="Asia/Kolkata",
    )
    assert before == datetime(2026, 9, 15, 4, 30, tzinfo=UTC)
    assert after == datetime(2026, 9, 22, 4, 30, tzinfo=UTC)


def test_acceptance_5_relative_duration_adds_to_the_absolute_instant() -> None:
    resolved = _resolved(
        expression="in 2 hours",
        now="2026-09-11T23:30:00+00:00",
        timezone="Europe/London",
    )
    assert resolved == datetime(2026, 9, 12, 1, 30, tzinfo=UTC)


def test_acceptance_6_explicit_new_york_timezone_overrides_device_and_uses_dst() -> None:
    timezone, source = timezone_for_request(
        "Meeting at 10 AM New York time.", device_timezone="Asia/Kolkata"
    )
    assert (timezone, source) == ("America/New_York", "explicit")
    resolved = _resolved(
        expression="Meeting at 10 AM New York time.",
        now="2026-07-01T12:00:00+00:00",
        timezone=timezone,
    )
    assert resolved == datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


def test_acceptance_7_explicit_utc_overrides_device_timezone() -> None:
    timezone, source = timezone_for_request(
        "Remind me at 5 PM UTC.", device_timezone="Asia/Kolkata"
    )
    assert (timezone, source) == ("UTC", "explicit")
    assert _resolved(
        expression="Remind me at 5 PM UTC.",
        now="2026-09-11T12:00:00+00:00",
        timezone=timezone,
    ) == datetime(2026, 9, 11, 17, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_device_epoch_wins_over_skewed_backend_clock_for_utc_and_isd() -> None:
    """Regression for a device at 16:22 IST while the host says 23:22 UTC."""

    registry = create_default_tool_registry()
    executor = ToolExecutor(registry)
    device_context = build_device_time_context(
        _device_context(epoch="2026-09-11T10:52:00", timezone="Asia/Kolkata"),
        fallback_clock=FrozenClock(datetime(2026, 9, 11, 23, 22, tzinfo=UTC)),
    )

    utc_timezone, utc_source = timezone_for_request(
        "What is current time in UTC?", device_timezone=device_context.timezone_id
    )
    utc_context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        clock=FrozenClock(datetime(2026, 9, 11, 23, 22, tzinfo=UTC)),
        user_timezone=utc_timezone,
        timezone_source=utc_source,
        device_time_context=replace(device_context, timezone_id=utc_timezone),
    )
    utc_result = await executor.execute(
        LLMToolCall(tool_call_id="skew-utc", name="get_current_time", arguments={}),
        context=utc_context,
    )
    assert json.loads(utc_result.content)["result"] == {
        "local_date": "11 September 2026",
        "local_time": "10:52 AM",
        "timezone": "UTC",
        "utc_offset": "+00:00",
    }

    ist_timezone, ist_source = timezone_for_request(
        "What is the current time in ISD?", device_timezone="America/New_York"
    )
    assert (ist_timezone, ist_source) == ("Asia/Kolkata", "explicit")
    ist_context = replace(
        utc_context,
        user_timezone=ist_timezone,
        timezone_source=ist_source,
        device_time_context=replace(device_context, timezone_id=ist_timezone),
    )
    ist_result = await executor.execute(
        LLMToolCall(tool_call_id="skew-ist", name="get_current_time", arguments={}),
        context=ist_context,
    )
    assert json.loads(ist_result.content)["result"] == {
        "local_date": "11 September 2026",
        "local_time": "4:22 PM",
        "timezone": "Asia/Kolkata",
        "utc_offset": "+05:30",
    }


@pytest.mark.parametrize("timezone", ["Asia/Kolkata", "America/New_York", "Europe/London"])
def test_device_zones_are_iana_and_offset_is_derived_from_zone_rules(timezone: str) -> None:
    context = build_device_time_context(
        _device_context(epoch="2026-11-01T06:30:00", timezone=timezone),
        fallback_clock=FrozenClock(datetime(2020, 1, 1, tzinfo=UTC)),
    )
    assert context.timezone_id == timezone
    assert format_local_time(context)["timezone"] == timezone


def test_invalid_or_missing_device_context_uses_backend_utc_fallback() -> None:
    fallback = FrozenClock(datetime(2026, 9, 11, 12, tzinfo=UTC))
    context = build_device_time_context(
        {"device_epoch_ms": "bad", "timezone_id": "Not/AZone"},
        fallback_clock=fallback,
    )
    assert context.source == "backend_fallback"
    assert context.timezone_id == "UTC"
    assert context.instant_utc == fallback.now_utc()


def test_refreshing_two_device_contexts_does_not_leak_timezone_or_epoch() -> None:
    fallback = FrozenClock(datetime(2020, 1, 1, tzinfo=UTC))
    first = build_device_time_context(
        _device_context(epoch="2026-09-11T12:00:00", timezone="Asia/Kolkata"),
        fallback_clock=fallback,
    )
    second = build_device_time_context(
        _device_context(epoch="2026-09-11T12:00:00", timezone="America/New_York"),
        fallback_clock=fallback,
    )
    assert first.timezone_id == "Asia/Kolkata"
    assert second.timezone_id == "America/New_York"
    assert first.device_epoch_ms == second.device_epoch_ms


def test_gateway_formats_request_timezone_and_keeps_lifecycle_on_server_clock() -> None:
    """Regression for voice_turn_completion_failed after transcript delivery."""

    backend_now = datetime(2026, 9, 11, 23, 22, tzinfo=UTC)
    device_context = build_device_time_context(
        _device_context(epoch="2026-09-11T10:52:00", timezone="Asia/Kolkata"),
        fallback_clock=FrozenClock(backend_now),
    )
    gateway = object.__new__(VoiceGateway)
    gateway.clock = FrozenClock(backend_now)
    gateway._device_time_context = device_context
    gateway._device_clock = DeviceEpochClock(device_context.device_epoch_ms)

    explicit_context = gateway._time_context_for_timezone("America/New_York")

    assert explicit_context is not None
    assert explicit_context.timezone_id == "America/New_York"
    assert explicit_context.utc_offset == "-04:00"
    assert gateway._application_clock().now_utc() == backend_now
    assert gateway._trusted_user_clock().now_utc() == device_context.instant_utc


def test_reminder_api_response_includes_normalized_time_metadata() -> None:
    trigger_at = datetime(2026, 9, 12, 3, 30, tzinfo=UTC)
    local_trigger_at = trigger_at.astimezone(ZoneInfo("Asia/Kolkata"))
    reminder = SimpleNamespace(
        id=uuid.uuid4(),
        task_id=None,
        title="Device-local reminder",
        body=None,
        trigger_at=trigger_at,
        local_trigger_at=local_trigger_at,
        timezone="Asia/Kolkata",
        timezone_source="device",
        recurrence_rule=None,
        status="scheduled",
        delivery_channel="push",
        created_at=trigger_at,
        updated_at=trigger_at,
        sent_at=None,
    )

    response = _public_response(reminder)

    assert response.trigger_at == trigger_at
    assert response.local_trigger_at == local_trigger_at
    assert response.timezone == "Asia/Kolkata"
    assert response.timezone_source == "device"


def test_reminder_api_response_accepts_existing_row_without_local_timestamp() -> None:
    trigger_at = datetime(2026, 9, 12, 17, 0, tzinfo=UTC)
    reminder = SimpleNamespace(
        id=uuid.uuid4(),
        task_id=None,
        title="Existing reminder",
        body=None,
        trigger_at=trigger_at,
        local_trigger_at=None,
        timezone="UTC",
        timezone_source="device",
        recurrence_rule=None,
        status="processing",
        delivery_channel="push",
        created_at=trigger_at,
        updated_at=trigger_at,
        sent_at=None,
    )

    response = _public_response(reminder)

    assert response.local_trigger_at is None
    assert response.timezone_source == "device"
    assert response.status == "scheduled"
