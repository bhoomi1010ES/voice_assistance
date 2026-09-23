from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.llm.errors import LLMToolLoopLimitError
from app.llm.task_tools import CreateTaskArguments, register_task_tools
from app.llm.tool_loop import (
    InMemoryToolIdempotencyStore,
    LLMToolLoop,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
    create_default_tool_registry,
)
from app.llm.types import (
    LLMCapabilities,
    LLMEvent,
    LLMMessage,
    LLMNamedToolChoice,
    LLMProviderInfo,
    LLMRequest,
    LLMRole,
    LLMToolCall,
)
from app.services.device_time import DeviceTimeContext


class LookupArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str = Field(min_length=1, max_length=64)


class CreateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=100)


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "llm_provider": "nvidia",
        "llm_base_url": "https://integrate.api.nvidia.com/v1",
        "llm_api_key": "test-placeholder-key",
        "llm_model": "nvidia/nemotron-3-super-120b-a12b",
    }
    values.update(overrides)
    return Settings(**values)


def _request(
    *,
    user_text: str = "What time is it?",
    allowed_tools=(),
    tool_choice="auto",
) -> LLMRequest:
    return LLMRequest(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        system_instructions="Answer briefly.",
        messages=(LLMMessage(role=LLMRole.USER, content=user_text),),
        allowed_tools=allowed_tools,
        tool_choice=tool_choice,
        max_output_tokens=64,
    )


def _context(request: LLMRequest, **overrides) -> ToolExecutionContext:
    values = dict(
        user_id=uuid.uuid4(),
        session_id=request.session_id,
        turn_id=request.turn_id,
        response_id=request.response_id,
    )
    values.update(overrides)
    return ToolExecutionContext(**values)


def _event(request: LLMRequest, event_type: str, sequence: int, **values) -> LLMEvent:
    return LLMEvent(
        event_type=event_type,
        session_id=request.session_id,
        turn_id=request.turn_id,
        response_id=request.response_id,
        provider="nvidia",
        configured_model="nvidia/nemotron-3-super-120b-a12b",
        monotonic_seconds=time.monotonic(),
        sequence=sequence,
        **values,
    )


class FakeLLMService:
    provider_info = LLMProviderInfo(
        provider="nvidia",
        api_family="openai_chat_completions",
        host="https://integrate.api.nvidia.com",
        configured_model="nvidia/nemotron-3-super-120b-a12b",
        capabilities=LLMCapabilities(streaming=True, text_generation=True, tool_calling=True),
    )

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def stream(self, request: LLMRequest):
        self.requests.append(request)
        if any(message.role == LLMRole.TOOL for message in request.messages):
            yield _event(request, "request_started", 0)
            yield _event(request, "text_delta", 1, delta="It is available now.")
            yield _event(
                request,
                "response_completed",
                2,
                text="It is available now.",
                finish_reason="stop",
            )
            return

        call = LLMToolCall(
            tool_call_id="call-time-1",
            name="get_current_time",
            arguments_json="{}",
            arguments={},
        )
        yield _event(request, "request_started", 0)
        yield _event(request, "text_delta", 1, delta="I will check that.")
        yield _event(request, "tool_call_started", 2, tool_call=call)
        yield _event(request, "tool_call_arguments_delta", 3, delta="}", tool_call=call)
        yield _event(
            request,
            "tool_call_completed",
            4,
            tool_call=call,
            provider_items=(
                {
                    "type": "reasoning",
                    "id": "rs-time",
                    "encrypted_content": "opaque",
                },
                {
                    "type": "function_call",
                    "id": "fc-time",
                    "call_id": "call-time-1",
                    "name": "get_current_time",
                    "arguments": "{}",
                },
            ),
        )
        yield _event(request, "response_completed", 5, text="", finish_reason="tool_calls")


class FakeConfirmationLLMService:
    provider_info = FakeLLMService.provider_info

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def stream(self, request: LLMRequest):
        self.requests.append(request)
        call = LLMToolCall(
            tool_call_id="call-confirmation-1",
            name="create_task",
            arguments_json='{"title":"Protected task"}',
            arguments={"title": "Protected task"},
        )
        yield _event(request, "request_started", 0)
        yield _event(request, "tool_call_started", 1, tool_call=call)
        yield _event(request, "tool_call_arguments_delta", 2, delta="}", tool_call=call)
        yield _event(request, "tool_call_completed", 3, tool_call=call)
        yield _event(request, "response_completed", 4, text="", finish_reason="tool_calls")


class FakeClockOnlyLLMService:
    provider_info = FakeLLMService.provider_info

    def __init__(self, tool_calls: tuple[LLMToolCall, ...]) -> None:
        self.tool_calls = tool_calls
        self.requests: list[LLMRequest] = []

    async def stream(self, request: LLMRequest):
        if any(message.role == LLMRole.TOOL for message in request.messages):
            raise AssertionError("A successful clock-only round must not call the provider again.")
        self.requests.append(request)
        yield _event(request, "request_started", 0)
        yield _event(request, "text_delta", 1, delta="I will check that.")
        sequence = 2
        for call in self.tool_calls:
            yield _event(request, "tool_call_started", sequence, tool_call=call)
            sequence += 1
            yield _event(
                request,
                "tool_call_arguments_delta",
                sequence,
                delta=call.arguments_json,
                tool_call=call,
            )
            sequence += 1
            yield _event(request, "tool_call_completed", sequence, tool_call=call)
            sequence += 1
        yield _event(request, "response_completed", sequence, text="", finish_reason="tool_calls")


class FakeFollowUpLLMService:
    provider_info = FakeLLMService.provider_info

    def __init__(self, tool_call: LLMToolCall, final_text: str) -> None:
        self.tool_call = tool_call
        self.final_text = final_text
        self.requests: list[LLMRequest] = []

    async def stream(self, request: LLMRequest):
        self.requests.append(request)
        if any(message.role == LLMRole.TOOL for message in request.messages):
            assert request.tool_choice == "auto"
            yield _event(request, "request_started", 0)
            yield _event(request, "text_delta", 1, delta=self.final_text)
            yield _event(
                request,
                "response_completed",
                2,
                text=self.final_text,
                finish_reason="stop",
            )
            return

        assert isinstance(request.tool_choice, LLMNamedToolChoice)
        assert request.tool_choice.function.name == self.tool_call.name
        yield _event(request, "request_started", 0)
        yield _event(request, "tool_call_started", 1, tool_call=self.tool_call)
        yield _event(
            request,
            "tool_call_arguments_delta",
            2,
            delta=self.tool_call.arguments_json,
            tool_call=self.tool_call,
        )
        yield _event(
            request,
            "tool_call_completed",
            3,
            tool_call=self.tool_call,
            provider_items=(
                {"type": "reasoning", "id": "rs-follow-up", "encrypted_content": "opaque"},
                {
                    "type": "function_call",
                    "id": "fc-follow-up",
                    "call_id": self.tool_call.tool_call_id,
                    "name": self.tool_call.name,
                    "arguments": self.tool_call.arguments_json,
                },
            ),
        )
        yield _event(request, "response_completed", 4, text="", finish_reason="tool_calls")


class FakeDatabase:
    def __init__(self) -> None:
        self.tasks = []

    def add(self, value) -> None:
        self.tasks.append(value)

    async def flush(self) -> None:
        for task in self.tasks:
            if task.id is None:
                task.id = uuid.uuid4()


@pytest.mark.asyncio
async def test_default_registry_is_server_owned_and_schema_backed() -> None:
    registry = create_default_tool_registry()

    assert {
        "create_task",
        "update_task",
        "complete_task",
        "list_tasks",
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "list_reminders",
        "get_current_time",
        "get_current_date",
    }.issubset(registry.names())
    definition = next(
        definition for definition in registry.definitions() if definition.name == "get_current_time"
    )
    assert definition.name == "get_current_time"
    assert definition.input_schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_executor_rejects_invalid_and_unauthorized_calls_before_handler() -> None:
    registry = ToolRegistry()
    invoked = 0

    async def handler(_context, _arguments):
        nonlocal invoked
        invoked += 1
        return {"value": "should not happen"}

    registry.register(
        name="lookup_weather",
        description="Look up weather.",
        arguments_model=LookupArguments,
        handler=handler,
        required_scopes=frozenset({"weather:read"}),
    )
    executor = ToolExecutor(registry)
    request = _request()
    context = _context(request)

    invalid = await executor.execute(
        LLMToolCall(
            tool_call_id="call-invalid",
            name="lookup_weather",
            arguments={"unexpected": "value"},
        ),
        context=context,
    )
    unauthorized = await executor.execute(
        LLMToolCall(
            tool_call_id="call-unauthorized",
            name="lookup_weather",
            arguments={"city": "Mumbai"},
        ),
        context=context,
    )

    assert invalid.success is False
    assert invalid.error_code == "llm_tool_invalid_arguments"
    assert unauthorized.success is False
    assert unauthorized.error_code == "llm_tool_not_authorized"
    assert invoked == 0


@pytest.mark.asyncio
async def test_create_task_uses_authenticated_owner_and_rejects_privileged_fields() -> None:
    registry = ToolRegistry()
    register_task_tools(registry)
    database = FakeDatabase()
    executor = ToolExecutor(registry, idempotency_store=InMemoryToolIdempotencyStore())
    request = _request()
    context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=request.session_id,
        turn_id=request.turn_id,
        response_id=request.response_id,
        scopes=frozenset({"tasks:write"}),
        confirmed_tool_call_ids=frozenset({"call-task-1"}),
        db=database,
        clock=FrozenClock(datetime(2026, 9, 3, 18, 0, tzinfo=UTC)),
    )

    result = await executor.execute(
        LLMToolCall(
            tool_call_id="call-task-1",
            name="create_task",
            arguments={
                "title": "Call Rahul",
                "due_at": "2026-09-04T09:00:00+00:00",
                "notes": "Use the phone.",
            },
        ),
        context=context,
    )
    privileged = await executor.execute(
        LLMToolCall(
            tool_call_id="call-task-privileged",
            name="create_task",
            arguments={"title": "No", "owner_id": str(uuid.uuid4()), "admin": True},
        ),
        context=context,
    )

    assert result.success is True
    assert len(database.tasks) == 1
    assert database.tasks[0].user_id == context.user_id
    assert database.tasks[0].title == "Call Rahul"
    assert privileged.success is False
    assert privileged.error_code == "llm_tool_invalid_arguments"
    assert len(database.tasks) == 1


@pytest.mark.asyncio
async def test_create_task_confirmation_denial_expiry_and_cancellation_do_not_mutate() -> None:
    registry = ToolRegistry()
    register_task_tools(registry)
    database = FakeDatabase()
    executor = ToolExecutor(registry, idempotency_store=InMemoryToolIdempotencyStore())
    request = _request()
    call = LLMToolCall(
        tool_call_id="call-task-2",
        name="create_task",
        arguments={"title": "Protected task"},
    )
    base = dict(
        user_id=uuid.uuid4(),
        session_id=request.session_id,
        turn_id=request.turn_id,
        response_id=request.response_id,
        scopes=frozenset({"tasks:write"}),
        db=database,
    )

    denied = await executor.execute(call, context=ToolExecutionContext(**base))
    expired = await executor.execute(
        call,
        context=ToolExecutionContext(
            **base,
            confirmed_tool_call_ids=frozenset({call.tool_call_id}),
            confirmation_expires_at_monotonic=time.monotonic() - 1,
        ),
    )
    cancelled = await executor.execute(
        call,
        context=ToolExecutionContext(
            **base,
            confirmed_tool_call_ids=frozenset({call.tool_call_id}),
            cancellation_check=lambda: True,
        ),
    )

    assert denied.error_code == "llm_tool_confirmation_required"
    assert expired.error_code == "llm_tool_confirmation_expired"
    assert cancelled.error_code == "llm_cancelled"
    assert database.tasks == []


@pytest.mark.asyncio
async def test_create_task_idempotency_allows_replay_but_not_different_call() -> None:
    registry = ToolRegistry()
    register_task_tools(registry)
    database = FakeDatabase()
    executor = ToolExecutor(registry, idempotency_store=InMemoryToolIdempotencyStore())
    request = _request()
    user_id = uuid.uuid4()
    context = ToolExecutionContext(
        user_id=user_id,
        session_id=request.session_id,
        turn_id=request.turn_id,
        response_id=request.response_id,
        scopes=frozenset({"tasks:write"}),
        confirmed_tool_call_ids=frozenset({"call-a", "call-b"}),
        db=database,
    )

    first = await executor.execute(
        LLMToolCall(tool_call_id="call-a", name="create_task", arguments={"title": "One"}),
        context=context,
    )
    replay = await executor.execute(
        LLMToolCall(tool_call_id="call-a", name="create_task", arguments={"title": "One"}),
        context=context,
    )
    second_context = ToolExecutionContext(
        user_id=user_id,
        session_id=request.session_id,
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        scopes=frozenset({"tasks:write"}),
        confirmed_tool_call_ids=frozenset({"call-b"}),
        db=database,
    )
    second = await executor.execute(
        LLMToolCall(tool_call_id="call-b", name="create_task", arguments={"title": "Two"}),
        context=second_context,
    )

    assert first.executed is True
    assert replay.replayed is True
    assert second.executed is True
    assert len(database.tasks) == 2


def test_create_task_arguments_are_strict_and_typed() -> None:
    args = CreateTaskArguments(title="  Call Rahul  ", due_at=datetime.now(UTC))

    assert args.title == "Call Rahul"
    with pytest.raises(ValueError):
        CreateTaskArguments(title=123)
    with pytest.raises(ValueError):
        CreateTaskArguments(title="Valid", owner_id="other")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": "   "},
        {"title": "x" * 256},
        {"title": "Valid", "due_at": {"broken": True}},
        {"title": "Valid", "notes": "x" * 100_001},
    ],
)
def test_create_task_structured_output_rejects_invalid_shapes(payload) -> None:
    with pytest.raises(ValueError):
        CreateTaskArguments.model_validate(payload)


@pytest.mark.asyncio
async def test_mutating_tool_uses_scoped_idempotency_key() -> None:
    registry = ToolRegistry()
    invoked = 0

    async def handler(_context, arguments):
        nonlocal invoked
        invoked += 1
        return {"title": arguments.title}

    registry.register(
        name="create_task",
        description="Create a task.",
        arguments_model=CreateArguments,
        handler=handler,
        required_scopes=frozenset({"tasks:write"}),
        read_only=False,
        requires_confirmation=True,
    )
    store = InMemoryToolIdempotencyStore()
    executor = ToolExecutor(registry, idempotency_store=store)
    request = _request()
    context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=request.session_id,
        turn_id=request.turn_id,
        response_id=request.response_id,
        scopes=frozenset({"tasks:write"}),
        confirmed_tool_call_ids=frozenset({"call-task-1"}),
    )
    call = LLMToolCall(
        tool_call_id="call-task-1",
        name="create_task",
        arguments={"title": "Buy milk"},
    )

    first = await executor.execute(call, context=context)
    second = await executor.execute(call, context=context)

    assert first.success is True
    assert first.executed is True
    assert second.success is True
    assert second.replayed is True
    assert second.executed is False
    assert first.content == second.content
    assert invoked == 1


@pytest.mark.asyncio
async def test_forced_clock_tool_answer_is_terminal_and_suppresses_premature_text() -> None:
    settings = _settings()
    registry = create_default_tool_registry()
    call = LLMToolCall(
        tool_call_id="call-time-1",
        name="get_current_time",
        arguments_json="{}",
        arguments={},
    )
    service = FakeClockOnlyLLMService((call,))
    loop = LLMToolLoop(settings, service, registry)
    request = _request(
        allowed_tools=registry.definitions(),
        tool_choice=LLMNamedToolChoice(function={"name": "get_current_time"}),
    )
    context = _context(
        request,
        source_transcript="What time is it?",
        device_time_context=DeviceTimeContext(
            device_epoch_ms=int(datetime(2026, 9, 23, 12, 34, tzinfo=UTC).timestamp() * 1000),
            timezone_id="UTC",
            utc_offset="+00:00",
            locale="en-US",
        ),
    )

    events = [event async for event in loop.stream(request, context=context)]

    assert [event.event_type for event in events] == [
        "request_started",
        "tool_call_started",
        "tool_call_arguments_delta",
        "tool_call_completed",
        "tool_execution_completed",
        "text_delta",
        "response_completed",
    ]
    assert all(event.delta != "I will check that." for event in events)
    assert len(service.requests) == 1
    assert service.requests[0].tool_choice == request.tool_choice
    assert events[-2].delta == "The current time is 12:34 PM."
    assert events[-1].text == events[-2].delta
    assert events[-1].finish_reason == "tool_result"
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)
    assert all(event.session_id == request.session_id for event in events)
    assert all(event.turn_id == request.turn_id for event in events)
    assert all(event.response_id == request.response_id for event in events)


@pytest.mark.asyncio
async def test_clock_tool_speaks_date_and_time_once_when_both_tools_return() -> None:
    registry = create_default_tool_registry()
    time_call = LLMToolCall(
        tool_call_id="call-time-1",
        name="get_current_time",
        arguments_json="{}",
        arguments={},
    )
    date_call = LLMToolCall(
        tool_call_id="call-date-1",
        name="get_current_date",
        arguments_json="{}",
        arguments={},
    )
    service = FakeClockOnlyLLMService((time_call, date_call))
    loop = LLMToolLoop(_settings(), service, registry)
    request = _request(
        user_text="What's the date and time?",
        allowed_tools=registry.definitions(),
        tool_choice=LLMNamedToolChoice(function={"name": "get_current_time"}),
    )
    context = _context(
        request,
        source_transcript="What's the date and time?",
        device_time_context=DeviceTimeContext(
            device_epoch_ms=int(datetime(2026, 9, 23, 12, 34, tzinfo=UTC).timestamp() * 1000),
            timezone_id="UTC",
            utc_offset="+00:00",
            locale="en-US",
        ),
    )

    events = [event async for event in loop.stream(request, context=context)]
    answer = events[-2].delta

    assert answer == "The current time is 12:34 PM and today's date is 23 September 2026."
    assert answer.count("12:34 PM") == 1
    assert answer.count("23 September 2026") == 1
    assert len(service.requests) == 1


@pytest.mark.asyncio
async def test_forced_date_tool_returns_one_deterministic_spoken_answer() -> None:
    registry = create_default_tool_registry()
    call = LLMToolCall(
        tool_call_id="call-date-1",
        name="get_current_date",
        arguments_json="{}",
        arguments={},
    )
    service = FakeClockOnlyLLMService((call,))
    loop = LLMToolLoop(_settings(), service, registry)
    request = _request(
        user_text="What's today's date?",
        allowed_tools=registry.definitions(),
        tool_choice=LLMNamedToolChoice(function={"name": "get_current_date"}),
    )
    context = _context(
        request,
        source_transcript="What's today's date?",
        device_time_context=DeviceTimeContext(
            device_epoch_ms=int(datetime(2026, 9, 23, 12, 34, tzinfo=UTC).timestamp() * 1000),
            timezone_id="UTC",
            utc_offset="+00:00",
            locale="en-US",
        ),
    )

    events = [event async for event in loop.stream(request, context=context)]

    assert events[-2].delta == "Today's date is 23 September 2026."
    assert events[-1].text == events[-2].delta
    assert events[-1].finish_reason == "tool_result"
    assert len(service.requests) == 1


@pytest.mark.asyncio
async def test_explicit_timezone_is_included_in_the_spoken_clock_answer() -> None:
    registry = create_default_tool_registry()
    call = LLMToolCall(
        tool_call_id="call-time-1",
        name="get_current_time",
        arguments_json="{}",
        arguments={},
    )
    service = FakeClockOnlyLLMService((call,))
    loop = LLMToolLoop(_settings(), service, registry)
    request = _request(
        user_text="What time is it in Los Angeles?",
        allowed_tools=registry.definitions(),
        tool_choice=LLMNamedToolChoice(function={"name": "get_current_time"}),
    )
    context = _context(
        request,
        timezone_source="explicit",
        source_transcript="What time is it in Los Angeles?",
        device_time_context=DeviceTimeContext(
            device_epoch_ms=int(datetime(2026, 9, 23, 12, 34, tzinfo=UTC).timestamp() * 1000),
            timezone_id="America/Los_Angeles",
            utc_offset="-07:00",
            locale="en-US",
        ),
    )

    events = [event async for event in loop.stream(request, context=context)]

    assert events[-2].delta == "The current time in America/Los_Angeles is 5:34 AM."


@pytest.mark.asyncio
async def test_forced_non_clock_tool_continuation_uses_auto_choice() -> None:
    registry = ToolRegistry()

    async def handler(_context, arguments):
        return {"city": arguments.city, "temperature": "18 C"}

    registry.register(
        name="lookup_weather",
        description="Look up weather.",
        arguments_model=LookupArguments,
        handler=handler,
    )
    call = LLMToolCall(
        tool_call_id="weather-1",
        name="lookup_weather",
        arguments_json='{"city":"Paris"}',
        arguments={"city": "Paris"},
    )
    service = FakeFollowUpLLMService(call, "The lookup returned a result.")
    loop = LLMToolLoop(_settings(), service, registry)
    request = _request(
        user_text="Check the weather in Paris.",
        allowed_tools=registry.definitions(),
        tool_choice=LLMNamedToolChoice(function={"name": "lookup_weather"}),
    )

    events = [event async for event in loop.stream(request, context=_context(request))]

    assert len(service.requests) == 2
    assert isinstance(service.requests[0].tool_choice, LLMNamedToolChoice)
    assert service.requests[1].tool_choice == "auto"
    follow_up = service.requests[1]
    assert follow_up.messages[-2].role == LLMRole.ASSISTANT
    assert follow_up.messages[-2].provider_items[0]["id"] == "rs-follow-up"
    assert follow_up.messages[-2].provider_items[1]["id"] == "fc-follow-up"
    assert follow_up.messages[-1].role == LLMRole.TOOL
    assert follow_up.messages[-1].tool_call_id == call.tool_call_id
    assert events[-2].delta == "The lookup returned a result."


@pytest.mark.asyncio
@pytest.mark.parametrize("result_mode", ["failed", "malformed"])
async def test_failed_or_malformed_clock_result_uses_auto_without_invented_time(
    result_mode: str,
) -> None:
    class EmptyArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    registry = ToolRegistry()

    async def handler(_context, _arguments):
        if result_mode == "failed":
            raise RuntimeError("clock failure")
        return {
            "local_time": "",
            "local_date": "23 September 2026",
            "timezone": "UTC",
            "utc_offset": "+00:00",
        }

    registry.register(
        name="get_current_time",
        description="Return the current local time.",
        arguments_model=EmptyArguments,
        handler=handler,
    )
    call = LLMToolCall(
        tool_call_id="clock-1",
        name="get_current_time",
        arguments_json="{}",
        arguments={},
    )
    service = FakeFollowUpLLMService(call, "I could not retrieve the current time.")
    loop = LLMToolLoop(_settings(), service, registry)
    request = _request(
        allowed_tools=registry.definitions(),
        tool_choice=LLMNamedToolChoice(function={"name": "get_current_time"}),
    )

    events = [event async for event in loop.stream(request, context=_context(request))]

    assert len(service.requests) == 2
    assert service.requests[1].tool_choice == "auto"
    expected_tool_event = (
        "tool_execution_failed" if result_mode == "failed" else "tool_execution_completed"
    )
    assert any(event.event_type == expected_tool_event for event in events)
    assert events[-2].delta == "I could not retrieve the current time."
    assert all(event.finish_reason != "tool_result" for event in events)


@pytest.mark.asyncio
async def test_tool_loop_stops_at_confirmation_without_provider_continuation() -> None:
    settings = _settings()
    service = FakeConfirmationLLMService()
    registry = ToolRegistry()
    invoked = 0

    async def handler(_context, _arguments):
        nonlocal invoked
        invoked += 1
        return {"ok": True}

    registry.register(
        name="create_task",
        description="Create a task after confirmation.",
        arguments_model=CreateArguments,
        handler=handler,
        required_scopes=frozenset({"tasks:write"}),
        read_only=False,
        requires_confirmation=True,
    )

    async def save_confirmation(_call, _arguments, _tool) -> bool:
        return True

    request = _request()
    context = ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=request.session_id,
        turn_id=request.turn_id,
        response_id=request.response_id,
        scopes=frozenset({"tasks:write"}),
        confirmation_requested=save_confirmation,
    )
    loop = LLMToolLoop(
        settings,
        service,
        registry,
        idempotency_store=InMemoryToolIdempotencyStore(),
    )

    events = [event async for event in loop.stream(request, context=context)]

    assert [event.event_type for event in events] == [
        "request_started",
        "tool_call_started",
        "tool_call_arguments_delta",
        "tool_call_completed",
        "confirmation_required",
    ]
    assert len(service.requests) == 1
    assert invoked == 0


@pytest.mark.asyncio
async def test_tool_loop_enforces_round_bound() -> None:
    settings = _settings(llm_max_tool_rounds=0)
    loop = LLMToolLoop(settings, FakeLLMService(), create_default_tool_registry())

    request = _request()
    with pytest.raises(LLMToolLoopLimitError, match="round bound"):
        _ = [event async for event in loop.stream(request, context=_context(request))]
