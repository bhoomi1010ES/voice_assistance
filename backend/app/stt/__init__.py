"""Local speech-to-text service and engine contracts."""

from app.stt.base import (
    STTAudioError,
    STTAudioTooLongError,
    STTAuthenticationError,
    STTCancelledError,
    STTConfigurationError,
    STTEmptyAudioError,
    STTEmptyTranscriptError,
    STTEngine,
    STTEngineInfo,
    STTEngineTurn,
    STTError,
    STTInferenceError,
    STTNetworkError,
    STTRateLimitError,
    STTTimeoutError,
    STTUnavailableError,
)
from app.stt.remote_engine import RemoteTranscriptionEngine
from app.stt.service import (
    STTService,
    STTTranscriptEvent,
    STTTranscriptResult,
)
from app.stt.windows_engine import WindowsSpeechEngine

__all__ = [
    "STTAudioError",
    "STTAudioTooLongError",
    "STTAuthenticationError",
    "STTCancelledError",
    "STTConfigurationError",
    "STTEmptyAudioError",
    "STTEmptyTranscriptError",
    "STTError",
    "STTInferenceError",
    "STTNetworkError",
    "STTRateLimitError",
    "STTService",
    "STTTimeoutError",
    "STTUnavailableError",
    "STTTranscriptEvent",
    "STTTranscriptResult",
    "RemoteTranscriptionEngine",
    "STTEngine",
    "STTEngineInfo",
    "STTEngineTurn",
    "WindowsSpeechEngine",
]
