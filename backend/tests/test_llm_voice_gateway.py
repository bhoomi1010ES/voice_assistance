from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.clock import DeviceEpochClock, FrozenClock
from app.core.config import Settings
from app.llm.tool_loop import (
    EmptyToolArguments,
    ToolExecutor,
    ToolRegistry,
    create_default_tool_registry,
)
from app.llm.types import (
    LLMCapabilities,
    LLMEvent,
    LLMMessage,
    LLMProviderInfo,
    LLMRole,
    LLMToolCall,
    LLMUsage,
)
from app.services.device_time import build_device_time_context
from app.services.latency_trace import LatencyTracer
from app.websocket.cancellation import CancellationGuard
from app.websocket.gateway import VoiceGateway


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        llm_provider="nvidia",
        llm_base_url="https://integrate.api.nvidia.com/v1",
        llm_api_key="test-placeholder-key",
        llm_model="nvidia/nemotron-3-super-120b-a12b",
    )


def _info() -> LLMProviderInfo:
    return LLMProviderInfo(
        provider="nvidia",
        api_family="openai_chat_completions",
        host="https://integrate.api.nvidia.com",
        configured_model="nvidia/nemotron-3-super-120b-a12b",
        capabilities=LLMCapabilities(
            streaming=True,
            text_generation=True,
            tool_calling=True,
            usage_reporting=True,
            cancellation=True,
        ),
    )


class FakePersistence:
    def __init__(self) -> None:
        self.metadata: list[dict] = []

    async def merge_turn_metadata(self, db, principal, *, turn_id, metadata):
        self.metadata.append(metadata)
        return None


class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeDatabase:
    def __init__(self) -> None:
        self.commits = 0

    def begin_nested(self):
        return _NestedTransaction()

    async def scalar(self, _statement):
        return SimpleNamespace(memory_enabled=False)

    async def commit(self) -> None:
        self.commits += 1


class FakeLLMService:
    enabled = True

    def __init__(self, event_factory) -> None:
        self.provider_info = _info()
        self.event_factory = event_factory
        self.cancelled: list[uuid.UUID] = []

    async def stream(self, request):
        for event in self.event_factory(request):
            yield event

    async def cancel(self, response_id: uuid.UUID) -> bool:
        self.cancelled.append(response_id)
        return True


def _event(request, event_type, sequence, **values) -> LLMEvent:
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


def _gateway(event_factory):
    gateway = object.__new__(VoiceGateway)
    gateway.settings = _settings()
    gateway.llm_service = FakeLLMService(event_factory)
    gateway.cancel_guard = CancellationGuard()
    gateway.persistence = FakePersistence()
    gateway.db = FakeDatabase()
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4())
    gateway._session_id = None
    gateway.state = SimpleNamespace(session_id=None)
    outbound: list[dict] = []

    async def send(event: dict) -> None:
        outbound.append(event)

    gateway._send = send
    return gateway, outbound


@pytest.mark.asyncio
async def test_gateway_does_not_emit_wait_phrase_for_empty_transcript() -> None:
    gateway, outbound = _gateway(lambda _request: ())

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        transcript="   ",
    )

    assert result == {"status": "failed", "error": "empty_transcript"}
    assert outbound == []


@pytest.mark.asyncio
async def test_gateway_traces_provider_usage_without_prompt_content(tmp_path) -> None:
    gateway, _outbound = _gateway(
        lambda request: (
            _event(request, "request_started", 0, attempt=1),
            _event(
                request,
                "usage",
                1,
                attempt=1,
                usage=LLMUsage(input_tokens=123, output_tokens=19, total_tokens=142),
            ),
            _event(
                request,
                "response_completed",
                2,
                attempt=1,
                text="A harmless answer.",
                finish_reason="stop",
                returned_model="nvidia/nemotron-3-super-120b-a12b",
            ),
        )
    )
    trace_path = tmp_path / "llm-usage.jsonl"
    gateway.latency_tracer = LatencyTracer(trace_path)
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Do not include this prompt in tracing.",
    )

    assert result["status"] == "completed"
    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    request = next(item for item in records if item["event"] == "gateway_llm_request_observed")
    usage = next(item for item in records if item["event"] == "llm_provider_usage")
    assert request["metadata"]["provider"] == "nvidia"
    assert request["metadata"]["configured_model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert request["metadata"]["attempt"] == 1
    assert usage["metadata"]["input_tokens"] == 123
    assert usage["metadata"]["output_tokens"] == 19
    assert usage["metadata"]["total_tokens"] == 142
    assert "Do not include this prompt" not in trace_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_live_memory_exclusion_lookup_refreshes_cached_gateway_metadata() -> None:
    class ExclusionDatabase:
        def begin_nested(self):
            return _NestedTransaction()

        async def execute(self, _statement):
            return SimpleNamespace(first=lambda: ({"memory_excluded": True},))

    gateway = object.__new__(VoiceGateway)
    gateway.db = ExclusionDatabase()
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4())
    gateway._session_id = uuid.uuid4()
    gateway._session_client_metadata = {"memory_excluded": False}
    gateway.voice_session = SimpleNamespace(
        client_metadata={"memory_excluded": False, "timezone": "UTC"}
    )

    assert await gateway._memory_excluded_for_session() is True
    assert gateway._session_client_metadata == {"memory_excluded": True}
    assert gateway.voice_session.client_metadata == {"memory_excluded": True, "timezone": "UTC"}


def test_gateway_session_identity_does_not_read_expired_orm_state() -> None:
    gateway = object.__new__(VoiceGateway)
    session_id = uuid.uuid4()

    class ExpiredVoiceSession:
        @property
        def id(self):
            raise AssertionError("expired ORM identity was accessed")

    gateway._session_id = None
    gateway.state = SimpleNamespace(session_id=session_id)
    gateway.voice_session = ExpiredVoiceSession()

    assert gateway._active_session_id() == session_id


@pytest.mark.asyncio
async def test_gateway_streams_correlated_text_and_persists_safe_metadata() -> None:
    def events(request):
        yield _event(request, "request_started", 0)
        yield _event(request, "text_delta", 1, delta="Hello ")
        yield _event(request, "text_delta", 2, delta="there")
        yield _event(
            request,
            "usage",
            3,
            usage=LLMUsage(input_tokens=9, output_tokens=2, total_tokens=11),
        )
        yield _event(
            request,
            "response_completed",
            4,
            text="Hello there",
            provider_request_id="request-1",
            returned_model="nvidia/nemotron-3-super-120b-a12b",
            finish_reason="stop",
        )

    gateway, outbound = _gateway(events)
    session_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        transcript="Hello assistant",
    )

    assert result["status"] == "completed"
    assert [event["type"] for event in outbound] == [
        "assistant.thinking",
        "assistant.text.delta",
        "assistant.text.delta",
        "assistant.text.final",
    ]
    assert outbound[0]["text"] in {
        "We're reviewing your query.",
        "Give me a moment.",
    }
    assert all(event["session_id"] == str(session_id) for event in outbound)
    assert all(event["turn_id"] == str(turn_id) for event in outbound)
    assert all(event["response_id"] == str(response_id) for event in outbound)
    persisted = gateway.persistence.metadata[0]["llm"]
    assert persisted["status"] == "completed"
    assert persisted["response_text"] == "Hello there"
    assert persisted["usage"] == {"input_tokens": 9, "output_tokens": 2, "total_tokens": 11}
    assert "test-placeholder-key" not in repr(persisted)
    timing = gateway._turn_timings[turn_id].points
    assert timing["llm_started_at"].monotonic <= timing["llm_first_token_at"].monotonic
    assert timing["llm_first_token_at"].monotonic <= timing["llm_completed_at"].monotonic
    assert timing["llm_first_token_at"].wall.tzinfo is not None


@pytest.mark.asyncio
async def test_gateway_with_device_time_context_reaches_answer_stream() -> None:
    """A device timezone must not fail the turn between STT and the LLM answer."""

    def events(request):
        yield _event(request, "request_started", 0)
        yield _event(request, "text_delta", 1, delta="It is 4:22 PM.")
        yield _event(
            request,
            "response_completed",
            2,
            text="It is 4:22 PM.",
            finish_reason="stop",
        )

    gateway, outbound = _gateway(events)
    backend_now = datetime(2026, 9, 11, 23, 22, tzinfo=UTC)
    device_epoch_ms = int(datetime(2026, 9, 11, 10, 52, tzinfo=UTC).timestamp() * 1000)
    gateway.clock = FrozenClock(backend_now)
    gateway._device_time_context = build_device_time_context(
        {
            "device_epoch_ms": device_epoch_ms,
            "timezone_id": "Asia/Kolkata",
            "utc_offset": "+05:30",
            "locale": "en-IN",
        },
        fallback_clock=gateway.clock,
    )
    gateway._device_clock = DeviceEpochClock(gateway._device_time_context.device_epoch_ms)
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What is the current time?",
    )

    assert result["status"] == "completed"
    assert [event["type"] for event in outbound] == [
        "assistant.thinking",
        "assistant.text.delta",
        "assistant.text.final",
    ]


@pytest.mark.asyncio
async def test_gateway_emits_ordered_server_owned_tool_lifecycle() -> None:
    gateway, outbound = _gateway(lambda _request: ())
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4())
    gateway.voice_session = None
    gateway.tool_registry = SimpleNamespace(definitions=lambda: ())

    class FakeToolLoop:
        async def stream(self, request, *, context):
            call = LLMToolCall(
                tool_call_id="call-time-1",
                name="get_current_time",
                arguments={},
            )
            yield _event(request, "tool_call_completed", 1, tool_call=call)
            assert context.tool_execution_started is not None
            await context.tool_execution_started(call, time.monotonic())
            assert context.tool_execution_finished is not None
            context.tool_execution_finished(call, time.monotonic())
            yield _event(request, "tool_execution_completed", 2, tool_call=call)
            yield _event(
                request,
                "response_completed",
                3,
                text="It is noon UTC.",
                finish_reason="stop",
            )

    gateway.tool_loop = FakeToolLoop()
    session_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        transcript="What time is it?",
    )

    assert result["status"] == "completed"
    assert [event["type"] for event in outbound] == [
        "assistant.thinking",
        "tool.status",
        "tool.status",
        "tool.status",
        "assistant.text.final",
    ]
    assert [event["tool_status"] for event in outbound[1:4]] == [
        "understanding",
        "executing",
        "success",
    ]
    assert all(event["tool_call_id"] == "call-time-1" for event in outbound[1:4])


@pytest.mark.asyncio
async def test_cancellation_after_write_handler_before_gateway_commit_rolls_back() -> None:
    gateway, _outbound = _gateway(lambda _request: ())
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4())
    gateway.voice_session = None
    registry = ToolRegistry()

    async def noop(_context, _arguments):
        return {"created": True}

    registry.register(
        name="create_task",
        description="Create a task",
        arguments_model=EmptyToolArguments,
        handler=noop,
        required_scopes=frozenset({"tasks:write"}),
        read_only=False,
        requires_confirmation=True,
    )
    gateway.tool_registry = registry
    rollbacks: list[bool] = []

    async def rollback() -> None:
        rollbacks.append(True)

    gateway.db.rollback = rollback

    class FakeToolLoop:
        async def stream(self, request, *, context):
            del context
            call = LLMToolCall(
                tool_call_id="cancel-race-task",
                name="create_task",
                arguments={},
            )
            yield _event(request, "tool_call_completed", 1, tool_call=call)
            gateway.cancel_guard.cancel(request.response_id)
            yield _event(request, "tool_execution_completed", 2, tool_call=call)

    gateway.tool_loop = FakeToolLoop()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Create a task",
    )

    assert result["status"] == "cancelled"
    assert rollbacks == [True]
    assert gateway.db.commits == 0


@pytest.mark.asyncio
async def test_gateway_treats_confirmation_request_as_terminal_without_llm_failure() -> None:
    gateway, outbound = _gateway(lambda _request: ())
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4())
    gateway.voice_session = None
    gateway.tool_registry = SimpleNamespace(definitions=lambda: ())

    class FakeToolLoop:
        async def stream(self, request, *, context):
            del context
            call = LLMToolCall(
                tool_call_id="call-create-task",
                name="create_task",
                arguments={"title": "Call Rahul"},
            )
            yield _event(
                request,
                "confirmation_required",
                1,
                tool_call=call,
                error_code="llm_tool_confirmation_required",
            )

    gateway.tool_loop = FakeToolLoop()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Remind me to call Rahul tomorrow.",
    )

    assert result["status"] == "confirmation_required"
    assert result["proposed_tool_names"] == []
    assert [event["type"] for event in outbound] == ["assistant.thinking"]
    assert gateway.persistence.metadata == []


@pytest.mark.asyncio
async def test_gateway_emits_task_wait_phrase_without_wait_tts() -> None:
    gateway, outbound = _gateway(lambda _request: ())
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4())
    gateway.voice_session = None
    gateway.tool_registry = create_default_tool_registry()
    gateway._tts_tasks = {}
    gateway._tts_queues = {}
    gateway._turn_timings = {}

    class FakeToolLoop:
        async def stream(self, request, *, context):
            del context
            call = LLMToolCall(
                tool_call_id="call-create-task",
                name="create_task",
                arguments={"title": "Call Rahul"},
            )
            yield _event(
                request,
                "confirmation_required",
                1,
                tool_call=call,
                error_code="llm_tool_confirmation_required",
            )

    class FakeTTS:
        enabled = True

        def __init__(self) -> None:
            self.texts: list[str] = []

        async def stream(self, *, text: str, **_kwargs):
            self.texts.append(text)
            if False:
                yield b""

    tts = FakeTTS()
    gateway.tool_loop = FakeToolLoop()
    gateway.tts_service = tts
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Remind me to call Rahul tomorrow.",
    )

    assert result["status"] == "confirmation_required"
    assert result["proposed_tool_names"] == []
    assert outbound[0]["type"] == "assistant.thinking"
    assert outbound[0]["category"] == "task"
    assert outbound[0]["text"] in {"I'm on it.", "Setting that up."}
    assert tts.texts == []


@pytest.mark.asyncio
async def test_gateway_preserves_typed_provider_failure_without_text_final() -> None:
    def events(request):
        yield _event(request, "request_started", 0)
        yield _event(
            request,
            "response_failed",
            1,
            error_code="llm_rate_limited",
            retryable=True,
        )

    gateway, outbound = _gateway(events)
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Hello assistant",
    )

    assert result == {"status": "failed", "error": "llm_rate_limited"}
    assert [event["type"] for event in outbound] == [
        "assistant.thinking",
        "assistant.response.failed",
    ]
    assert outbound[1]["code"] == "llm_rate_limited"
    assert gateway.persistence.metadata[0]["llm"]["status"] == "failed"


@pytest.mark.asyncio
async def test_gateway_discards_events_after_response_is_cancelled() -> None:
    def events(request):
        yield _event(request, "request_started", 0)
        yield _event(request, "text_delta", 1, delta="late")

    gateway, outbound = _gateway(events)
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)
    assert gateway.cancel_guard.cancel(response_id) is True

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Hello assistant",
    )

    assert result == {"status": "cancelled"}
    assert outbound == []
    assert gateway.persistence.metadata == []


@pytest.mark.asyncio
async def test_gateway_queues_wait_phrase_before_reply_tts() -> None:
    def events(request):
        yield _event(request, "request_started", 0)
        yield _event(request, "response_completed", 1, text="Hello there", finish_reason="stop")

    gateway, _outbound = _gateway(events)
    gateway._tts_tasks = {}
    gateway._tts_queues = {}
    gateway._turn_timings = {}

    class FakeTTS:
        enabled = True

        def __init__(self) -> None:
            self.texts: list[str] = []

        async def stream(self, *, text: str, **_kwargs):
            self.texts.append(text)
            if False:
                yield b""

    tts = FakeTTS()
    gateway.tts_service = tts
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Hello assistant",
    )

    assert result["status"] == "completed"
    assert tts.texts == ["We're reviewing your query.", "Hello there"]


def _direct_gateway(*, failing_clock_tool: bool = False):
    from app.routing.service import DecisionRouterService

    def unexpected_llm_call(_request):
        raise AssertionError("Direct clock routes must not call the main LLM.")

    gateway, outbound = _gateway(unexpected_llm_call)
    gateway.settings = _settings().model_copy(update={"router_mode": "on"})
    gateway.router_service = DecisionRouterService(gateway.settings)
    gateway.voice_session = None
    gateway.clock = FrozenClock(datetime(2026, 9, 24, 18, 30, tzinfo=UTC))
    gateway._user_timezone = lambda: "UTC"
    gateway._time_context_for_timezone = lambda _timezone: None
    gateway._trusted_user_clock = lambda: gateway.clock
    gateway.tts_service = None

    async def unexpected_retrieval(*_args, **_kwargs):
        raise AssertionError("Direct clock routes must not call RAG or memory retrieval.")

    gateway._memory_context_for_transcript = unexpected_retrieval
    if failing_clock_tool:

        async def fail(_context, _arguments):
            raise RuntimeError("clock provider failed")

        registry = ToolRegistry()
        registry.register(
            name="get_current_time",
            description="Current time for failure test",
            arguments_model=EmptyToolArguments,
            handler=fail,
        )
    else:
        registry = create_default_tool_registry()
    gateway.tool_registry = registry
    gateway.tool_loop = SimpleNamespace(executor=ToolExecutor(registry))
    return gateway, outbound


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcript", "expected_tool", "expected_text"),
    [
        ("What time is it?", "get_current_time", "The current time is 6:30 PM."),
        (
            "What's the time in Tokyo?",
            "get_current_time",
            "The current time in Asia/Tokyo is 3:30 AM.",
        ),
        ("What's today's date?", "get_current_date", "Today's date is 24 September 2026."),
    ],
)
async def test_direct_clock_route_uses_executor_and_emits_one_final_response(
    transcript: str,
    expected_tool: str,
    expected_text: str,
) -> None:
    gateway, outbound = _direct_gateway()
    response_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=turn_id,
        response_id=response_id,
        transcript=transcript,
    )

    assert result["status"] == "completed"
    assert result["executed_tool_names"] == [expected_tool]
    assert [event["type"] for event in outbound] == [
        "tool.status",
        "tool.status",
        "assistant.text.delta",
        "assistant.text.final",
    ]
    assert [event["tool_status"] for event in outbound[:2]] == ["executing", "success"]
    assert outbound[-1]["text"] == expected_text
    assert outbound[2]["sequence"] == 0
    assert outbound[2]["delta"] == expected_text
    assert all(event["turn_id"] == str(turn_id) for event in outbound)
    assert all(event["response_id"] == str(response_id) for event in outbound)


@pytest.mark.asyncio
async def test_direct_clock_tool_failure_does_not_fall_through_to_model() -> None:
    gateway, outbound = _direct_gateway(failing_clock_tool=True)
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What time is it?",
    )

    assert result["status"] == "failed"
    assert [event["type"] for event in outbound] == [
        "tool.status",
        "tool.status",
        "assistant.text.delta",
        "assistant.text.final",
    ]
    assert outbound[-1]["text"] == "I couldn't retrieve the current time just now."


@pytest.mark.asyncio
async def test_cancelled_direct_clock_route_emits_no_tool_or_response_events() -> None:
    gateway, outbound = _direct_gateway()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)
    gateway.cancel_guard.cancel(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What time is it?",
    )

    assert result == {"status": "cancelled"}
    assert outbound == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcript", "expected_text"),
    [
        ("Yes", "There isn't a pending action to approve."),
        ("No", "Okay, nothing was changed."),
    ],
)
async def test_confirmation_words_without_pending_action_never_call_tools(
    transcript: str,
    expected_text: str,
) -> None:
    gateway, outbound = _direct_gateway()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript=transcript,
    )

    assert result["status"] == "completed"
    assert result["executed_tool_names"] == []
    assert [event["type"] for event in outbound] == [
        "assistant.text.delta",
        "assistant.text.final",
    ]
    assert outbound[0]["sequence"] == 0
    assert outbound[0]["delta"] == expected_text
    assert outbound[1]["text"] == expected_text


@pytest.mark.asyncio
async def test_no_pending_confirmation_guard_also_applies_with_router_disabled() -> None:
    gateway, outbound = _direct_gateway()
    gateway.settings = gateway.settings.model_copy(update={"router_mode": "off"})
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Yes",
    )

    assert result["executed_tool_names"] == []
    assert [event["type"] for event in outbound] == [
        "assistant.text.delta",
        "assistant.text.final",
    ]


class _StructuredReadDatabase(FakeDatabase):
    def __init__(self, rows=(), *, fail: bool = False) -> None:
        super().__init__()
        self.rows = list(rows)
        self.fail = fail
        self.statement = None

    async def scalars(self, statement):
        self.statement = statement
        if self.fail:
            raise RuntimeError("database unavailable")
        return SimpleNamespace(all=lambda: self.rows)


def _structured_read_gateway(*, rows=(), fail: bool = False):
    gateway, outbound = _direct_gateway()
    gateway.db = _StructuredReadDatabase(rows, fail=fail)
    return gateway, outbound


@pytest.mark.asyncio
async def test_today_task_query_uses_owner_scoped_db_and_skips_rag_and_model() -> None:
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        title="Dentist appointment",
        description=None,
        status="pending",
        priority="normal",
        due_at=datetime(2026, 9, 24, 21, tzinfo=UTC),
        local_due_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
        timezone="America/Los_Angeles",
        timezone_source="device",
    )
    gateway, outbound = _structured_read_gateway(rows=[task])
    response_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=turn_id,
        response_id=response_id,
        transcript="What tasks do I have today?",
    )

    compiled = gateway.db.statement.compile()
    assert gateway.principal.user_id in compiled.params.values()
    assert result["status"] == "completed"
    assert result["executed_tool_names"] == ["list_tasks"]
    assert [event["type"] for event in outbound] == [
        "tool.status",
        "tool.status",
        "assistant.text.delta",
        "assistant.text.final",
    ]
    assert outbound[-1]["text"] == (
        "Task 'Dentist appointment' is scheduled for 24 September 2026 at 2:00 PM."
    )


@pytest.mark.asyncio
async def test_medicine_time_query_reads_reminders_and_does_not_answer_current_time() -> None:
    reminder = SimpleNamespace(
        id=uuid.uuid4(),
        title="Take medicine",
        body="Morning medication",
        trigger_at=datetime(2026, 9, 25, 14, tzinfo=UTC),
        local_trigger_at=datetime(2026, 9, 25, 7, tzinfo=UTC),
        timezone="America/Los_Angeles",
        timezone_source="device",
        status="scheduled",
    )
    gateway, outbound = _structured_read_gateway(rows=[reminder])
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What time do I take my medicine?",
    )

    assert result["executed_tool_names"] == ["list_reminders"]
    assert "reminders.user_id" in str(gateway.db.statement)
    assert outbound[-1]["text"] == (
        "Reminder 'Take medicine' is scheduled for 25 September 2026 at 7:00 AM."
    )


@pytest.mark.asyncio
async def test_structured_read_db_failure_is_reported_without_model_fallback() -> None:
    gateway, outbound = _structured_read_gateway(fail=True)
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What is my next meeting?",
    )

    assert result["status"] == "failed"
    assert result["executed_tool_names"] == []
    assert outbound[-1]["text"] == "I couldn't check your tasks right now."
    assert [event["type"] for event in outbound][-2:] == [
        "assistant.text.delta",
        "assistant.text.final",
    ]


class _FakeRoutedMemoryService:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    async def retrieve(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result

    async def has_multiple_current_matches(self, *_args, **_kwargs):
        return False


def _enable_routed_memory(gateway, service) -> None:
    gateway.settings = gateway.settings.model_copy(
        update={"router_mode": "on", "memory_retrieval_mode": "inject"}
    )
    gateway.router_service.settings = gateway.settings
    gateway.websocket = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(memory_service=service))
    )

    async def memory_enabled(_statement):
        return True

    gateway.db.scalar = memory_enabled


@pytest.mark.asyncio
async def test_exact_memory_query_returns_attributed_evidence_without_main_llm() -> None:
    from app.memory.types import FusedMemory, MemoryQueryPlan, MemoryRetrievalResult, MemoryType

    gateway, outbound = _direct_gateway()
    memory = FusedMemory(
        memory_id=uuid.uuid4(),
        user_id=gateway.principal.user_id,
        content="I prefer chai.",
        rank=1,
        sources=("structured", "fts"),
        created_at=datetime(2026, 9, 20, tzinfo=UTC),
        memory_type=MemoryType.PREFERENCE,
        subject="user",
        predicate="favorite_drink",
    )
    service = _FakeRoutedMemoryService(
        MemoryRetrievalResult(
            status="ready",
            plan=MemoryQueryPlan(normalized_query="What is my favorite drink?"),
            memories=(memory,),
        )
    )
    _enable_routed_memory(gateway, service)
    persisted = {}

    async def capture_persist(**kwargs):
        persisted.update(kwargs["content_json"])

    gateway._persist_final_message_if_supported = capture_persist
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What is my favorite drink?",
    )

    assert result["status"] == "completed"
    assert service.calls[0][1]["user_id"] == gateway.principal.user_id
    assert result["memory_evidence_ids"] == [str(memory.memory_id)]
    assert persisted["memory_evidence_ids"] == [str(memory.memory_id)]
    assert persisted["memory_evidence_route"] == "DIRECT_RAG"
    assert outbound[-1]["text"] == "I have this saved: “I prefer chai.”"
    assert [event["type"] for event in outbound] == [
        "assistant.text.delta",
        "assistant.text.final",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retrieval_status", "expected"),
    [
        ("ready", "I don't have a saved memory that answers that."),
        ("degraded", "I couldn't check your saved memories right now."),
    ],
)
async def test_missing_or_failed_memory_does_not_fall_through_to_general_llm(
    retrieval_status: str, expected: str
) -> None:
    from app.memory.types import MemoryQueryPlan, MemoryRetrievalResult

    gateway, outbound = _direct_gateway()
    service = _FakeRoutedMemoryService(
        MemoryRetrievalResult(
            status=retrieval_status,
            plan=MemoryQueryPlan(normalized_query="What is my favorite spaceship?"),
        )
    )
    _enable_routed_memory(gateway, service)
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What is my favorite spaceship?",
    )

    assert result["status"] == "completed"
    assert outbound[-1]["text"] == expected
    assert service.calls


@pytest.mark.asyncio
async def test_non_memory_route_skips_legacy_retrieval_in_router_canary_path() -> None:
    gateway, _outbound = _direct_gateway()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._dispatch_structured_route(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What is the weather like today?",
    )

    assert result == {"status": "continue_without_memory_retrieval"}


@pytest.mark.asyncio
async def test_general_route_omits_memory_context_and_search_but_keeps_history() -> None:
    from app.memory.tool_tools import register_memory_tools
    from app.routing.service import DecisionRouterService

    captured = []
    gateway, _outbound = _gateway(lambda _request: ())
    gateway.settings = _settings().model_copy(
        update={"router_mode": "on", "memory_retrieval_mode": "inject"}
    )
    gateway.router_service = DecisionRouterService(gateway.settings)
    registry = create_default_tool_registry()
    register_memory_tools(registry, allow_write=True)
    gateway.tool_registry = registry
    gateway.principal = SimpleNamespace(user_id=uuid.uuid4())
    gateway.voice_session = None
    gateway.tts_service = None
    gateway._memory_user_enabled = lambda: _return_true()
    gateway._memory_excluded_for_session = lambda: _return_false()
    history = (LLMMessage(role=LLMRole.USER, content="Earlier user request."),)

    async def history_for_turn(*_args, **_kwargs):
        return history

    async def unexpected_memory_context(*_args, **_kwargs):
        raise AssertionError("General route must skip memory retrieval.")

    gateway._conversation_history_for_turn = history_for_turn
    gateway._memory_context_for_transcript = unexpected_memory_context

    class CapturingToolLoop:
        async def stream(self, request, *, context):
            del context
            captured.append(request)
            yield _event(request, "response_completed", 1, text="Plants use light.")

    async def _return_true() -> bool:
        return True

    async def _return_false() -> bool:
        return False

    gateway.tool_loop = CapturingToolLoop()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)
    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What is photosynthesis?",
    )

    assert result["status"] == "completed"
    assert len(captured) == 1
    assert captured[0].messages[0].content == "Earlier user request."
    assert len(captured[0].messages) == 2
    assert all(tool.name != "memory_search" for tool in captured[0].allowed_tools)
    assert "Memory evidence policy" not in captured[0].system_instructions


@pytest.mark.asyncio
async def test_memory_forget_without_resolvable_id_abstains_without_retrieval_or_llm() -> None:
    gateway, outbound = _direct_gateway()
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="Forget that I prefer tea.",
    )

    assert result["status"] == "completed"
    assert result["executed_tool_names"] == []
    assert "safely identify" in outbound[-1]["text"]


@pytest.mark.asyncio
async def test_memory_synthesis_passes_only_bounded_selected_evidence_to_llm() -> None:
    from app.memory.types import FusedMemory, MemoryQueryPlan, MemoryRetrievalResult, MemoryType

    gateway, _outbound = _direct_gateway()
    first = FusedMemory(
        memory_id=uuid.uuid4(),
        user_id=gateway.principal.user_id,
        content="I work remotely from Mumbai.",
        rank=1,
        sources=("fts",),
        created_at=datetime(2026, 9, 20, tzinfo=UTC),
        memory_type=MemoryType.FACT,
    )
    second = first.model_copy(
        update={
            "memory_id": uuid.uuid4(),
            "rank": 2,
            "content": "I joined Project Atlas in August.",
        }
    )
    service = _FakeRoutedMemoryService(
        MemoryRetrievalResult(
            status="ready",
            plan=MemoryQueryPlan(normalized_query="What have I told you about my work?"),
            memories=(first, second),
        )
    )
    _enable_routed_memory(gateway, service)
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    outcome = await gateway._dispatch_structured_route(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What have I told you about my work?",
    )

    assert outcome is not None
    assert outcome["status"] == "continue_with_memory_evidence"
    assert outcome["memory_evidence_ids"] == (first.memory_id, second.memory_id)
    assert f"memory_id={first.memory_id}" in outcome["memory_context"]
    assert "status=active" in outcome["memory_context"]
    assert "I work remotely from Mumbai." in outcome["memory_context"]
    assert "I joined Project Atlas in August." in outcome["memory_context"]
    assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_memory_opt_out_is_reported_without_retrieval() -> None:
    from app.memory.types import MemoryQueryPlan, MemoryRetrievalResult

    gateway, outbound = _direct_gateway()
    service = _FakeRoutedMemoryService(
        MemoryRetrievalResult(
            status="ready",
            plan=MemoryQueryPlan(normalized_query="What is my name?"),
        )
    )
    _enable_routed_memory(gateway, service)

    async def memory_disabled(_statement):
        return False

    gateway.db.scalar = memory_disabled
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What is my name?",
    )

    assert result["status"] == "completed"
    assert service.calls == []
    assert outbound[-1]["text"] == "Saved memory is turned off for this account."


@pytest.mark.asyncio
async def test_excluded_session_never_retrieves_memory() -> None:
    from app.memory.types import MemoryQueryPlan, MemoryRetrievalResult

    gateway, outbound = _direct_gateway()
    service = _FakeRoutedMemoryService(
        MemoryRetrievalResult(
            status="ready",
            plan=MemoryQueryPlan(normalized_query="What is my name?"),
        )
    )
    _enable_routed_memory(gateway, service)
    gateway._session_client_metadata = {"memory_excluded": True}
    response_id = uuid.uuid4()
    gateway.cancel_guard.activate(response_id)

    result = await gateway._stream_llm_response(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=response_id,
        transcript="What is my name?",
    )

    assert result["status"] == "completed"
    assert service.calls == []
    assert outbound[-1]["text"] == "I can't access saved memories in this session right now."


def test_gateway_transcript_delivery_log_is_content_free() -> None:
    from app.websocket.gateway import _transcript_delivery_log_fields

    secret_transcript = "Call Rahul about the confidential Orchid account tomorrow."
    event = SimpleNamespace(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        timestamp_ms=123,
        text=secret_transcript,
        language="en",
    )

    fields = _transcript_delivery_log_fields(event)

    assert fields["event"] == "voice.transcript.final.delivered"
    assert fields["session_id"] == str(event.session_id)
    assert "text" not in fields
    assert "language" not in fields
    assert secret_transcript not in repr(fields)
