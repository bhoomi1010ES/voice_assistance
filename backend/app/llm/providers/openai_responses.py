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
    LLMNamedToolChoice,
    LLMRequest,
    LLMToolCall,
    LLMUsage,
)


class OpenAIResponsesProvider(OpenAIChatProvider):
    """Native OpenAI Responses streaming adapter."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(settings, provider_name="openai", client=client)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/responses"

    @property
    def api_family(self) -> str:
        return "openai_responses"

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
        input_items: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role.value == "tool":
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content,
                    }
                )
                continue
            if message.role.value == "assistant" and message.tool_calls:
                if message.content:
                    input_items.append(
                        {
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": message.content}],
                        }
                    )
                for tool_call in message.tool_calls:
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": tool_call.tool_call_id,
                            "name": tool_call.name,
                            "arguments": tool_call.arguments_json
                            or json.dumps(tool_call.arguments or {}, separators=(",", ":")),
                        }
                    )
                continue
            input_items.append(
                {
                    "role": message.role.value,
                    "content": [{"type": "input_text", "text": message.content}],
                }
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": request.system_instructions,
            "input": input_items,
            "max_output_tokens": request.max_output_tokens,
            "stream": True,
            "store": False,
        }
        if request.allowed_tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                }
                for tool in request.allowed_tools
            ]
            if isinstance(request.tool_choice, LLMNamedToolChoice):
                payload["tool_choice"] = {
                    "type": "function",
                    "name": request.tool_choice.function.name,
                }
            else:
                payload["tool_choice"] = request.tool_choice
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
            "Authorization": f"Bearer {self._api_key()}",
        }
        provider_request_id: str | None = None
        returned_model: str | None = None
        finish_reason: str | None = None
        text_parts: list[str] = []
        tools: dict[str, _ToolAccumulator] = {}
        tool_item_ids: dict[str, str] = {}
        tool_indexes: dict[int, str] = {}
        event_data: list[str] = []
        event_data_bytes = 0
        total_response_bytes = 0
        completed = False
        saw_payload = False

        async def process_data(data: str) -> AsyncIterator[LLMEvent]:
            nonlocal completed, saw_payload, provider_request_id, returned_model, finish_reason
            if not data:
                return
            if data == "[DONE]":
                for tool_event in self._complete_responses_tools(tools, event):
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
            container = payload.get("response")
            if not isinstance(container, dict):
                container = payload
            value = container.get("id")
            if isinstance(value, str) and value:
                provider_request_id = provider_request_id or value
            value = container.get("model")
            if isinstance(value, str) and value:
                returned_model = value

            if event_type == "response.output_text.delta":
                delta = payload.get("delta")
                if not isinstance(delta, str):
                    raise LLMProtocolError("The provider returned an invalid text delta.")
                text_parts.append(delta)
                yield event(
                    "text_delta",
                    delta=delta,
                    provider_request_id=provider_request_id,
                    returned_model=returned_model,
                )
                return

            if event_type == "response.output_item.added":
                item = payload.get("item")
                if not isinstance(item, dict) or item.get("type") != "function_call":
                    return
                call_id = item.get("call_id") or item.get("id")
                name = item.get("name")
                if not isinstance(call_id, str) or not call_id:
                    raise LLMProtocolError("The provider returned a function call without an ID.")
                if not isinstance(name, str) or not name:
                    raise LLMProtocolError("The provider returned a function call without a name.")
                item_id = item.get("id")
                if isinstance(item_id, str) and item_id:
                    tool_item_ids[item_id] = call_id
                index = payload.get("output_index")
                if isinstance(index, int):
                    tool_indexes[index] = call_id
                tools[call_id] = _ToolAccumulator(tool_call_id=call_id, name=name)
                yield event(
                    "tool_call_started",
                    tool_call=LLMToolCall(tool_call_id=call_id, name=name),
                )
                tools[call_id].started = True
                return

            if event_type == "response.function_call_arguments.delta":
                call_id = payload.get("item_id") or payload.get("call_id")
                if isinstance(call_id, str):
                    call_id = tool_item_ids.get(call_id, call_id)
                if not isinstance(call_id, str):
                    index = payload.get("output_index")
                    call_id = tool_indexes.get(index) if isinstance(index, int) else None
                if not isinstance(call_id, str) or call_id not in tools:
                    raise LLMProtocolError("The provider returned arguments for an unknown call.")
                delta = payload.get("delta")
                if not isinstance(delta, str):
                    raise LLMProtocolError("The provider returned invalid function arguments.")
                accumulator = tools[call_id]
                accumulator.arguments_json += delta
                yield event(
                    "tool_call_arguments_delta",
                    delta=delta,
                    tool_call=LLMToolCall(
                        tool_call_id=call_id,
                        name=accumulator.name,
                        arguments_json=accumulator.arguments_json,
                    ),
                )
                return

            if event_type == "response.function_call_arguments.done":
                call_id = payload.get("item_id") or payload.get("call_id")
                if isinstance(call_id, str):
                    call_id = tool_item_ids.get(call_id, call_id)
                if not isinstance(call_id, str) or call_id not in tools:
                    raise LLMProtocolError("The provider completed an unknown function call.")
                arguments = payload.get("arguments")
                if isinstance(arguments, str):
                    tools[call_id].arguments_json = arguments
                for tool_event in self._complete_responses_tools(
                    {call_id: tools[call_id]}, event
                ):
                    yield tool_event
                del tools[call_id]
                return

            if event_type == "response.completed":
                usage = self._parse_responses_usage(container.get("usage"))
                if usage is not None:
                    yield event(
                        "usage",
                        usage=usage,
                        provider_request_id=provider_request_id,
                        returned_model=returned_model,
                    )
                status = container.get("status")
                if isinstance(status, str):
                    finish_reason = status
                for tool_event in self._complete_responses_tools(tools, event):
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

            if event_type == "response.failed":
                raise LLMProviderError("The provider reported a failed response.")
            if event_type == "response.incomplete":
                raise LLMError("The provider returned an incomplete response.")

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
                    for tool_event in self._complete_responses_tools(tools, event):
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
    def _parse_responses_usage(value: Any) -> LLMUsage | None:
        if not isinstance(value, dict):
            return None
        values = {
            "input_tokens": value.get("input_tokens"),
            "output_tokens": value.get("output_tokens"),
            "total_tokens": value.get("total_tokens"),
        }
        if not any(isinstance(item, int) for item in values.values()):
            return None
        return LLMUsage(
            **{key: item if isinstance(item, int) else None for key, item in values.items()}
        )

    @staticmethod
    def _complete_responses_tools(
        tools: dict[str, _ToolAccumulator], event_factory
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
