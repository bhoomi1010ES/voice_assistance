from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.core.config import Settings
from app.llm.errors import LLMConfigurationError, LLMError
from app.llm.factory import create_llm_provider
from app.llm.provider import LLMProvider
from app.llm.types import LLMEvent, LLMProviderInfo, LLMRequest

LOGGER = logging.getLogger("voice-assistance-backend")


class LLMService:
    """Own the selected provider lifecycle, retries, bounds, and cancellation."""

    _OUTPUT_EVENTS = {
        "text_delta",
        "tool_call_started",
        "tool_call_arguments_delta",
        "tool_call_completed",
    }

    def __init__(
        self,
        settings: Settings,
        *,
        provider: LLMProvider | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._provider = provider
        self._sleep = sleep
        self._provider_info: LLMProviderInfo | None = None
        self._initialized = False
        self._closed = False
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrent_requests)
        self._active_tasks: dict[uuid.UUID, asyncio.Task[Any]] = {}
        self._cancel_events: dict[uuid.UUID, asyncio.Event] = {}

    @property
    def enabled(self) -> bool:
        return self.settings.llm_configured

    @property
    def provider_info(self) -> LLMProviderInfo | None:
        return self._provider_info

    async def initialize(self) -> LLMProviderInfo | None:
        if self._initialized:
            return self._provider_info
        if not self.enabled:
            self._initialized = True
            return None
        if self._provider is None:
            self._provider = create_llm_provider(self.settings)
        self._provider_info = await self._provider.initialize()
        self._initialized = True
        LOGGER.info(
            "LLM provider initialized",
            extra={
                "event": "llm.provider.initialized",
                "provider": self._provider_info.provider,
                "provider_host": self._provider_info.host,
                "configured_model": self._provider_info.configured_model,
                "api_family": self._provider_info.api_family,
                "live_verified": self._provider_info.live_verified,
            },
        )
        return self._provider_info

    def readiness(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "status": "disabled"}
        if not self._initialized or self._provider_info is None:
            return {"enabled": True, "status": "not_ready"}
        return {
            "enabled": True,
            "status": "ready",
            "provider": self._provider_info.provider,
            "host": self._provider_info.host,
            "model": self._provider_info.configured_model,
            "api_family": self._provider_info.api_family,
            "live_verified": self._provider_info.live_verified,
            "capabilities": self._provider_info.capabilities.model_dump(),
        }

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        if not self._initialized:
            raise LLMConfigurationError("The LLM service was not initialized.")
        if not self.enabled or self._provider is None or self._provider_info is None:
            raise LLMConfigurationError("Phase 5 LLM is not configured.")
        if self._closed:
            raise LLMConfigurationError("The LLM service is closed.")
        if request.max_output_tokens > self.settings.llm_max_output_tokens:
            raise LLMConfigurationError(
                "The LLM output-token request exceeds its configured bound."
            )

        task = asyncio.current_task()
        if task is None:
            raise LLMConfigurationError("The LLM stream must run inside an asyncio task.")
        if request.response_id in self._active_tasks:
            raise LLMConfigurationError("The response already has an active LLM stream.")

        cancel_event = asyncio.Event()
        self._active_tasks[request.response_id] = task
        self._cancel_events[request.response_id] = cancel_event
        sequence = 0
        try:
            async with self._semaphore:
                for attempt in range(1, self.settings.llm_max_retry_attempts + 2):
                    emitted_output = False
                    try:
                        async for provider_event in self._provider.stream(
                            request,
                            attempt=attempt,
                        ):
                            if cancel_event.is_set():
                                return
                            emitted_output = emitted_output or (
                                provider_event.event_type in self._OUTPUT_EVENTS
                            )
                            yield provider_event.model_copy(
                                update={"sequence": sequence, "attempt": attempt}
                            )
                            sequence += 1
                        return
                    except asyncio.CancelledError:
                        raise
                    except LLMError as error:
                        can_retry = (
                            error.retryable
                            and not emitted_output
                            and attempt <= self.settings.llm_max_retry_attempts
                            and not cancel_event.is_set()
                        )
                        if can_retry:
                            await self._sleep(self._retry_delay(error, attempt))
                            continue
                        if cancel_event.is_set():
                            return
                        yield LLMEvent(
                            event_type="response_failed",
                            session_id=request.session_id,
                            turn_id=request.turn_id,
                            response_id=request.response_id,
                            provider=self._provider_info.provider,
                            configured_model=self._provider_info.configured_model,
                            monotonic_seconds=time.monotonic(),
                            sequence=sequence,
                            attempt=attempt,
                            provider_request_id=error.request_id,
                            error_code=error.code,
                            retryable=error.retryable,
                        )
                        return
        finally:
            if self._active_tasks.get(request.response_id) is task:
                self._active_tasks.pop(request.response_id, None)
                self._cancel_events.pop(request.response_id, None)

    async def cancel(self, response_id: uuid.UUID) -> bool:
        cancel_event = self._cancel_events.get(response_id)
        task = self._active_tasks.get(response_id)
        if cancel_event is None or task is None:
            return False
        cancel_event.set()
        if task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        current_task = asyncio.current_task()
        tasks = [task for task in set(self._active_tasks.values()) if task is not current_task]
        for event in self._cancel_events.values():
            event.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)
        if self._provider is not None:
            await self._provider.close()
        self._active_tasks.clear()
        self._cancel_events.clear()

    @staticmethod
    def _retry_delay(error: LLMError, attempt: int) -> float:
        if error.retry_after_seconds is not None:
            return max(0.0, min(error.retry_after_seconds, 10.0))
        return min(0.25 * (2 ** (attempt - 1)), 2.0)
