from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.config import Settings
from app.llm.errors import (
    LLMContextLimitError,
    LLMInvalidRequestError,
    LLMModelNotFoundError,
    LLMOverloadedError,
    LLMPermissionError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.llm.providers.nvidia import NvidiaProvider
from app.llm.types import LLMMessage, LLMRequest, LLMRole


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        llm_provider="nvidia",
        llm_base_url="https://integrate.api.nvidia.com/v1",
        llm_api_key="test-placeholder-key",
        llm_model="nvidia/nemotron-3-super-120b-a12b",
        llm_max_retry_attempts=0,
    )


def _request() -> LLMRequest:
    return LLMRequest(
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        system_instructions="Answer briefly.",
        messages=(LLMMessage(role=LLMRole.USER, content="Hello"),),
        max_output_tokens=32,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, LLMInvalidRequestError),
        (403, LLMPermissionError),
        (404, LLMModelNotFoundError),
        (408, LLMTimeoutError),
        (413, LLMContextLimitError),
        (429, LLMRateLimitError),
        (502, LLMOverloadedError),
        (503, LLMOverloadedError),
        (500, "provider"),
    ],
)
async def test_provider_http_failures_map_to_safe_typed_errors(status, expected) -> None:
    headers = {"x-request-id": f"request-{status}"}
    if status in {429, 502, 503}:
        headers["retry-after"] = "3"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                headers=headers,
                json={"error": {"message": "secret provider body must not escape"}},
            )
        )
    )
    provider = NvidiaProvider(_settings(), client=client)
    await provider.initialize()

    with pytest.raises(Exception) as raised:
        _ = [event async for event in provider.stream(_request())]

    await client.aclose()
    error = raised.value
    if expected == "provider":
        assert error.code == "llm_provider_error"
    else:
        assert isinstance(error, expected)
    assert error.status_code == status
    assert error.request_id == f"request-{status}"
    assert "secret provider body" not in str(error)
    if status in {429, 502, 503}:
        assert error.retry_after_seconds == 3
