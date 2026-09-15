"""Streaming HTTP adapter for the configured `/v1/audio/speech` endpoint."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings
from app.tts.base import (
    TTSCancelledError,
    TTSConfigurationError,
    TTSEngineInfo,
    TTSError,
    TTSNetworkError,
    TTSProviderError,
)
from app.tts.wav import WavPcmStreamParser


@dataclass(frozen=True, slots=True)
class TTSStreamMetrics:
    """Monotonic provider/request metrics for one synthesized sentence."""

    generation_ms: float
    pcm_bytes: int
    audio_duration_ms: float
    rtf: float | None


class RemoteTTSEngine:
    """Stream provider PCM without buffering a complete assistant response."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._info: TTSEngineInfo | None = None
        self._last_stream_metrics: TTSStreamMetrics | None = None

    async def initialize(self) -> TTSEngineInfo:
        if self._info is not None:
            return self._info
        if not self.settings.tts_api_url:
            self._info = TTSEngineInfo(enabled=False, provider="unconfigured")
            return self._info
        endpoint = self.settings.tts_api_url_resolved
        if self._credentials() is None:
            raise TTSConfigurationError("TTS_API_KEY or STT_API_KEY is required")
        timeout = httpx.Timeout(
            self.settings.tts_api_timeout_seconds,
            connect=self.settings.tts_api_connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=8),
            transport=self._transport,
        )
        self._info = TTSEngineInfo(
            enabled=True,
            provider="remote",
            endpoint_host=urlsplit(endpoint).netloc,
            model=self.settings.tts_api_model,
            voice=self.settings.tts_api_voice,
            sample_rate_hz=self.settings.tts_api_sample_rate_hz,
        )
        return self._info

    @property
    def enabled(self) -> bool:
        return self._info is not None and self._info.enabled

    async def stream(
        self,
        *,
        text: str,
        response_id: str,
    ) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        await self.initialize()
        client = self._client
        if client is None:
            raise TTSConfigurationError("TTS engine is not configured")
        payload = {
            "model": self.settings.tts_api_model,
            "voice": self.settings.tts_api_voice,
            "input": text,
            "response_format": self.settings.tts_api_response_format,
            "sample_rate_hz": self.settings.tts_api_sample_rate_hz,
        }
        received = 0
        pcm_bytes = 0
        request_started = time.monotonic()
        self._last_stream_metrics = None
        pending_pcm: bytes | None = None
        try:
            async with client.stream(
                "POST",
                self.settings.tts_api_url_resolved,
                headers=self._auth_headers(),
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise TTSProviderError(f"TTS provider returned HTTP {response.status_code}")
                if self.settings.tts_api_response_format != "wav":
                    raise TTSConfigurationError(
                        "Kokoro TTS must be configured with response_format=wav"
                    )
                content_type = response.headers.get("content-type", "").lower()
                if "audio/wav" not in content_type and "audio/x-wav" not in content_type:
                    raise TTSProviderError("TTS provider did not return audio/wav")
                header_rate = response.headers.get("x-sample-rate")
                if header_rate is not None and header_rate != str(
                    self.settings.tts_api_sample_rate_hz
                ):
                    raise TTSProviderError("TTS provider sample rate does not match configuration")
                parser = WavPcmStreamParser(
                    expected_sample_rate_hz=self.settings.tts_api_sample_rate_hz
                )
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > self.settings.tts_api_max_response_bytes:
                        raise TTSProviderError("TTS response exceeded the configured size limit")
                    for pcm_chunk in parser.feed(chunk):
                        pcm_bytes += len(pcm_chunk)
                        if pending_pcm is not None:
                            yield pending_pcm
                        pending_pcm = pcm_chunk
                parser.finish()
                generation_ms = round((time.monotonic() - request_started) * 1000, 3)
                audio_duration_ms = pcm_bytes * 1000 / (
                    self.settings.tts_api_sample_rate_hz * 1 * 2
                )
                self._last_stream_metrics = TTSStreamMetrics(
                    generation_ms=generation_ms,
                    pcm_bytes=pcm_bytes,
                    audio_duration_ms=audio_duration_ms,
                    rtf=(generation_ms / audio_duration_ms if audio_duration_ms > 0 else None),
                )
                if pending_pcm is not None:
                    yield pending_pcm
        except TTSError:
            raise
        except asyncio.CancelledError as error:
            raise TTSCancelledError("TTS response was cancelled") from error
        except httpx.TimeoutException as error:
            raise TTSNetworkError("TTS request timed out") from error
        except httpx.RequestError as error:
            raise TTSNetworkError("TTS request failed") from error

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def last_stream_metrics(self) -> TTSStreamMetrics | None:
        return self._last_stream_metrics

    def _credentials(self):
        return self.settings.tts_api_key or self.settings.stt_api_key

    def _auth_headers(self) -> dict[str, str]:
        credential = self._credentials()
        if credential is None:
            return {}
        return {"Authorization": f"Bearer {credential.get_secret_value()}"}
