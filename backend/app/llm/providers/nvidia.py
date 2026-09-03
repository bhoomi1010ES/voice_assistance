from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings
from app.llm.providers.openai_chat import OpenAIChatProvider
from app.llm.types import LLMCapabilities, LLMRequest


class NvidiaProvider(OpenAIChatProvider):
    """NVIDIA policy profile over the shared Chat Completions transport."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(settings, provider_name="nvidia", client=client)

    @property
    def capabilities(self) -> LLMCapabilities:
        return LLMCapabilities(
            streaming=True,
            text_generation=True,
            tool_calling=True,
            parallel_tool_calling=False,
            strict_tool_schema=False,
            structured_text_output=False,
            usage_reporting=True,
            model_listing=False,
            cancellation=True,
        )

    def _provider_request_options(self, request: LLMRequest) -> dict[str, Any]:
        # These are the current documented defaults for Nemotron 3 Super.
        return {"temperature": 1.0, "top_p": 0.95}
