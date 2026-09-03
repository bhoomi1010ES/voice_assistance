from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from app.core.config import Settings
from app.llm.errors import LLMConfigurationError, LLMRateLimitError
from app.llm.service import LLMService
from app.llm.types import (
    LLMCapabilities,
    LLMEvent,
    LLMMessage,
    LLMProviderInfo,
    LLMRequest,
    LLMRole,
)


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "llm_provider": "nvidia",
        "llm_base_url": "https://integrate.api.nvidia.com/v1",
        "llm_api_key": "test-placeholder-key",
        "llm_model": "nvidia/nemotron-3-super-120b-a12b",
        "llm_max_retry_attempts": 1,
    }
    values.update(overrides)
    return Settings(**values)


def _request() -> LLMRequest:
    return LLMRequest(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        system_instructions="Answer briefly.",
        messages=(LLMMessage(role=LLMRole.USER, content="Hello"),),
        max_output_tokens=64,
    )


class RetryProvider:
    def __init__(self, *, fail_after_delta: bool = False) -> None:
        self.fail_after_delta = fail_after_delta
        self.attempts: list[int] = []
        self.closed = False

    async def initialize(self) -> LLMProviderInfo:
        return LLMProviderInfo(
            provider="nvidia",
            api_family="openai_chat_completions",
            host="https://integrate.api.nvidia.com",
            configured_model="nvidia/nemotron-3-super-120b-a12b",
            capabilities=LLMCapabilities(
                streaming=True,
                text_generation=True,
                cancellation=True,
            ),
        )

    async def stream(self, request: LLMRequest, *, attempt: int = 1):
        self.attempts.append(attempt)
        yield self._event(request, "request_started", attempt, 0)
        if attempt == 1 and not self.fail_after_delta:
            raise LLMRateLimitError("rate limited", retry_after_seconds=0)
        yield self._event(request, "text_delta", attempt, 1, delta="ok")
        if attempt == 1 and self.fail_after_delta:
            raise LLMRateLimitError("rate limited", retry_after_seconds=0)
        yield self._event(request, "response_completed", attempt, 2, text="ok")

    async def close(self) -> None:
        self.closed = True

    @staticmethod
    def _event(request, event_type, attempt, sequence, **values) -> LLMEvent:
        return LLMEvent(
            event_type=event_type,
            session_id=request.session_id,
            turn_id=request.turn_id,
            response_id=request.response_id,
            provider="nvidia",
            configured_model="nvidia/nemotron-3-super-120b-a12b",
            monotonic_seconds=time.monotonic(),
            sequence=sequence,
            attempt=attempt,
            **values,
        )


@pytest.mark.asyncio
async def test_disabled_service_is_explicit_and_does_not_create_provider() -> None:
    service = LLMService(Settings(_env_file=None))

    assert await service.initialize() is None
    assert service.enabled is False
    assert service.readiness() == {"enabled": False, "status": "disabled"}
    with pytest.raises(LLMConfigurationError):
        _ = [event async for event in service.stream(_request())]


@pytest.mark.asyncio
async def test_service_retries_only_before_first_output() -> None:
    provider = RetryProvider()
    service = LLMService(_settings(), provider=provider)
    await service.initialize()

    events = [event async for event in service.stream(_request())]
    await service.close()

    assert provider.attempts == [1, 2]
    assert [event.sequence for event in events] == list(range(len(events)))
    assert events[-1].event_type == "response_completed"
    assert provider.closed is True


@pytest.mark.asyncio
async def test_service_does_not_retry_after_text_delta() -> None:
    provider = RetryProvider(fail_after_delta=True)
    service = LLMService(_settings(), provider=provider)
    await service.initialize()

    events = [event async for event in service.stream(_request())]

    assert provider.attempts == [1]
    assert [event.event_type for event in events] == [
        "request_started",
        "text_delta",
        "response_failed",
    ]
    assert events[-1].error_code == "llm_rate_limited"
    await service.close()


class BlockingProvider(RetryProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def stream(self, request: LLMRequest, *, attempt: int = 1):
        self.started.set()
        yield self._event(request, "request_started", attempt, 0)
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_service_cancellation_stops_active_stream() -> None:
    provider = BlockingProvider()
    service = LLMService(_settings(), provider=provider)
    await service.initialize()
    request = _request()

    async def consume() -> None:
        _ = [event async for event in service.stream(request)]

    task = asyncio.create_task(consume())
    await provider.started.wait()
    assert await service.cancel(request.response_id) is True
    assert task.done()
    assert await service.cancel(request.response_id) is False
    await service.close()
