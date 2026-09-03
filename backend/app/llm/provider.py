from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.llm.types import LLMEvent, LLMProviderInfo, LLMRequest


class LLMProvider(Protocol):
    """Wire-provider contract used by the Phase 5 service."""

    async def initialize(self) -> LLMProviderInfo: ...

    async def stream(self, request: LLMRequest, *, attempt: int = 1) -> AsyncIterator[LLMEvent]: ...

    async def close(self) -> None: ...
