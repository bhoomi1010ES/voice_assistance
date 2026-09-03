from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.llm.errors import (
    LLMContextLimitError,
    LLMToolArgumentsError,
    LLMToolAuthorizationError,
    LLMToolError,
    LLMToolLoopLimitError,
)
from app.llm.service import LLMService
from app.llm.types import (
    LLMEvent,
    LLMMessage,
    LLMRequest,
    LLMRole,
    LLMToolCall,
    LLMToolDefinition,
)

ToolHandler = Callable[["ToolExecutionContext", BaseModel], Awaitable[Any]]
ToolArgumentNormalizer = Callable[["ToolExecutionContext", BaseModel], BaseModel]
IdempotencyKey = tuple[uuid.UUID, uuid.UUID, str, str]


@dataclass(frozen=True)
class ToolExecutionContext:
    """Server-owned identity and policy context for one tool invocation."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    response_id: uuid.UUID
    scopes: frozenset[str] = frozenset()
    confirmed_tool_call_ids: frozenset[str] = frozenset()
    confirmation_expires_at_monotonic: float | None = None
    confirmation_check: Callable[[LLMToolCall], bool] | None = None
    confirmation_requested: (
        Callable[[LLMToolCall, BaseModel, RegisteredTool], Awaitable[bool]] | None
    ) = None
    db: AsyncSession | None = None
    clock: Clock = field(default_factory=SystemClock)
    user_timezone: str = "UTC"
    source_transcript: str | None = None
    cancellation_check: Callable[[], bool] | None = None
    tool_execution_started: Callable[[LLMToolCall, float], None] | None = None
    tool_execution_finished: Callable[[LLMToolCall, float], None] | None = None


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: ToolHandler
    argument_normalizer: ToolArgumentNormalizer | None
    required_scopes: frozenset[str]
    read_only: bool
    requires_confirmation: bool
    max_calls_per_turn: int

    @property
    def definition(self) -> LLMToolDefinition:
        return LLMToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.arguments_model.model_json_schema(),
        )


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_call_id: str
    name: str
    content: str
    success: bool
    executed: bool
    replayed: bool = False
    error_code: str | None = None


class ToolIdempotencyStore(Protocol):
    async def claim(self, key: IdempotencyKey) -> ToolIdempotencyClaim: ...

    async def get(self, key: IdempotencyKey) -> str | None: ...

    async def put(self, key: IdempotencyKey, content: str) -> None: ...

    async def release(self, key: IdempotencyKey) -> None: ...


@dataclass(frozen=True)
class ToolIdempotencyClaim:
    acquired: bool
    cached_content: str | None = None


class InMemoryToolIdempotencyStore:
    """Small scoped store for tests and read-only diagnostics.

    Mutating production tools must provide a durable implementation of the
    same protocol. This store intentionally has no persistence guarantees.
    """

    def __init__(self) -> None:
        self._values: dict[IdempotencyKey, str | None] = {}
        self._lock = asyncio.Lock()

    async def claim(self, key: IdempotencyKey) -> ToolIdempotencyClaim:
        async with self._lock:
            if key not in self._values:
                self._values[key] = None
                return ToolIdempotencyClaim(acquired=True)
            value = self._values[key]
            return ToolIdempotencyClaim(
                acquired=False,
                cached_content=value,
            )

    async def get(self, key: IdempotencyKey) -> str | None:
        async with self._lock:
            return self._values.get(key)

    async def put(self, key: IdempotencyKey, content: str) -> None:
        async with self._lock:
            self._values[key] = content

    async def release(self, key: IdempotencyKey) -> None:
        async with self._lock:
            if self._values.get(key) is None:
                self._values.pop(key, None)


class ToolRegistry:
    """Registry of server-owned tools exposed to the selected LLM provider."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        arguments_model: type[BaseModel],
        handler: ToolHandler,
        argument_normalizer: ToolArgumentNormalizer | None = None,
        required_scopes: frozenset[str] = frozenset(),
        read_only: bool = True,
        requires_confirmation: bool = False,
        max_calls_per_turn: int = 4,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        if not read_only and not requires_confirmation:
            raise ValueError("Mutating tools must require confirmation")
        if max_calls_per_turn < 1:
            raise ValueError("max_calls_per_turn must be positive")
        self._tools[name] = RegisteredTool(
            name=name,
            description=description,
            arguments_model=arguments_model,
            handler=handler,
            argument_normalizer=argument_normalizer,
            required_scopes=required_scopes,
            read_only=read_only,
            requires_confirmation=requires_confirmation,
            max_calls_per_turn=max_calls_per_turn,
        )

    def definitions(self) -> tuple[LLMToolDefinition, ...]:
        return tuple(self._tools[name].definition for name in sorted(self._tools))

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))


class ToolExecutor:
    """Validate, authorize, rate-limit, and dispatch registered tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        idempotency_store: ToolIdempotencyStore | None = None,
        max_result_chars: int = 16_384,
    ) -> None:
        self.registry = registry
        self.idempotency_store = idempotency_store
        self.max_result_chars = max_result_chars
        self._turn_call_counts: dict[tuple[uuid.UUID, uuid.UUID, str], int] = {}
        self._turn_call_lock = asyncio.Lock()
        self._idempotency_locks: dict[IdempotencyKey, asyncio.Lock] = {}

    async def execute(
        self,
        call: LLMToolCall,
        *,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        tool = self.registry.get(call.name)
        if tool is None:
            return self._failure(call, "llm_tool_not_registered")
        if context.cancellation_check is not None and context.cancellation_check():
            return self._failure(call, "llm_cancelled")

        arguments = call.arguments
        if arguments is None:
            try:
                decoded = json.loads(call.arguments_json or "{}")
            except json.JSONDecodeError:
                return self._failure(call, LLMToolArgumentsError.code)
            if not isinstance(decoded, dict):
                return self._failure(call, LLMToolArgumentsError.code)
            arguments = decoded
        try:
            validated_arguments = tool.arguments_model.model_validate(arguments)
        except ValidationError:
            return self._failure(call, LLMToolArgumentsError.code)
        if tool.argument_normalizer is not None:
            try:
                validated_arguments = tool.argument_normalizer(context, validated_arguments)
            except LLMToolError as error:
                return self._failure(call, error.code)
            except (TypeError, ValueError, ValidationError):
                return self._failure(call, LLMToolArgumentsError.code)

        if not tool.required_scopes.issubset(context.scopes):
            return self._failure(call, LLMToolAuthorizationError.code)
        confirmed = call.tool_call_id in context.confirmed_tool_call_ids
        if context.confirmation_check is not None:
            confirmed = confirmed or context.confirmation_check(call)
        if tool.requires_confirmation and not confirmed:
            if context.confirmation_requested is not None:
                try:
                    confirmation_saved = await context.confirmation_requested(
                        call,
                        validated_arguments,
                        tool,
                    )
                except Exception:  # noqa: BLE001 - confirmation failures stay model-visible
                    return self._failure(call, "llm_tool_confirmation_unavailable")
                if not confirmation_saved:
                    return self._failure(call, "llm_tool_confirmation_unavailable")
            return self._failure(call, "llm_tool_confirmation_required")
        if (
            tool.requires_confirmation
            and context.confirmation_expires_at_monotonic is not None
            and time.monotonic() > context.confirmation_expires_at_monotonic
        ):
            return self._failure(call, "llm_tool_confirmation_expired")
        if context.cancellation_check is not None and context.cancellation_check():
            return self._failure(call, "llm_cancelled")
        key = (context.user_id, context.turn_id, call.name, call.tool_call_id)
        if tool.read_only:
            if not await self._within_turn_rate_limit(tool, context):
                return self._failure(call, "llm_tool_rate_limited")
            return await self._invoke(tool, call, context, validated_arguments)
        if self.idempotency_store is None:
            return self._failure(call, "llm_tool_idempotency_unavailable")

        lock = self._idempotency_locks.setdefault(key, asyncio.Lock())
        async with lock:
            claim = await self._claim_idempotency(key)
            if claim.cached_content is not None:
                return ToolExecutionResult(
                    tool_call_id=call.tool_call_id,
                    name=call.name,
                    content=claim.cached_content,
                    success=True,
                    executed=False,
                    replayed=True,
                )
            if not claim.acquired:
                return self._failure(call, "llm_tool_in_progress")
            if not await self._within_turn_rate_limit(tool, context):
                await self._release_idempotency(key)
                return self._failure(call, "llm_tool_rate_limited")
            result = await self._invoke(tool, call, context, validated_arguments)
            if result.success:
                await self.idempotency_store.put(key, result.content)
            else:
                await self._release_idempotency(key)
            return result

    async def _claim_idempotency(self, key: IdempotencyKey) -> ToolIdempotencyClaim:
        claim_method = getattr(self.idempotency_store, "claim", None)
        if claim_method is not None:
            return await claim_method(key)
        cached = await self.idempotency_store.get(key)
        return ToolIdempotencyClaim(acquired=cached is None, cached_content=cached)

    async def _release_idempotency(self, key: IdempotencyKey) -> None:
        release_method = getattr(self.idempotency_store, "release", None)
        if release_method is not None:
            await release_method(key)

    async def _within_turn_rate_limit(
        self,
        tool: RegisteredTool,
        context: ToolExecutionContext,
    ) -> bool:
        key = (context.user_id, context.turn_id, tool.name)
        async with self._turn_call_lock:
            count = self._turn_call_counts.get(key, 0)
            if count >= tool.max_calls_per_turn:
                return False
            self._turn_call_counts[key] = count + 1
            return True

    async def _invoke(
        self,
        tool: RegisteredTool,
        call: LLMToolCall,
        context: ToolExecutionContext,
        arguments: BaseModel,
    ) -> ToolExecutionResult:
        started = time.monotonic()
        if context.tool_execution_started is not None:
            context.tool_execution_started(call, started)
        try:
            if context.cancellation_check is not None and context.cancellation_check():
                return self._failure(call, "llm_cancelled")
            value = await tool.handler(context, arguments)
            content = self._serialize_result(value)
        except LLMToolError as error:
            return self._failure(call, error.code)
        except Exception:  # noqa: BLE001 - tool failures become model-visible results
            return self._failure(call, "llm_tool_execution_failed")
        finally:
            if context.tool_execution_finished is not None:
                context.tool_execution_finished(call, time.monotonic())
        if len(content) > self.max_result_chars:
            return self._failure(call, "llm_tool_result_too_large")
        return ToolExecutionResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            content=content,
            success=True,
            executed=True,
        )

    @staticmethod
    def _serialize_result(value: Any) -> str:
        if isinstance(value, str):
            result: Any = value
        elif isinstance(value, BaseModel):
            result = value.model_dump(mode="json")
        else:
            result = value
        return json.dumps(
            {"ok": True, "result": result},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _failure(call: LLMToolCall, code: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id=call.tool_call_id,
            name=call.name,
            content=json.dumps(
                {"ok": False, "error": {"code": code}},
                separators=(",", ":"),
            ),
            success=False,
            executed=False,
            error_code=code,
        )


class LLMToolLoop:
    """Run bounded sequential provider-neutral tool rounds."""

    def __init__(
        self,
        settings: Settings,
        llm_service: LLMService,
        registry: ToolRegistry,
        *,
        idempotency_store: ToolIdempotencyStore | None = None,
    ) -> None:
        self.settings = settings
        self.llm_service = llm_service
        self.registry = registry
        self.executor = ToolExecutor(
            registry,
            idempotency_store=idempotency_store,
            max_result_chars=settings.llm_max_tool_result_chars,
        )

    async def stream(
        self,
        request: LLMRequest,
        *,
        context: ToolExecutionContext,
    ) -> AsyncIterator[LLMEvent]:
        current_request = request
        started = time.monotonic()
        total_tool_calls = 0
        for round_number in range(self.settings.llm_max_tool_rounds + 1):
            if time.monotonic() - started > self.settings.llm_max_tool_wall_time_seconds:
                raise LLMToolLoopLimitError("The tool loop exceeded its wall-time bound.")
            round_events: list[LLMEvent] = []
            completed_calls: list[LLMToolCall] = []
            async for event in self.llm_service.stream(current_request):
                round_events.append(event)
                if event.event_type == "tool_call_completed" and event.tool_call is not None:
                    completed_calls.append(event.tool_call)

            if not completed_calls:
                for buffered_event in round_events:
                    yield buffered_event
                return
            if round_number >= self.settings.llm_max_tool_rounds:
                raise LLMToolLoopLimitError("The tool loop exceeded its round bound.")
            total_tool_calls += len(completed_calls)
            if total_tool_calls > self.settings.llm_max_tool_calls:
                raise LLMToolLoopLimitError("The tool loop exceeded its tool-call bound.")

            # Suppress model text and the intermediate terminal event while a
            # tool call is pending; only confirmed final text reaches callers.
            for event in round_events:
                if event.event_type in {
                    "text_delta",
                    "response_completed",
                }:
                    continue
                yield event

            next_messages = list(current_request.messages)
            next_messages.append(
                LLMMessage(
                    role=LLMRole.ASSISTANT,
                    content="",
                    tool_calls=tuple(completed_calls),
                )
            )
            execution_results: list[ToolExecutionResult] = []
            for call in completed_calls:
                result = await self.executor.execute(call, context=context)
                execution_results.append(result)
                next_messages.append(
                    LLMMessage(
                        role=LLMRole.TOOL,
                        content=result.content,
                        tool_call_id=result.tool_call_id,
                    )
                )
            if any(
                result.error_code == "llm_tool_confirmation_required"
                for result in execution_results
            ):
                # A confirmation request is a terminal orchestration state. Do
                # not send its tool-result messages back to the provider, since
                # a model may otherwise keep proposing the same mutation until
                # the generic loop bound is reached.
                provider_info = self.llm_service.provider_info
                if provider_info is None:
                    raise LLMToolLoopLimitError(
                        "Confirmation was requested without provider metadata."
                    )
                last_event = round_events[-1] if round_events else None
                confirmation_call = next(
                    call
                    for call, result in zip(completed_calls, execution_results, strict=True)
                    if result.error_code == "llm_tool_confirmation_required"
                )
                yield LLMEvent(
                    event_type="confirmation_required",
                    session_id=current_request.session_id,
                    turn_id=current_request.turn_id,
                    response_id=current_request.response_id,
                    provider=provider_info.provider,
                    configured_model=provider_info.configured_model,
                    monotonic_seconds=time.monotonic(),
                    sequence=(last_event.sequence + 1 if last_event is not None else 0),
                    attempt=(last_event.attempt if last_event is not None else 1),
                    tool_call=confirmation_call,
                    error_code="llm_tool_confirmation_required",
                )
                return
            current_request = current_request.model_copy(
                update={"messages": tuple(next_messages)}
            )
            self._enforce_context_bound(current_request)

        raise LLMToolLoopLimitError("The tool loop did not reach a terminal response.")

    def _enforce_context_bound(self, request: LLMRequest) -> None:
        characters = len(request.system_instructions) + sum(
            len(message.content)
            + sum(len(call.arguments_json) + len(call.name) for call in message.tool_calls)
            for message in request.messages
        )
        if characters > self.settings.llm_max_context_tokens * 4:
            raise LLMContextLimitError("The tool loop exceeded the configured context bound.")


class EmptyToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _current_time(_context: ToolExecutionContext, _arguments: BaseModel) -> dict[str, str]:
    return {"utc": _context.clock.now_utc().isoformat()}


def create_default_tool_registry() -> ToolRegistry:
    """Return the server-owned diagnostic and confirmed task tool set."""

    registry = ToolRegistry()
    registry.register(
        name="get_current_time",
        description="Return the current UTC time. This is read-only.",
        arguments_model=EmptyToolArguments,
        handler=_current_time,
        read_only=True,
    )
    from app.llm.task_tools import register_task_tools

    register_task_tools(registry)
    return registry
