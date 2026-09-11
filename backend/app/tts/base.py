"""Shared Phase 8 text-to-speech contracts."""

from __future__ import annotations

from dataclasses import dataclass


class TTSError(RuntimeError):
    """Base error for bounded TTS delivery."""


class TTSConfigurationError(TTSError):
    """TTS endpoint configuration is invalid or incomplete."""


class TTSNetworkError(TTSError):
    """The provider could not be reached."""


class TTSProviderError(TTSError):
    """The provider returned a non-success response or invalid audio."""


class TTSCancelledError(TTSError):
    """The correlated response was cancelled or superseded."""


@dataclass(frozen=True)
class TTSEngineInfo:
    enabled: bool
    provider: str
    endpoint_host: str | None = None
    model: str | None = None
    voice: str | None = None
    sample_rate_hz: int | None = None
