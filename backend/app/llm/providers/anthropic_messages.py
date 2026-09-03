from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings
from app.llm.errors import LLMError, LLMProtocolError, LLMProviderError
from app.llm.providers.openai_chat import OpenAIChatProvider, _ToolAccumulator
from app.llm.types import (
    LLMCapabilities,
    LLMEvent,
    LLMEventType,
    LLMRequest,
    LLMToolCall,
    LLMUsage,
)


class AnthropicMessagesProvider(OpenAIChatProvider):
    """Native Anthropic Messages streaming adapter."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(settings, provider_name="anthropic", client=client)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/v1/messages"

    @property
    def api_family(self) -> str:
        return "anthropic_messages"

    @property
    def capabilities(self) -> LLMCapabilities:
        return LLMCapabilities(
            streaming=True,
            text_generation=True,
            tool_calling=True,
            usage_reporting=True,
            cancellation=True,
        )

    def _build_payload(self, request: LLMRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role.value == "tool":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id,
                                "content": message.content,
                            }
                        ],
                    }
                )
            elif message.role.value == "assistant" and message.tool_calls:
                content: list[dict[str, Any]] = []
                if message.content:
                    content.append({"type": "text", "text": message.content})
                for tool_call in message.tool_calls:
                    arguments = tool_call.arguments
                    if arguments is None:
                        try:
                            parsed = json.loads(tool_call.arguments_json or "{}")
                        except json.JSONDecodeError as error:
                            raise LLMProtocolError(
                                "Assistant tool-call arguments are not valid JSON."
                            ) from error
                        if not isinstance(parsed, dict):
                            raise LLMProtocolError(
                                "Assistant tool-call arguments must be a JSON object."
                            )
                        arguments = parsed
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tool_call.tool_call_id,
                            "name": tool_call.name,
                            "input": arguments,
                        }
                    )
                messages.append({"role": "assistant", "content": content})
            else:
                messages.append({"role": message.role.value, "content": message.content})
        payload: dict[str, Any] = {
            "model": self.model,
            "system": request.system_instructions,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "stream": True,
        }
        if request.allowed_tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.allowed_tools
            ]
            if request.tool_choice == "required":
                payload["tool_choice"] = {"type": "any"}
            elif request.tool_choice == "none":
                payload["tool_choice"] = {"type": "none"}
        return payload

    async def stream(
        self,
        request: LLMRequest,
        *,
        attempt: int = 1,
    ) -> AsyncIterator[LLMEvent]:
        if not self._initialized:
            raise LLMProtocolError("The LLM provider was not initialized.")
        client = self._ensure_client()
        sequence = 0

        def event(event_type: LLMEventType, **values: Any) -> LLMEvent:
            nonlocal sequence
            result = LLMEvent(
                event_type=event_type,
                session_id=request.session_id,
                turn_id=request.turn_id,
                response_id=request.response_id,
                provider=self.provider_name,
                configured_model=self.model,
                monotonic_seconds=time.monotonic(),
                sequence=sequence,
                attempt=attempt,
                **values,
            )
            sequence += 1
            return result

        yield event("request_started")
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "x-api-key": self._api_key(),
            "anthropic-version": self.settings.llm_anthropic_version,
        }
        provider_request_id: str | None = None
        returned_model: str | None = None
        finish_reason: str | None = None
        text_parts: list[str] = []
        tools: dict[int, _ToolAccumulator] = {}
        usage_input: int | None = None
        usage_output: int | None = None
        event_data: list[str] = []
        event_data_bytes = 0
        total_response_bytes = 0
        completed = False
        saw_payload = False

        async def process_data(data: str) -> AsyncIterator[LLMEvent]:
            nonlocal completed, saw_payload, provider_request_id, returned_model
            nonlocal finish_reason, usage_input, usage_output
            if not data:
                return
            if data == "[DONE]":
                for tool_event in self._complete_anthropic_tools(tools, event):
                    yield tool_event
                yield event(
                    "response_completed",
                    text="".join(text_parts),
                    provider_request_id=provider_request_id,
                    returned_model=returned_model,
                    finish_reason=finish_reason,
                )
                completed = True
                return
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as error:
                raise LLMProtocolError("The provider returned malformed SSE JSON.") from error
            if not isinstance(payload, dict):
                raise LLMProtocolError("The provider returned a non-object SSE payload.")
            saw_payload = True
            event_type = payload.get("type")

            if event_type == "message_start":
                message = payload.get("message")
                if not isinstance(message, dict):
                    raise LLMProtocolError("The provider returned an invalid message start.")
                value = message.get("id")
                if isinstance(value, str) and value:
                    provider_request_id = provider_request_id or value
                value = message.get("model")
                if isinstance(value, str) and value:
                    returned_model = value
                usage = message.get("usage")
                if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
                    usage_input = usage["input_tokens"]
                    yield event(
                        "usage",
                        usage=LLMUsage(input_tokens=usage_input),
                        provider_request_id=provider_request_id,
                        returned_model=returned_model,
                    )
                return

            if event_type == "content_block_start":
                block = payload.get("content_block")
                index = payload.get("index")
                if not isinstance(block, dict) or not isinstance(index, int):
                    raise LLMProtocolError("The provider returned an invalid content block.")
                if block.get("type") == "tool_use":
                    call_id = block.get("id")
                    name = block.get("name")
                    if not isinstance(call_id, str) or not call_id:
                        raise LLMProtocolError("The provider returned a tool call without an ID.")
                    if not isinstance(name, str) or not name:
                        raise LLMProtocolError("The provider returned a tool call without a name.")
                    accumulator = _ToolAccumulator(tool_call_id=call_id, name=name, started=True)
                    tools[index] = accumulator
                    yield event(
                        "tool_call_started",
                        tool_call=LLMToolCall(tool_call_id=call_id, name=name),
                    )
                return

            if event_type == "content_block_delta":
                index = payload.get("index")
                delta = payload.get("delta")
                if not isinstance(index, int) or not isinstance(delta, dict):
                    raise LLMProtocolError("The provider returned an invalid content delta.")
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta.get("text")
                    if not isinstance(text, str):
                        raise LLMProtocolError("The provider returned an invalid text delta.")
                    text_parts.append(text)
                    yield event(
                        "text_delta",
                        delta=text,
                        provider_request_id=provider_request_id,
                        returned_model=returned_model,
                    )
                elif delta_type == "input_json_delta":
                    accumulator = tools.get(index)
                    partial_json = delta.get("partial_json")
                    if accumulator is None or not isinstance(partial_json, str):
                        raise LLMProtocolError("The provider returned invalid tool arguments.")
                    accumulator.arguments_json += partial_json
                    yield event(
                        "tool_call_arguments_delta",
                        delta=partial_json,
                        tool_call=LLMToolCall(
                            tool_call_id=accumulator.tool_call_id,
                            name=accumulator.name,
                            arguments_json=accumulator.arguments_json,
                        ),
                    )
                return

            if event_type == "message_delta":
                delta = payload.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("stop_reason"), str):
                    finish_reason = delta["stop_reason"]
                usage = payload.get("usage")
                if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
                    usage_output = usage["output_tokens"]
                    yield event(
                        "usage",
                        usage=LLMUsage(
                            input_tokens=usage_input,
                            output_tokens=usage_output,
                            total_tokens=(
                                usage_input + usage_output
                                if usage_input is not None
                                else None
                            ),
                        ),
                        provider_request_id=provider_request_id,
                        returned_model=returned_model,
                    )
                return

            if event_type == "content_block_stop":
                index = payload.get("index")
                if isinstance(index, int) and index in tools:
                    for tool_event in self._complete_anthropic_tools(
                        {index: tools[index]}, event
                    ):
                        yield tool_event
                    del tools[index]
                return

            if event_type == "message_stop":
                for tool_event in self._complete_anthropic_tools(tools, event):
                    yield tool_event
                yield event(
                    "response_completed",
                    text="".join(text_parts),
                    provider_request_id=provider_request_id,
                    returned_model=returned_model,
                    finish_reason=finish_reason,
                )
                completed = True
                return

            if event_type == "error":
                raise LLMProviderError("The provider reported a failed response.")

        try:
            async with client.stream(
                "POST",
                self.endpoint,
                headers=headers,
                json=self._build_payload(request),
            ) as response:
                provider_request_id = self._header_request_id(response.headers)
                if response.status_code < 200 or response.status_code >= 300:
                    body = await self._read_bounded_error(response)
                    raise self._http_error(response, body, provider_request_id)
                async for line in response.aiter_lines():
                    total_response_bytes += len(line.encode("utf-8")) + 1
                    if total_response_bytes > self.settings.llm_max_response_bytes:
                        raise LLMProtocolError(
                            "The provider response exceeded the configured limit."
                        )
                    if line == "":
                        if event_data:
                            async for parsed_event in process_data("\n".join(event_data)):
                                yield parsed_event
                            event_data = []
                            event_data_bytes = 0
                        if completed:
                            break
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        value = line[5:].lstrip(" ")
                        event_data_bytes += len(value.encode("utf-8"))
                        if event_data_bytes > self.settings.llm_max_sse_event_bytes:
                            raise LLMProtocolError(
                                "A provider SSE event exceeded the configured limit."
                            )
                        event_data.append(value)
                if event_data and not completed:
                    async for parsed_event in process_data("\n".join(event_data)):
                        yield parsed_event
                if not completed and saw_payload and finish_reason is not None:
                    for tool_event in self._complete_anthropic_tools(tools, event):
                        yield tool_event
                    yield event(
                        "response_completed",
                        text="".join(text_parts),
                        provider_request_id=provider_request_id,
                        returned_model=returned_model,
                        finish_reason=finish_reason,
                    )
                    completed = True
                if not completed:
                    raise LLMProtocolError("The provider stream ended without a completion event.")
        except asyncio.CancelledError:
            raise
        except LLMError:
            raise
        except httpx.TimeoutException as error:
            from app.llm.errors import LLMTimeoutError

            raise LLMTimeoutError("The provider request timed out.") from error
        except httpx.RequestError as error:
            raise LLMProviderError("The provider could not be reached.") from error

    @staticmethod
    def _complete_anthropic_tools(
        tools: dict[int, _ToolAccumulator], event_factory
    ) -> list[LLMEvent]:
        events: list[LLMEvent] = []
        for accumulator in tools.values():
            raw_arguments = accumulator.arguments_json or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as error:
                raise LLMProtocolError("The provider returned malformed tool arguments.") from error
            if not isinstance(arguments, dict):
                raise LLMProtocolError("Tool arguments must decode to a JSON object.")
            events.append(
                event_factory(
                    "tool_call_completed",
                    tool_call=LLMToolCall(
                        tool_call_id=accumulator.tool_call_id,
                        name=accumulator.name,
                        arguments_json=raw_arguments,
                        arguments=arguments,
                    ),
                )
            )
        return events
