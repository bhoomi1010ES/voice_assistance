from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.core.config import Settings
from app.llm.errors import LLMAuthenticationError, LLMProtocolError
from app.llm.providers.nvidia import NvidiaProvider
from app.llm.types import LLMMessage, LLMRequest, LLMRole, LLMToolDefinition


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "llm_provider": "nvidia",
        "llm_base_url": "https://integrate.api.nvidia.com/v1",
        "llm_api_key": "test-placeholder-key",
        "llm_model": "nvidia/nemotron-3-super-120b-a12b",
        "llm_max_retry_attempts": 0,
    }
    values.update(overrides)
    return Settings(**values)


def _request(*, tools=()) -> LLMRequest:
    return LLMRequest(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        system_instructions="Answer briefly.",
        messages=(LLMMessage(role=LLMRole.USER, content="Hello"),),
        allowed_tools=tools,
        max_output_tokens=128,
    )


async def _collect(provider: NvidiaProvider, request: LLMRequest):
    await provider.initialize()
    try:
        return [event async for event in provider.stream(request)]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_nvidia_stream_maps_text_usage_and_completion() -> None:
    captured: dict = {}
    body = (
        'data: {"id":"req-1","model":"returned-model","choices":['
        '{"delta":{"content":"Hello "},"finish_reason":null}]}\n\n'
        'data: {"id":"req-1","choices":['
        '{"delta":{"reasoning_content":"private","content":"world"},'
        '"finish_reason":null}]}\n\n'
        'data: {"id":"req-1","choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-request-id": "header-request"},
            content=body,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = NvidiaProvider(_settings(), client=client)
    events = await _collect(provider, _request())
    await client.aclose()

    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-placeholder-key"
    assert captured["json"]["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert captured["json"]["stream"] is True
    assert captured["json"]["temperature"] == 1.0
    assert captured["json"]["top_p"] == 0.95
    assert [event.delta for event in events if event.event_type == "text_delta"] == [
        "Hello ",
        "world",
    ]
    assert all(event.delta != "private" for event in events)
    usage = next(event.usage for event in events if event.event_type == "usage")
    assert usage is not None
    assert usage.total_tokens == 9
    completed = next(event for event in events if event.event_type == "response_completed")
    assert completed.text == "Hello world"
    assert completed.provider_request_id == "header-request"
    assert completed.returned_model == "returned-model"
    assert completed.finish_reason == "stop"


@pytest.mark.asyncio
async def test_nvidia_stream_assembles_and_validates_fragmented_tool_arguments() -> None:
    tool = LLMToolDefinition(
        name="lookup_weather",
        description="Look up current weather.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    body = (
        'data: {"id":"req-tool","choices":[{"delta":{"tool_calls":[{"index":0,'
        '"id":"call-1","function":{"name":"lookup_weather",'
        '"arguments":"{\\"city\\":\\""}}]},"finish_reason":null}]}\n\n'
        'data: {"id":"req-tool","choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"Mumbai\\"}"}}]},"finish_reason":"tool_calls"}]}\n\n'
        "data: [DONE]\n\n"
    )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    provider = NvidiaProvider(_settings(), client=client)
    events = await _collect(provider, _request(tools=(tool,)))
    await client.aclose()

    completed = next(event for event in events if event.event_type == "tool_call_completed")
    assert completed.tool_call is not None
    assert completed.tool_call.tool_call_id == "call-1"
    assert completed.tool_call.name == "lookup_weather"
    assert completed.tool_call.arguments == {"city": "Mumbai"}


@pytest.mark.asyncio
async def test_nvidia_authentication_error_is_typed_and_does_not_include_body() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                401,
                headers={"x-request-id": "request-401"},
                json={"error": {"message": "credential value must not escape"}},
            )
        )
    )
    provider = NvidiaProvider(_settings(), client=client)
    await provider.initialize()

    with pytest.raises(LLMAuthenticationError) as raised:
        _ = [event async for event in provider.stream(_request())]

    await client.aclose()
    assert raised.value.code == "llm_authentication_error"
    assert raised.value.request_id == "request-401"
    assert "credential value" not in str(raised.value)


@pytest.mark.asyncio
async def test_nvidia_malformed_sse_is_a_protocol_error() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content="data: {not-json}\n\n")
        )
    )
    provider = NvidiaProvider(_settings(), client=client)
    await provider.initialize()

    with pytest.raises(LLMProtocolError):
        _ = [event async for event in provider.stream(_request())]

    await client.aclose()
