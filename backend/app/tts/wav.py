"""Incremental validation and extraction of Kokoro WAV PCM payloads."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from app.tts.base import TTSProviderError


@dataclass(frozen=True)
class WavFormat:
    audio_format: int
    channels: int
    sample_rate_hz: int
    bits_per_sample: int
    block_align: int


class WavPcmStreamParser:
    """Extract raw PCM16 data from a RIFF/WAVE stream without buffering it all."""

    def __init__(self, *, expected_sample_rate_hz: int) -> None:
        self.expected_sample_rate_hz = expected_sample_rate_hz
        self._buffer = bytearray()
        self._state = "riff"
        self._chunk_id: bytes | None = None
        self._chunk_remaining = 0
        self._chunk_is_padded = False
        self._fmt_buffer = bytearray()
        self._format: WavFormat | None = None
        self._saw_data = False
        self._pcm_remainder = bytearray()

    @property
    def format(self) -> WavFormat | None:
        return self._format

    @property
    def saw_data(self) -> bool:
        return self._saw_data

    def feed(self, data: bytes) -> list[bytes]:
        if data:
            self._buffer.extend(data)
        output: list[bytes] = []
        while True:
            if self._state == "riff":
                if len(self._buffer) < 12:
                    break
                riff, _size, wave = struct.unpack_from("<4sI4s", self._buffer)
                del self._buffer[:12]
                if riff != b"RIFF" or wave != b"WAVE":
                    raise TTSProviderError("TTS provider did not return a RIFF/WAVE response")
                self._state = "chunk_header"

            if self._state == "chunk_header":
                if len(self._buffer) < 8:
                    break
                self._chunk_id, chunk_size = struct.unpack_from("<4sI", self._buffer)
                del self._buffer[:8]
                self._chunk_remaining = chunk_size
                self._chunk_is_padded = bool(chunk_size & 1)
                if self._chunk_id == b"fmt ":
                    if chunk_size < 16 or chunk_size > 4096:
                        raise TTSProviderError("TTS WAV fmt chunk is invalid")
                    self._fmt_buffer.clear()
                    self._state = "fmt"
                elif self._chunk_id == b"data":
                    if self._format is None:
                        raise TTSProviderError("TTS WAV data chunk arrived before fmt chunk")
                    self._saw_data = True
                    self._state = "data"
                else:
                    self._state = "skip"

            if self._state == "fmt":
                take = min(self._chunk_remaining, len(self._buffer))
                if take:
                    self._fmt_buffer.extend(self._buffer[:take])
                    del self._buffer[:take]
                    self._chunk_remaining -= take
                if self._chunk_remaining:
                    break
                self._format = self._parse_format(bytes(self._fmt_buffer))
                self._state = "pad" if self._chunk_is_padded else "chunk_header"

            if self._state == "data":
                take = min(self._chunk_remaining, len(self._buffer))
                if take:
                    self._pcm_remainder.extend(self._buffer[:take])
                    del self._buffer[:take]
                    self._chunk_remaining -= take
                    complete_bytes = len(self._pcm_remainder) & ~1
                    if complete_bytes:
                        output.append(bytes(self._pcm_remainder[:complete_bytes]))
                        del self._pcm_remainder[:complete_bytes]
                if self._chunk_remaining:
                    break
                self._state = "pad" if self._chunk_is_padded else "chunk_header"

            if self._state == "skip":
                take = min(self._chunk_remaining, len(self._buffer))
                if take:
                    del self._buffer[:take]
                    self._chunk_remaining -= take
                if self._chunk_remaining:
                    break
                self._state = "pad" if self._chunk_is_padded else "chunk_header"

            if self._state == "pad":
                if not self._buffer:
                    break
                del self._buffer[:1]
                self._state = "chunk_header"

        return output

    def finish(self) -> None:
        if self._state == "riff":
            raise TTSProviderError("TTS WAV response is truncated")
        if self._state == "pad":
            raise TTSProviderError("TTS WAV response is missing a chunk padding byte")
        if self._chunk_remaining or self._pcm_remainder:
            raise TTSProviderError("TTS WAV data is truncated or not PCM16 aligned")
        if self._format is None or not self._saw_data:
            raise TTSProviderError("TTS WAV response has no PCM data chunk")

    def _parse_format(self, data: bytes) -> WavFormat:
        audio_format, channels, sample_rate, _byte_rate, block_align, bits = struct.unpack_from(
            "<HHIIHH", data
        )
        result = WavFormat(
            audio_format=audio_format,
            channels=channels,
            sample_rate_hz=sample_rate,
            bits_per_sample=bits,
            block_align=block_align,
        )
        if (
            result.audio_format != 1
            or result.channels != 1
            or result.sample_rate_hz != self.expected_sample_rate_hz
            or result.bits_per_sample != 16
            or result.block_align != 2
        ):
            raise TTSProviderError(
                f"TTS WAV format must be mono PCM16 at {self.expected_sample_rate_hz} Hz"
            )
        return result
