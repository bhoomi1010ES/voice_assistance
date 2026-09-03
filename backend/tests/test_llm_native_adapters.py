from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.core.config import Settings
from app.llm.providers.anthropic_messages import AnthropicMessagesProvider
from app.llm.providers.openai_responses import OpenAIResponsesProvider
from app.llm.types import (
    LLMMessage,
    LLMNamedToolChoice,
    LLMRequest,
    LLMRole,
    LLMToolCall,
    LLMToolDefinition,
)


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


def _settings(provider: str, base_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        llm_provider=provider,
        llm_base_url=base_url,
        llm_api_key="test-placeholder-key",
        llm_model="test-model",
        llm_max_retry_attempts=0,
    )


def _follow_up_request() -> LLMRequest:
    call = LLMToolCall(
        tool_call_id="call-1",
        name="lookup_weather",
        arguments_json='{"city":"Mumbai"}',
        arguments={"city": "Mumbai"},
    )
    return LLMRequest(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        system_instructions="Answer briefly.",
        messages=(
            LLMMessage(role=LLMRole.USER, content="What is the weather?"),
            LLMMessage(role=LLMRole.ASSISTANT, content="", tool_calls=(call,)),
            LLMMessage(
                role=LLMRole.TOOL,
                content='{"ok":true,"result":{"temperature":24}}',
                tool_call_id="call-1",
            ),
        ),
        max_output_tokens=128,
    )


async def _collect(provider, request: LLMRequest):
    await provider.initialize()
    try:
        return [event async for event in provider.stream(request)]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_responses_maps_native_text_usage_and_request_shape() -> None:
    captured: dict = {}
    body = (
        'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
        'data: {"type":"response.completed","response":{"id":"resp-1",'
        '"model":"returned-model","status":"completed",'
        '"usage":{"input_tokens":4,"output_tokens":1,"total_tokens":5}}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIResponsesProvider(
        _settings("openai", "https://api.openai.com/v1"),
        client=client,
    )
    events = await _collect(provider, _request())
    await client.aclose()

    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["headers"]["authorization"] == "Bearer test-placeholder-key"
    assert captured["body"]["store"] is False
    assert captured["body"]["instructions"] == "Answer briefly."
    assert captured["body"]["input"][0]["role"] == "user"
    assert [event.delta for event in events if event.event_type == "text_delta"] == ["Hello"]
    usage = next(event.usage for event in events if event.event_type == "usage")
    assert usage is not None
    assert usage.total_tokens == 5
    completed = next(event for event in events if event.event_type == "response_completed")
    assert completed.text == "Hello"
    assert completed.provider_request_id == "resp-1"


@pytest.mark.asyncio
async def test_openai_responses_maps_function_call_fragments_once() -> None:
    tool = LLMToolDefinition(
        name="lookup_weather",
        description="Look up current weather.",
        input_schema={"type": "object"},
    )
    body = (
        'data: {"type":"response.output_item.added","output_index":0,'
        '"item":{"type":"function_call","id":"item-1","call_id":"call-1",'
        '"name":"lookup_weather"}}\n\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"item-1",'
        '"delta":"{\\"city\\":\\"Mumbai\\"}"}\n\n'
        'data: {"type":"response.function_call_arguments.done","item_id":"item-1",'
        '"arguments":"{\\"city\\":\\"Mumbai\\"}"}\n\n'
        'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    provider = OpenAIResponsesProvider(
        _settings("openai", "https://api.openai.com/v1"),
        client=client,
    )
    events = await _collect(provider, _request(tools=(tool,)))
    await client.aclose()

    completed = [event for event in events if event.event_type == "tool_call_completed"]
    assert len(completed) == 1
    assert completed[0].tool_call is not None
    assert completed[0].tool_call.tool_call_id == "call-1"
    assert completed[0].tool_call.arguments == {"city": "Mumbai"}


@pytest.mark.asyncio
async def test_anthropic_messages_maps_native_headers_text_usage_and_completion() -> None:
    captured: dict = {}
    body = (
        'event: message_start\n'
        'data: {"type":"message_start","message":{"id":"msg-1",'
        '"model":"returned-model","usage":{"input_tokens":6}}}\n\n'
        'event: content_block_start\n'
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}\n\n'
        'event: content_block_delta\n'
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta",'
        '"text":"Hello"}}\n\n'
        'event: message_delta\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":2}}\n\n'
        'event: message_stop\n'
        'data: {"type":"message_stop"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicMessagesProvider(
        _settings("anthropic", "https://api.anthropic.com"),
        client=client,
    )
    events = await _collect(provider, _request())
    await client.aclose()

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "test-placeholder-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["body"]["system"] == "Answer briefly."
    assert captured["body"]["max_tokens"] == 128
    assert [event.delta for event in events if event.event_type == "text_delta"] == ["Hello"]
    usage = [event.usage for event in events if event.event_type == "usage"][-1]
    assert usage is not None
    assert usage.input_tokens == 6
    assert usage.output_tokens == 2
    assert usage.total_tokens == 8
    completed = next(event for event in events if event.event_type == "response_completed")
    assert completed.text == "Hello"
    assert completed.provider_request_id == "msg-1"
    assert completed.finish_reason == "end_turn"


@pytest.mark.asyncio
async def test_anthropic_messages_maps_tool_use_and_does_not_duplicate_completion() -> None:
    tool = LLMToolDefinition(
        name="lookup_weather",
        description="Look up current weather.",
        input_schema={"type": "object"},
    )
    body = (
        'data: {"type":"message_start","message":{"id":"msg-tool",'
        '"model":"test-model"}}\n\n'
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use",'
        '"id":"tool-1","name":"lookup_weather"}}\n\n'
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta",'
        '"partial_json":"{\\"city\\":\\"Mumbai\\"}"}}\n\n'
        'data: {"type":"content_block_stop","index":0}\n\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    provider = AnthropicMessagesProvider(
        _settings("anthropic", "https://api.anthropic.com"),
        client=client,
    )
    events = await _collect(provider, _request(tools=(tool,)))
    await client.aclose()

    completed = [event for event in events if event.event_type == "tool_call_completed"]
    assert len(completed) == 1
    assert completed[0].tool_call is not None
    assert completed[0].tool_call.arguments == {"city": "Mumbai"}


def test_follow_up_tool_results_map_to_each_native_wire_contract() -> None:
    request = _follow_up_request()
    openai_payload = OpenAIResponsesProvider(
        _settings("openai", "https://api.openai.com/v1")
    )._build_payload(request)
    anthropic_payload = AnthropicMessagesProvider(
        _settings("anthropic", "https://api.anthropic.com")
    )._build_payload(request)

    openai_items = openai_payload["input"]
    assert openai_items[1]["type"] == "function_call"
    assert openai_items[1]["call_id"] == "call-1"
    assert openai_items[2]["type"] == "function_call_output"
    assert openai_items[2]["call_id"] == "call-1"
    assert anthropic_payload["messages"][1]["content"][0]["type"] == "tool_use"
    assert anthropic_payload["messages"][1]["content"][0]["id"] == "call-1"
    assert anthropic_payload["messages"][2]["content"][0]["type"] == "tool_result"
    assert anthropic_payload["messages"][2]["content"][0]["tool_use_id"] == "call-1"


def test_named_tool_choice_maps_to_each_native_wire_contract() -> None:
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

    openai_payload = OpenAIResponsesProvider(
        _settings("openai", "https://api.openai.com/v1")
    )._build_payload(request)
    anthropic_payload = AnthropicMessagesProvider(
        _settings("anthropic", "https://api.anthropic.com")
    )._build_payload(request)

    assert openai_payload["tool_choice"] == {"type": "function", "name": "create_task"}
    assert anthropic_payload["tool_choice"] == {"type": "tool", "name": "create_task"}
