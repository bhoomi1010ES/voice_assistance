from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from app.core.config import Settings
from app.llm.errors import LLMAuthenticationError, LLMOverloadedError, LLMProtocolError
from app.llm.providers.nvidia import NvidiaProvider
from app.llm.types import (
    LLMMessage,
    LLMNamedToolChoice,
    LLMRequest,
    LLMRole,
    LLMToolDefinition,
)


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


def test_nvidia_named_tool_choice_is_serialized_without_expanding_tool_scope() -> None:
    tool = LLMToolDefinition(
        name="create_task",
        description="Create a task after confirmation.",
        input_schema={"type": "object"},
    )
    request = _request(tools=(tool,)).model_copy(
        update={
            "tool_choice": LLMNamedToolChoice(function={"name": "create_task"}),
        }
    )

    payload = NvidiaProvider(_settings())._build_payload(request)

    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "create_task"},
    }
    assert [item["function"]["name"] for item in payload["tools"]] == ["create_task"]


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
async def test_nvidia_trace_records_adapter_and_stream_boundaries(monkeypatch, tmp_path) -> None:
    trace_path = tmp_path / "llm-trace.jsonl"
    monkeypatch.setenv("LATENCY_TRACE_PATH", str(trace_path))
    body = 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
    body += 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    body += "data: [DONE]\n\n"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=body))
    )
    provider = NvidiaProvider(_settings(), client=client)
    request = _request()
    await _collect(provider, request)
    await client.aclose()

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    turn_records = [record for record in records if record.get("turn_id") == str(request.turn_id)]
    events = {record["event"] for record in turn_records}
    assert {
        "llm_prepare_started",
        "llm_prepare_completed",
        "provider_prepare_started",
        "provider_prepare_completed",
        "http_request_started",
        "llm_request_started",
        "stream_opened",
        "llm_stream_opened",
        "first_sse_event",
        "llm_first_sse_event",
        "llm_first_event",
        "first_content_token",
        "llm_first_content_token",
    } <= events
    assert all(record["monotonic_ns"] > 0 for record in turn_records)
    assert '"ok"' not in trace_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_nvidia_trace_records_tcp_and_response_header_boundaries(
    monkeypatch, tmp_path
) -> None:
    trace_path = tmp_path / "network-trace.jsonl"
    monkeypatch.setenv("LATENCY_TRACE_PATH", str(trace_path))
    body = b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
    body += b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    body += b"data: [DONE]\n\n"

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        headers = await reader.readuntil(b"\r\n\r\n")
        header_lines = headers.decode("latin1").split("\r\n")
        content_length = next(
            int(line.split(":", 1)[1].strip())
            for line in header_lines
            if line.casefold().startswith("content-length:")
        )
        await reader.readexactly(content_length)
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    provider = NvidiaProvider(_settings(llm_base_url=f"http://127.0.0.1:{port}/v1"))
    request = _request()
    try:
        await _collect(provider, request)
    finally:
        server.close()
        await server.wait_closed()

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    turn_records = [record for record in records if record.get("turn_id") == str(request.turn_id)]
    events = {record["event"] for record in turn_records}
    assert "llm_connection_acquired" in events
    assert "llm_tcp_connect_started" in events
    assert "llm_tcp_connect_completed" in events
    assert "response_headers_received" in events
    assert "llm_response_headers_received" in events


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
async def test_nvidia_stream_maps_embedded_service_unavailable_error() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"retry-after": "3", "x-request-id": "request-503"},
                content=(
                    'data: {"error":{"message":"temporary detail",'
                    '"type":"service_unavailable","code":503}}\n\n'
                    "data: [DONE]\n\n"
                ),
            )
        )
    )
    provider = NvidiaProvider(_settings(), client=client)
    await provider.initialize()

    with pytest.raises(LLMOverloadedError) as raised:
        _ = [event async for event in provider.stream(_request())]

    await client.aclose()
    assert raised.value.code == "llm_overloaded"
    assert raised.value.status_code == 503
    assert raised.value.request_id == "request-503"
    assert raised.value.retry_after_seconds == 3
    assert "temporary detail" not in str(raised.value)


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
