"""Bounded exact-PCM capture for one physical Windows STT diagnostic turn."""

from __future__ import annotations

import hashlib
import json
import logging
import struct
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

LOGGER = logging.getLogger("voice-assistance-backend")


class DiagnosticPcmCapture:
    """Keep one turn's worker-bound PCM and materialize raw/WAV evidence."""

    SAMPLE_RATE_HZ = 16_000
    CHANNELS = 1
    SAMPLE_WIDTH_BYTES = 2
    NEAR_ZERO_THRESHOLD = 64

    def __init__(
        self,
        directory: Path,
        *,
        session_id: str,
        turn_id: str,
        max_seconds: int,
    ) -> None:
        self.directory = directory
        self.session_id = session_id
        self.turn_id = turn_id
        self.max_bytes = max_seconds * self.SAMPLE_RATE_HZ * self.SAMPLE_WIDTH_BYTES
        self._pcm = bytearray()
        self._finalized = False
        self._started_monotonic = time.monotonic()
        self._first_audio_monotonic: float | None = None

    @property
    def byte_count(self) -> int:
        return len(self._pcm)

    def append(self, pcm_bytes: bytes) -> None:
        if self._finalized:
            return
        if len(pcm_bytes) % self.SAMPLE_WIDTH_BYTES:
            raise ValueError("diagnostic PCM16 capture requires an even byte count")
        if len(self._pcm) + len(pcm_bytes) > self.max_bytes:
            raise ValueError("diagnostic PCM capture exceeded the configured turn limit")
        if pcm_bytes and self._first_audio_monotonic is None:
            self._first_audio_monotonic = time.monotonic()
        self._pcm.extend(pcm_bytes)

    def finalize(
        self,
        *,
        status: str,
        hypothesis_raw: str | None = None,
        error: str | None = None,
    ) -> dict[str, object]:
        if self._finalized:
            return self._metadata_path().exists() and json.loads(
                self._metadata_path().read_text(encoding="utf-8")
            ) or {}
        self._finalized = True

        self.directory.mkdir(parents=True, exist_ok=True)
        pcm = bytes(self._pcm)
        raw_path = self.directory / "raw_input.pcm"
        wav_path = self.directory / "raw_input.wav"
        metadata_path = self._metadata_path()
        raw_path.write_bytes(pcm)
        with wave.open(str(wav_path), "wb") as output:
            output.setnchannels(self.CHANNELS)
            output.setsampwidth(self.SAMPLE_WIDTH_BYTES)
            output.setframerate(self.SAMPLE_RATE_HZ)
            output.writeframes(pcm)

        stats = self._sample_stats(pcm)
        finished_monotonic = time.monotonic()
        metadata: dict[str, object] = {
            "status": status,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "capture_started_monotonic_ns": round(self._started_monotonic * 1_000_000_000),
            "first_audio_monotonic_ns": (
                round(self._first_audio_monotonic * 1_000_000_000)
                if self._first_audio_monotonic is not None
                else None
            ),
            "capture_finished_monotonic_ns": round(finished_monotonic * 1_000_000_000),
            "sample_rate_hz": self.SAMPLE_RATE_HZ,
            "channels": self.CHANNELS,
            "sample_width_bytes": self.SAMPLE_WIDTH_BYTES,
            "encoding": "signed_pcm16_little_endian",
            "byte_count": len(pcm),
            "sample_count": len(pcm) // self.SAMPLE_WIDTH_BYTES,
            "duration_ms": round(
                len(pcm) / (self.SAMPLE_RATE_HZ * self.SAMPLE_WIDTH_BYTES) * 1000
            ),
            "sha256": hashlib.sha256(pcm).hexdigest(),
            "raw_path": str(raw_path),
            "wav_path": str(wav_path),
            "hypothesis_raw": hypothesis_raw,
            "error": error,
            **stats,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        LOGGER.info(
            "Exact physical STT PCM diagnostic captured",
            extra={
                "event": "STT_DIAGNOSTIC_PCM_CAPTURED",
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "status": status,
                "raw_path": str(raw_path),
                "wav_path": str(wav_path),
                "byte_count": len(pcm),
                "sample_count": len(pcm) // self.SAMPLE_WIDTH_BYTES,
                "duration_ms": metadata["duration_ms"],
                "sha256": metadata["sha256"],
                "monotonic_ms": round(finished_monotonic * 1000, 1),
            },
        )
        return metadata

    def _metadata_path(self) -> Path:
        return self.directory / "metadata.json"

    @classmethod
    def _sample_stats(cls, pcm: bytes) -> dict[str, object]:
        if not pcm:
            return {
                "minimum_sample": None,
                "maximum_sample": None,
                "rms": 0.0,
                "peak_amplitude": 0,
                "clipped_sample_count": 0,
                "clipping_percentage": 0.0,
                "dc_offset": 0.0,
                "near_zero_sample_count": 0,
                "near_zero_percentage": 0.0,
            }

        sample_count = len(pcm) // cls.SAMPLE_WIDTH_BYTES
        samples = struct.unpack(f"<{sample_count}h", pcm)
        minimum = min(samples)
        maximum = max(samples)
        absolute = [abs(sample) for sample in samples]
        clipped = sum(sample in (-32768, 32767) for sample in samples)
        near_zero = sum(value <= cls.NEAR_ZERO_THRESHOLD for value in absolute)
        sum_samples = sum(samples)
        rms = (sum(sample * sample for sample in samples) / sample_count) ** 0.5
        return {
            "minimum_sample": minimum,
            "maximum_sample": maximum,
            "rms": round(rms, 3),
            "peak_amplitude": max(absolute),
            "clipped_sample_count": clipped,
            "clipping_percentage": round(clipped / sample_count * 100, 6),
            "dc_offset": round(sum_samples / sample_count, 6),
            "near_zero_sample_count": near_zero,
            "near_zero_percentage": round(near_zero / sample_count * 100, 6),
        }
