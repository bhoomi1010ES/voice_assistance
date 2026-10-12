"""Automated regression tests for PCM16 trailing silence trimming (QA-VOICE-001 Phase 2/3)."""

from __future__ import annotations

import io
import math
import struct
import wave
import uuid
import httpx
import pytest

from app.core.config import Settings
from app.stt.audio_energy import (
    AudioEnergyResult,
    evaluate_pcm16_speech_energy,
    trim_pcm16_trailing_silence,
)
from app.stt.base import STTEmptyTranscriptError
from app.stt.remote_engine import RemoteTranscriptionEngine


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "stt_engine": "remote",
        "stt_api_url": "https://stt.example.test/v1/audio/transcriptions",
        "stt_api_key": "test-api-key",
        "stt_api_model": "test-model",
        "stt_api_timeout_seconds": 2,
        "stt_api_connect_timeout_seconds": 1,
        "stt_silence_guard_enabled": True,
        "stt_silence_guard_min_rms": 25.0,
        "stt_silence_guard_min_peak": 100,
        "stt_silence_guard_window_ms": 100,
        "stt_trailing_silence_trimming_enabled": True,
        "stt_trailing_silence_padding_ms": 800,
    }
    values.update(overrides)
    return Settings(**values)


def _sine_pcm(
    duration_s: float,
    amplitude: int = 1500,
    frequency_hz: float = 440.0,
    sample_rate_hz: int = 16_000,
) -> bytes:
    sample_count = int(duration_s * sample_rate_hz)
    samples = [
        int(amplitude * math.sin(2 * math.pi * frequency_hz * i / sample_rate_hz))
        for i in range(sample_count)
    ]
    return struct.pack(f"<{len(samples)}h", *samples)


def _ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


# ==============================================================================
# Unit Tests: trim_pcm16_trailing_silence
# ==============================================================================


def test_trim_trailing_silence_short_speech_with_long_silence() -> None:
    """A short 1.5s speech followed by 26s of silence must be trimmed to ~2.3s."""
    sample_rate_hz = 16_000
    speech = _sine_pcm(1.5, amplitude=2000, sample_rate_hz=sample_rate_hz)
    silence = b"\x00\x00" * int(26.0 * sample_rate_hz)
    combined = speech + silence

    total_samples = len(combined) // 2
    assert total_samples == 27.5 * sample_rate_hz

    trimmed_pcm, energy, trimmed_ms = trim_pcm16_trailing_silence(
        combined,
        sample_rate_hz=sample_rate_hz,
        window_ms=100,
        min_rms=25.0,
        min_peak=100,
        trailing_padding_ms=800,
    )

    assert energy.has_speech is True
    # Speech ended around 1500ms
    assert energy.speech_end_ms is not None
    assert 1400 <= energy.speech_end_ms <= 1600

    # Trimmed duration should be roughly 25.2 seconds (26s silence - 0.8s padding)
    assert 24_500 <= trimmed_ms <= 26_000

    # Resulting audio duration should be ~2.3 seconds
    result_samples = len(trimmed_pcm) // 2
    result_duration_s = result_samples / sample_rate_hz
    assert 2.2 <= result_duration_s <= 2.4

    # The speech portion at the beginning is perfectly preserved
    assert trimmed_pcm[: len(speech)] == speech


def test_trim_trailing_silence_preserves_internal_pauses() -> None:
    """Internal pauses between sentences must not be trimmed; only trailing silence is removed."""
    sample_rate_hz = 16_000
    speech_1 = _sine_pcm(1.0, amplitude=1800, sample_rate_hz=sample_rate_hz)
    mid_pause = b"\x00\x00" * int(1.2 * sample_rate_hz)  # 1.2s pause inside speech
    speech_2 = _sine_pcm(1.0, amplitude=1800, sample_rate_hz=sample_rate_hz)
    trailing_silence = b"\x00\x00" * int(5.0 * sample_rate_hz)  # 5s trailing silence

    full_audio = speech_1 + mid_pause + speech_2 + trailing_silence
    # Total = 1.0 + 1.2 + 1.0 + 5.0 = 8.2s

    trimmed_pcm, energy, trimmed_ms = trim_pcm16_trailing_silence(
        full_audio,
        sample_rate_hz=sample_rate_hz,
        trailing_padding_ms=800,
    )

    assert energy.has_speech is True
    # Second speech ends around 3.2s (3200ms)
    assert 3100 <= energy.speech_end_ms <= 3300

    # Internal pause must still exist at offset len(speech_1)
    internal_pause_offset = len(speech_1)
    assert trimmed_pcm[internal_pause_offset : internal_pause_offset + len(mid_pause)] == mid_pause

    # Resulting audio length should be ~3.2s + 0.8s = 4.0s
    result_duration_s = (len(trimmed_pcm) // 2) / sample_rate_hz
    assert 3.9 <= result_duration_s <= 4.1
    # Trimmed duration should be around 4.2s (5.0s - 0.8s)
    assert 4000 <= trimmed_ms <= 4300


def test_trim_trailing_silence_pure_silence_returns_zero_trimmed() -> None:
    """Pure silence has no speech, returns original audio with 0ms trimmed (silence guard rejects it)."""
    silence = b"\x00\x00" * 32_000
    trimmed_pcm, energy, trimmed_ms = trim_pcm16_trailing_silence(silence)

    assert energy.has_speech is False
    assert trimmed_ms == 0.0
    assert trimmed_pcm == silence


def test_trim_trailing_silence_short_or_empty_audio() -> None:
    """Empty and odd byte buffers are handled safely."""
    trimmed, energy, ms = trim_pcm16_trailing_silence(b"")
    assert trimmed == b""
    assert energy.has_speech is False
    assert ms == 0.0

    trimmed_odd, energy_odd, ms_odd = trim_pcm16_trailing_silence(b"\x00\x10\x05")
    assert len(trimmed_odd) == 2
    assert ms_odd == 0.0


def test_trim_trailing_silence_already_bounded_speech() -> None:
    """Speech with less trailing silence than padding (e.g. 300ms silence < 800ms padding) is not trimmed."""
    sample_rate_hz = 16_000
    speech = _sine_pcm(1.0, amplitude=1500, sample_rate_hz=sample_rate_hz)
    short_tail = b"\x00\x00" * int(0.3 * sample_rate_hz)  # 300ms tail
    audio = speech + short_tail

    trimmed_pcm, energy, trimmed_ms = trim_pcm16_trailing_silence(
        audio,
        trailing_padding_ms=800,
    )
    assert energy.has_speech is True
    assert trimmed_ms == 0.0
    assert trimmed_pcm == audio


# ==============================================================================
# Integration Tests: RemoteTranscriptionEngine trimming
# ==============================================================================


@pytest.mark.asyncio
async def test_remote_engine_trims_audio_sent_to_remote_endpoint() -> None:
    """Remote engine must trim trailing silence so Whisper never receives 27s of silence."""
    received_wav_bytes = b""

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received_wav_bytes
        # Read the multipart content
        content = request.content
        # Extract the wav file from multipart
        idx = content.find(b"RIFF")
        if idx != -1:
            received_wav_bytes = content[idx:]
        return httpx.Response(200, json={"text": "How can you help me?"}, request=request)

    settings = _settings(
        stt_trailing_silence_trimming_enabled=True,
        stt_trailing_silence_padding_ms=800,
    )
    engine = RemoteTranscriptionEngine(settings, transport=httpx.MockTransport(handler))

    session_id, turn_id, response_id = _ids()
    handle = await engine.start_turn(
        session_id=session_id,
        turn_id=turn_id,
        response_id=response_id,
        generation=0,
        language="en",
        on_partial=lambda _: None,
    )

    # 1.5s speech + 20s silence = 21.5s audio (688,000 bytes)
    sample_rate = 16_000
    speech = _sine_pcm(1.5, amplitude=2000, sample_rate_hz=sample_rate)
    silence = b"\x00\x00" * int(20.0 * sample_rate)
    await engine.push_audio(handle, speech + silence, generation=0)

    final = await engine.finish_turn(handle, generation=1)
    assert final.text == "How can you help me?"

    # Verify the WAV received by the remote endpoint was trimmed
    assert len(received_wav_bytes) > 0
    with wave.open(io.BytesIO(received_wav_bytes), "rb") as wf:
        n_frames = wf.getnframes()
        duration_s = n_frames / wf.getframerate()
        # Should be ~2.3 seconds (1.5s speech + 0.8s padding), NOT 21.5 seconds!
        assert 2.1 <= duration_s <= 2.5
        assert duration_s < 5.0
