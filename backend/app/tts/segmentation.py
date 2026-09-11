"""Deterministic sentence segmentation for low-latency TTS."""

from __future__ import annotations

import re

_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+|\n+")


class SentenceSegmenter:
    """Emit bounded natural-language chunks while retaining incomplete text."""

    def __init__(self, *, max_chars: int) -> None:
        self.max_chars = max_chars
        self._buffer = ""

    def push(self, text: str) -> list[str]:
        if text:
            self._buffer += text
        return self._drain(force=False)

    def flush(self) -> list[str]:
        return self._drain(force=True)

    def _drain(self, *, force: bool) -> list[str]:
        chunks: list[str] = []
        while self._buffer:
            boundary = _BOUNDARY.search(self._buffer)
            if boundary is not None:
                candidate = self._buffer[: boundary.start()].strip()
                self._buffer = self._buffer[boundary.end() :]
                if candidate:
                    chunks.extend(_bounded(candidate, self.max_chars))
                continue
            if len(self._buffer) > self.max_chars:
                split_at = self._buffer.rfind(" ", 0, self.max_chars + 1)
                split_at = split_at if split_at >= self.max_chars // 2 else self.max_chars
                candidate = self._buffer[:split_at].strip()
                self._buffer = self._buffer[split_at:].lstrip()
                if candidate:
                    chunks.append(candidate)
                continue
            break
        if force and self._buffer.strip():
            chunks.extend(_bounded(self._buffer.strip(), self.max_chars))
            self._buffer = ""
        return chunks


def _bounded(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    return [text[start : start + max_chars].strip() for start in range(0, len(text), max_chars)]
