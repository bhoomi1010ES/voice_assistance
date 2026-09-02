"""Local speech-to-text service and engine contracts."""

from app.stt.base import (
    STTAudioError,
    STTCancelledError,
    STTConfigurationError,
    STTEngine,
    STTEngineInfo,
    STTEngineTurn,
    STTError,
    STTInferenceError,
    STTTimeoutError,
)
from app.stt.service import (
    STTService,
    STTTranscriptEvent,
    STTTranscriptResult,
)
from app.stt.windows_engine import WindowsSpeechEngine

__all__ = [
    "STTAudioError",
    "STTCancelledError",
    "STTConfigurationError",
    "STTError",
    "STTInferenceError",
    "STTService",
    "STTTimeoutError",
    "STTTranscriptEvent",
    "STTTranscriptResult",
    "STTEngine",
    "STTEngineInfo",
    "STTEngineTurn",
    "WindowsSpeechEngine",
]
