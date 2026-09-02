from app.stt.base import STTEngineInfo


class NoopSTTService:
    """Keep non-STT API tests independent of Windows speech installation."""

    async def initialize(self) -> STTEngineInfo:
        return STTEngineInfo(
            engine="test",
            runtime="test",
            available=True,
        )

    async def close(self) -> None:
        return None
