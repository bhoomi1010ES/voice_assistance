from __future__ import annotations

import time
import uuid

import pytest

from app.core.config import Settings
from app.llm.types import LLMCapabilities, LLMEvent, LLMProviderInfo, LLMUsage
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


class FakeDatabase:
    def __init__(self) -> None:
        self.commits = 0

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
    gateway.principal = object()
    outbound: list[dict] = []

    async def send(event: dict) -> None:
        outbound.append(event)

    gateway._send = send
    return gateway, outbound


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
        "assistant.text.delta",
        "assistant.text.delta",
        "assistant.text.final",
    ]
    assert all(event["session_id"] == str(session_id) for event in outbound)
    assert all(event["turn_id"] == str(turn_id) for event in outbound)
    assert all(event["response_id"] == str(response_id) for event in outbound)
    persisted = gateway.persistence.metadata[0]["llm"]
    assert persisted["status"] == "completed"
    assert persisted["response_text"] == "Hello there"
    assert persisted["usage"] == {"input_tokens": 9, "output_tokens": 2, "total_tokens": 11}
    assert "test-placeholder-key" not in repr(persisted)


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
    assert [event["type"] for event in outbound] == ["assistant.response.failed"]
    assert outbound[0]["code"] == "llm_rate_limited"
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
