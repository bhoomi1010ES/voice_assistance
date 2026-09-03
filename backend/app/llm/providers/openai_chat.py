from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.llm.errors import (
    LLMAuthenticationError,
    LLMContextLimitError,
    LLMError,
    LLMInvalidRequestError,
    LLMModelNotFoundError,
    LLMOverloadedError,
    LLMPermissionError,
    LLMProtocolError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.llm.types import (
    LLMCapabilities,
    LLMEvent,
    LLMEventType,
    LLMNamedToolChoice,
    LLMProviderInfo,
    LLMRequest,
    LLMRole,
    LLMToolCall,
    LLMUsage,
)


@dataclass
class _ToolAccumulator:
    tool_call_id: str = ""
    name: str = ""
    arguments_json: str = ""
    started: bool = False
    emitted_argument_characters: int = 0


class OpenAIChatProvider:
    """Conservative OpenAI-compatible Chat Completions streaming transport."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider_name: str = "openai_compatible",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.provider_name = provider_name
        self.base_url = settings.llm_base_url_resolved
        self.model = (settings.llm_model or "").strip()
        self._client = client
        self._owns_client = client is None
        self._initialized = False

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def capabilities(self) -> LLMCapabilities:
        return LLMCapabilities(
            streaming=True,
            text_generation=True,
            tool_calling=True,
            usage_reporting=True,
            cancellation=True,
        )

    @property
    def api_family(self) -> str:
        return "openai_chat_completions"

    async def initialize(self) -> LLMProviderInfo:
        self._ensure_client()
        self._initialized = True
        parsed = urlsplit(self.base_url)
        return LLMProviderInfo(
            provider=self.provider_name,
            api_family=self.api_family,
            host=f"{parsed.scheme}://{parsed.netloc}",
            configured_model=self.model,
            capabilities=self.capabilities,
            live_verified=False,
        )

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

        payload = self._build_payload(request)
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key()}",
        }
        provider_request_id: str | None = None
        returned_model: str | None = None
        finish_reason: str | None = None
        full_text: list[str] = []
        tools: dict[int, _ToolAccumulator] = {}
        total_response_bytes = 0
        event_data: list[str] = []
        event_data_bytes = 0
        saw_payload = False
        completed = False

        async def process_data(data: str) -> AsyncIterator[LLMEvent]:
            nonlocal completed, finish_reason, provider_request_id, returned_model, saw_payload
            if not data:
                return
            if data == "[DONE]":
                for tool_event in self._complete_tool_calls(tools, event):
                    yield tool_event
                yield event(
                    "response_completed",
                    text="".join(full_text),
                    provider_request_id=provider_request_id,
                    returned_model=returned_model,
                    finish_reason=finish_reason,
                )
                completed = True
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError as error:
                raise LLMProtocolError("The provider returned malformed SSE JSON.") from error
            if not isinstance(chunk, dict):
                raise LLMProtocolError("The provider returned a non-object SSE payload.")
            if chunk.get("error") is not None:
                raise LLMProviderError("The provider returned an in-stream error.")

            saw_payload = True
            chunk_request_id = chunk.get("id")
            if isinstance(chunk_request_id, str) and chunk_request_id:
                provider_request_id = provider_request_id or chunk_request_id
            chunk_model = chunk.get("model")
            if isinstance(chunk_model, str) and chunk_model:
                returned_model = chunk_model

            usage = self._parse_usage(chunk.get("usage"))
            if usage is not None:
                yield event(
                    "usage",
                    usage=usage,
                    provider_request_id=provider_request_id,
                    returned_model=returned_model,
                )

            choices = chunk.get("choices")
            if choices is None:
                return
            if not isinstance(choices, list):
                raise LLMProtocolError("The provider returned invalid Chat Completions choices.")
            for choice in choices:
                if not isinstance(choice, dict):
                    raise LLMProtocolError("The provider returned an invalid choice object.")
                choice_finish = choice.get("finish_reason")
                if isinstance(choice_finish, str):
                    finish_reason = choice_finish
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    raise LLMProtocolError("The provider returned an invalid choice delta.")

                text_delta = self._extract_text(delta.get("content"))
                if text_delta:
                    full_text.append(text_delta)
                    yield event(
                        "text_delta",
                        delta=text_delta,
                        provider_request_id=provider_request_id,
                        returned_model=returned_model,
                    )

                tool_deltas = delta.get("tool_calls")
                if tool_deltas is not None:
                    if not isinstance(tool_deltas, list):
                        raise LLMProtocolError("The provider returned invalid tool-call deltas.")
                    for tool_delta in tool_deltas:
                        for tool_event in self._apply_tool_delta(tools, tool_delta, event):
                            yield tool_event

        try:
            async with client.stream(
                "POST",
                self.endpoint,
                headers=headers,
                json=payload,
            ) as response:
                provider_request_id = self._header_request_id(response.headers)
                if response.status_code < 200 or response.status_code >= 300:
                    body = await self._read_bounded_error(response)
                    raise self._http_error(response, body, provider_request_id)

                async for line in response.aiter_lines():
                    line_bytes = len(line.encode("utf-8")) + 1
                    total_response_bytes += line_bytes
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
                    for tool_event in self._complete_tool_calls(tools, event):
                        yield tool_event
                    yield event(
                        "response_completed",
                        text="".join(full_text),
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
            raise LLMTimeoutError("The provider request timed out.") from error
        except httpx.RequestError as error:
            raise LLMProviderError("The provider could not be reached.") from error

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None
        self._initialized = False

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(
                connect=self.settings.llm_connect_timeout_seconds,
                read=self.settings.llm_request_timeout_seconds,
                write=self.settings.llm_request_timeout_seconds,
                pool=self.settings.llm_connect_timeout_seconds,
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=self.settings.llm_max_concurrent_requests,
                    max_keepalive_connections=self.settings.llm_max_concurrent_requests,
                ),
            )
        return self._client

    def _api_key(self) -> str:
        if self.settings.llm_api_key is None:
            raise LLMAuthenticationError("LLM_API_KEY is not configured.")
        return self.settings.llm_api_key.get_secret_value()

    def _build_payload(self, request: LLMRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": request.system_instructions}
        ]
        for message in request.messages:
            mapped: dict[str, Any] = {"role": message.role.value, "content": message.content}
            if message.role == LLMRole.TOOL:
                mapped["tool_call_id"] = message.tool_call_id
            elif message.role == LLMRole.ASSISTANT and message.tool_calls:
                mapped["tool_calls"] = [
                    {
                        "id": tool_call.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": tool_call.arguments_json or json.dumps(
                                tool_call.arguments or {},
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for tool_call in message.tool_calls
                ]
            messages.append(mapped)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "stream": True,
        }
        if request.allowed_tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.allowed_tools
            ]
            payload["tool_choice"] = (
                request.tool_choice.model_dump(mode="json")
                if isinstance(request.tool_choice, LLMNamedToolChoice)
                else request.tool_choice
            )
        payload.update(self._provider_request_options(request))
        return payload

    def _provider_request_options(self, request: LLMRequest) -> dict[str, Any]:
        return {}

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)

    @staticmethod
    def _parse_usage(value: Any) -> LLMUsage | None:
        if not isinstance(value, dict):
            return None
        input_tokens = value.get("prompt_tokens")
        output_tokens = value.get("completion_tokens")
        total_tokens = value.get("total_tokens")
        if not any(isinstance(item, int) for item in (input_tokens, output_tokens, total_tokens)):
            return None
        return LLMUsage(
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            total_tokens=total_tokens if isinstance(total_tokens, int) else None,
        )

    @staticmethod
    def _apply_tool_delta(
        tools: dict[int, _ToolAccumulator],
        value: Any,
        event_factory,
    ) -> list[LLMEvent]:
        if not isinstance(value, dict):
            raise LLMProtocolError("The provider returned an invalid tool-call delta.")
        index = value.get("index", 0)
        if not isinstance(index, int) or index < 0:
            raise LLMProtocolError("The provider returned an invalid tool-call index.")
        accumulator = tools.setdefault(index, _ToolAccumulator())
        tool_call_id = value.get("id")
        if isinstance(tool_call_id, str):
            accumulator.tool_call_id += tool_call_id
        function = value.get("function") or {}
        if not isinstance(function, dict):
            raise LLMProtocolError("The provider returned an invalid tool function.")
        name = function.get("name")
        if isinstance(name, str):
            accumulator.name += name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            accumulator.arguments_json += arguments

        events: list[LLMEvent] = []
        if (
            not accumulator.started
            and accumulator.tool_call_id
            and accumulator.name
            and accumulator.arguments_json
        ):
            accumulator.started = True
            events.append(
                event_factory(
                    "tool_call_started",
                    tool_call=LLMToolCall(
                        tool_call_id=accumulator.tool_call_id,
                        name=accumulator.name,
                    ),
                )
            )
        if accumulator.started and (
            len(accumulator.arguments_json) > accumulator.emitted_argument_characters
        ):
            fragment = accumulator.arguments_json[accumulator.emitted_argument_characters :]
            accumulator.emitted_argument_characters = len(accumulator.arguments_json)
            events.append(
                event_factory(
                    "tool_call_arguments_delta",
                    delta=fragment,
                    tool_call=LLMToolCall(
                        tool_call_id=accumulator.tool_call_id,
                        name=accumulator.name,
                        arguments_json=accumulator.arguments_json,
                    ),
                )
            )
        return events

    @staticmethod
    def _complete_tool_calls(
        tools: dict[int, _ToolAccumulator],
        event_factory,
    ) -> list[LLMEvent]:
        events: list[LLMEvent] = []
        for index in sorted(tools):
            accumulator = tools[index]
            if not accumulator.tool_call_id or not accumulator.name:
                raise LLMProtocolError("The provider returned an incomplete tool call.")
            if not accumulator.started:
                accumulator.started = True
                events.append(
                    event_factory(
                        "tool_call_started",
                        tool_call=LLMToolCall(
                            tool_call_id=accumulator.tool_call_id,
                            name=accumulator.name,
                        ),
                    )
                )
            if len(accumulator.arguments_json) > accumulator.emitted_argument_characters:
                fragment = accumulator.arguments_json[accumulator.emitted_argument_characters :]
                accumulator.emitted_argument_characters = len(accumulator.arguments_json)
                events.append(
                    event_factory(
                        "tool_call_arguments_delta",
                        delta=fragment,
                        tool_call=LLMToolCall(
                            tool_call_id=accumulator.tool_call_id,
                            name=accumulator.name,
                            arguments_json=accumulator.arguments_json,
                        ),
                    )
                )
            raw_arguments = accumulator.arguments_json or "{}"
            try:
                parsed_arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as error:
                raise LLMProtocolError("The provider returned malformed tool arguments.") from error
            if not isinstance(parsed_arguments, dict):
                raise LLMProtocolError("Tool arguments must decode to a JSON object.")
            events.append(
                event_factory(
                    "tool_call_completed",
                    tool_call=LLMToolCall(
                        tool_call_id=accumulator.tool_call_id,
                        name=accumulator.name,
                        arguments_json=raw_arguments,
                        arguments=parsed_arguments,
                    ),
                )
            )
        return events

    async def _read_bounded_error(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self.settings.llm_max_sse_event_bytes:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _header_request_id(headers: httpx.Headers) -> str | None:
        for name in ("x-request-id", "request-id", "x-nv-request-id"):
            value = headers.get(name)
            if value:
                return value[:256]
        return None

    @staticmethod
    def _retry_after(headers: httpx.Headers) -> float | None:
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, min(float(value), 60.0))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
            return max(0.0, min(seconds, 60.0))

    @classmethod
    def _http_error(
        cls,
        response: httpx.Response,
        body: bytes,
        request_id: str | None,
    ) -> LLMError:
        status = response.status_code
        common = {"status_code": status, "request_id": request_id}
        lowered = body[:8192].lower()
        if status == 400 and any(
            marker in lowered
            for marker in (b"context length", b"context_length", b"too many tokens")
        ):
            return LLMContextLimitError("The request exceeded the model context limit.", **common)
        if status == 400 or status == 422:
            return LLMInvalidRequestError("The provider rejected the request.", **common)
        if status == 401:
            return LLMAuthenticationError("The provider rejected authentication.", **common)
        if status == 403:
            return LLMPermissionError("The provider rejected model access.", **common)
        if status == 404:
            return LLMModelNotFoundError(
                "The configured model or endpoint was not found.",
                **common,
            )
        if status == 408:
            return LLMTimeoutError("The provider request timed out.", **common)
        if status == 413:
            return LLMContextLimitError("The request exceeded the provider limit.", **common)
        if status == 429:
            return LLMRateLimitError(
                "The provider rate limit was reached.",
                retry_after_seconds=cls._retry_after(response.headers),
                **common,
            )
        if status in {502, 503, 504, 529}:
            return LLMOverloadedError(
                "The provider is temporarily overloaded.",
                retry_after_seconds=cls._retry_after(response.headers),
                **common,
            )
        if status >= 500:
            return LLMProviderError("The provider returned a server error.", **common)
        return LLMInvalidRequestError("The provider rejected the request.", **common)
