from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings


class MemoryProviderError(RuntimeError):
    """Safe, provider-neutral error from an embedding or reranker service."""

    def __init__(self, code: str, message: str = "Memory model service unavailable") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EmbeddingResponse:
    model: str
    vectors: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


def _headers(settings: Settings) -> dict[str, str]:
    if settings.stt_api_key is None or not settings.stt_api_key.get_secret_value().strip():
        raise MemoryProviderError(
            "memory_auth_not_configured",
            "Memory model service is not configured",
        )
    return {
        settings.stt_api_auth_header: (
            f"{settings.stt_api_auth_scheme} {settings.stt_api_key.get_secret_value()}"
        ),
        "Accept": "application/json",
    }


def _json(response: httpx.Response, max_bytes: int) -> dict[str, Any]:
    try:
        content = response.content
    except httpx.HTTPError as error:
        raise MemoryProviderError("memory_response_invalid") from error
    if len(content) > max_bytes:
        raise MemoryProviderError("memory_response_too_large")
    try:
        payload = response.json()
    except ValueError as error:
        raise MemoryProviderError("memory_response_invalid") from error
    if not isinstance(payload, dict):
        raise MemoryProviderError("memory_response_invalid")
    return payload


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    if response.status_code in {401, 403}:
        code = "memory_provider_auth_failed"
    elif response.status_code == 429:
        code = "memory_provider_rate_limited"
    elif response.status_code >= 500:
        code = "memory_provider_unavailable"
    else:
        code = "memory_provider_rejected"
    raise MemoryProviderError(code)


class _RemoteClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None
        self._transport = transport
        self._semaphore = asyncio.Semaphore(8)

    async def initialize(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(
                    self.timeout,
                    connect=self.connect_timeout,
                ),
            )

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None if self._owns_client else self._client

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        await self.initialize()
        assert self._client is not None
        try:
            async with self._semaphore:
                return await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as error:
            raise MemoryProviderError("memory_provider_timeout") from error
        except httpx.HTTPError as error:
            raise MemoryProviderError("memory_provider_network_error") from error

    @property
    def connect_timeout(self) -> float:
        raise NotImplementedError

    @property
    def timeout(self) -> float:
        raise NotImplementedError


class RemoteEmbeddingProvider(_RemoteClient):
    """Strict OpenAI-compatible embedding client using the shared STT key."""

    @property
    def connect_timeout(self) -> float:
        return self.settings.embedding_api_connect_timeout_seconds

    @property
    def timeout(self) -> float:
        return self.settings.embedding_api_timeout_seconds

    async def health(self) -> bool:
        if not self.settings.embedding_api_url:
            return False
        response = await self._request("GET", self.settings.embedding_api_url)
        # The configured deployment exposes a POST-only inference route. A
        # 405 therefore confirms that the route is reachable without sending
        # an unnecessary model inference request during readiness checks.
        return response.is_success or response.status_code == 405

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        values = _validate_texts(texts, self.settings.embedding_api_max_batch_size)
        if not self.settings.embedding_api_url:
            raise MemoryProviderError("memory_provider_not_configured")
        response = await self._request(
            "POST",
            self.settings.embedding_api_url,
            headers={**_headers(self.settings), "Content-Type": "application/json"},
            json={
                "model": self.settings.memory_expected_embedding_model,
                "input": list(values),
                "encoding_format": "float",
            },
        )
        _raise_for_status(response)
        payload = _json(response, self.settings.embedding_api_max_response_bytes)
        model = payload.get("model")
        if model != self.settings.memory_expected_embedding_model:
            raise MemoryProviderError("memory_provider_contract_invalid")

        # The configured embedding deployment returns the same model and
        # vectors as the OpenAI-compatible contract, but names the vector
        # collection ``embeddings`` and exposes its dimension as ``dim``.
        # Normalize that narrow variant here so the rest of the memory
        # pipeline remains provider-neutral and keeps the same validation.
        if "data" in payload:
            data = payload.get("data")
        else:
            raw_embeddings = payload.get("embeddings")
            declared_dimension = payload.get("dim")
            if not isinstance(raw_embeddings, list) or (
                declared_dimension is not None
                and (
                    type(declared_dimension) is not int
                    or declared_dimension != self.settings.memory_embedding_dimension
                )
            ):
                raise MemoryProviderError("memory_provider_contract_invalid")
            data = [
                {"index": expected_index, "embedding": vector}
                for expected_index, vector in enumerate(raw_embeddings)
            ]
        if not isinstance(data, list):
            raise MemoryProviderError("memory_provider_contract_invalid")
        if len(data) != len(values):
            raise MemoryProviderError("memory_provider_contract_invalid")
        vectors: list[tuple[float, ...]] = []
        for expected_index, item in enumerate(data):
            if not isinstance(item, dict) or item.get("index") != expected_index:
                raise MemoryProviderError("memory_provider_contract_invalid")
            vector = item.get("embedding")
            if (
                not isinstance(vector, list)
                or len(vector) != self.settings.memory_embedding_dimension
            ):
                raise MemoryProviderError("memory_provider_contract_invalid")
            if not all(
                isinstance(value, (int, float)) and math.isfinite(value) for value in vector
            ):
                raise MemoryProviderError("memory_provider_contract_invalid")
            norm = math.sqrt(sum(float(value) ** 2 for value in vector))
            if not math.isfinite(norm) or norm <= 0:
                raise MemoryProviderError("memory_provider_contract_invalid")
            vectors.append(tuple(float(value) for value in vector))
        return EmbeddingResponse(model=model, vectors=tuple(vectors))


class RemoteReranker(_RemoteClient):
    """Strict reranker client; document text is never returned to the UI."""

    @property
    def connect_timeout(self) -> float:
        return self.settings.rerank_api_connect_timeout_seconds

    @property
    def timeout(self) -> float:
        return self.settings.rerank_api_timeout_seconds

    async def health(self) -> bool:
        if not self.settings.rerank_api_url:
            return False
        response = await self._request("GET", self.settings.rerank_api_url)
        return response.is_success or response.status_code == 405

    async def rerank(self, query: str, documents: Sequence[str]) -> tuple[RerankResult, ...]:
        normalized_query = " ".join(query.split())
        if not normalized_query or len(normalized_query.encode("utf-8")) > 8_192:
            raise MemoryProviderError("memory_query_invalid")
        values = _validate_texts(documents, self.settings.rerank_api_max_docs)
        if not self.settings.rerank_api_url:
            raise MemoryProviderError("memory_provider_not_configured")
        response = await self._request(
            "POST",
            self.settings.rerank_api_url,
            headers={**_headers(self.settings), "Content-Type": "application/json"},
            json={
                "model": self.settings.memory_expected_rerank_model,
                "query": normalized_query,
                "documents": list(values),
                "top_n": len(values),
                "return_documents": False,
            },
        )
        _raise_for_status(response)
        payload = _json(response, self.settings.rerank_api_max_response_bytes)
        if payload.get("model") != self.settings.memory_expected_rerank_model:
            raise MemoryProviderError("memory_provider_contract_invalid")
        results = payload.get("results")
        if not isinstance(results, list) or len(results) > len(values):
            raise MemoryProviderError("memory_provider_contract_invalid")
        parsed: list[RerankResult] = []
        seen: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                raise MemoryProviderError("memory_provider_contract_invalid")
            index = item.get("index")
            score_keys = [key for key in ("relevance_score", "score") if key in item]
            if len(score_keys) != 1:
                raise MemoryProviderError("memory_provider_contract_invalid")
            score = item[score_keys[0]]
            if (
                not isinstance(index, int)
                or index < 0
                or index >= len(values)
                or index in seen
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
                or not 0 <= float(score) <= 1
            ):
                raise MemoryProviderError("memory_provider_contract_invalid")
            seen.add(index)
            parsed.append(RerankResult(index=index, score=float(score)))
        if any(left.score < right.score for left, right in zip(parsed, parsed[1:], strict=False)):
            raise MemoryProviderError("memory_provider_contract_invalid")
        return tuple(parsed)


def _validate_texts(texts: Sequence[str], maximum: int) -> tuple[str, ...]:
    if not 1 <= len(texts) <= maximum:
        raise MemoryProviderError("memory_batch_invalid")
    values = tuple(" ".join(text.split()) for text in texts)
    if any(not value or len(value.encode("utf-8")) > 16_384 for value in values):
        raise MemoryProviderError("memory_input_invalid")
    return values
