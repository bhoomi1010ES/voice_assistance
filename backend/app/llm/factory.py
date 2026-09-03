from __future__ import annotations

import httpx

from app.core.config import Settings
from app.llm.errors import LLMConfigurationError
from app.llm.provider import LLMProvider
from app.llm.providers.anthropic_messages import AnthropicMessagesProvider
from app.llm.providers.nvidia import NvidiaProvider
from app.llm.providers.openai_chat import OpenAIChatProvider
from app.llm.providers.openai_responses import OpenAIResponsesProvider


def create_llm_provider(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> LLMProvider:
    """Create exactly the configured provider without aliases or fallback."""

    if not settings.llm_configured or settings.llm_provider is None:
        raise LLMConfigurationError("The four primary LLM settings are not configured.")
    if settings.llm_provider == "nvidia":
        return NvidiaProvider(settings, client=client)
    if settings.llm_provider == "openai":
        return OpenAIResponsesProvider(settings, client=client)
    if settings.llm_provider == "openai_compatible":
        return OpenAIChatProvider(settings, client=client)
    if settings.llm_provider == "anthropic":
        return AnthropicMessagesProvider(settings, client=client)
    raise LLMConfigurationError("The configured LLM provider is unsupported.")
