"""Lifecycle wrapper for the configured TTS engine."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.core.config import Settings
from app.tts.base import TTSEngineInfo
from app.tts.remote import RemoteTTSEngine


class TTSService:
    def __init__(self, settings: Settings, *, transport=None) -> None:
        self.engine = RemoteTTSEngine(settings, transport=transport)
        self.info: TTSEngineInfo | None = None

    async def initialize(self) -> TTSEngineInfo:
        self.info = await self.engine.initialize()
        return self.info

    @property
    def enabled(self) -> bool:
        return bool(self.info is not None and self.info.enabled)

    async def stream(self, *, text: str, response_id: str) -> AsyncIterator[bytes]:
        async for chunk in self.engine.stream(text=text, response_id=response_id):
            yield chunk

    async def close(self) -> None:
        await self.engine.close()
