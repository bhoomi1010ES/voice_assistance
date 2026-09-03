from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ConfigDict, Field

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
    LLMProviderInfo,
    LLMRequest,
    LLMRole,
    LLMToolCall,
)


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


def _request() -> LLMRequest:
    return LLMRequest(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        system_instructions="Answer briefly.",
        messages=(LLMMessage(role=LLMRole.USER, content="What time is it?"),),
        max_output_tokens=64,
    )


def _context(request: LLMRequest) -> ToolExecutionContext:
    return ToolExecutionContext(
        user_id=uuid.uuid4(),
        session_id=request.session_id,
        turn_id=request.turn_id,
        response_id=request.response_id,
    )


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
        yield _event(request, "tool_call_arguments_delta", 3, delta='}', tool_call=call)
        yield _event(request, "tool_call_completed", 4, tool_call=call)
        yield _event(request, "response_completed", 5, text="", finish_reason="tool_calls")


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

    assert registry.names() == ("create_task", "get_current_time")
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
async def test_tool_loop_runs_sequential_round_and_suppresses_premature_text() -> None:
    settings = _settings()
    service = FakeLLMService()
    registry = create_default_tool_registry()
    loop = LLMToolLoop(settings, service, registry)
    request = _request()

    events = [event async for event in loop.stream(request, context=_context(request))]

    assert [event.event_type for event in events] == [
        "request_started",
        "tool_call_started",
        "tool_call_arguments_delta",
        "tool_call_completed",
        "request_started",
        "text_delta",
        "response_completed",
    ]
    assert all(event.delta != "I will check that." for event in events)
    assert len(service.requests) == 2
    follow_up = service.requests[1]
    assert follow_up.messages[-2].role == LLMRole.ASSISTANT
    assert follow_up.messages[-2].tool_calls[0].tool_call_id == "call-time-1"
    assert follow_up.messages[-1].role == LLMRole.TOOL
    assert follow_up.messages[-1].tool_call_id == "call-time-1"


@pytest.mark.asyncio
async def test_tool_loop_enforces_round_bound() -> None:
    settings = _settings(llm_max_tool_rounds=0)
    loop = LLMToolLoop(settings, FakeLLMService(), create_default_tool_registry())

    request = _request()
    with pytest.raises(LLMToolLoopLimitError, match="round bound"):
        _ = [event async for event in loop.stream(request, context=_context(request))]
