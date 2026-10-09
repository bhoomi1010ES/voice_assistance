"""Audio energy evaluation and silence detection for PCM16 audio."""

from __future__ import annotations

import array
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioEnergyResult:
    """Detailed energy metrics for a bounded PCM16 audio turn."""

    has_speech: bool
    global_rms: float
    peak_window_rms: float
    peak_amplitude: int
    sample_count: int
    duration_ms: float
    window_count: int


def evaluate_pcm16_speech_energy(
    pcm: bytes | bytearray,
    *,
    sample_rate_hz: int = 16_000,
    window_ms: int = 100,
    min_rms: float = 25.0,
    min_peak: int = 100,
) -> AudioEnergyResult:
    """Evaluate whether a PCM16 mono buffer contains speech energy.

    Uses windowed RMS as the primary measure to ensure short speech bursts
    (e.g., 300 ms utterances inside a 15 s turn) are not diluted by surrounding
    silence. Peak amplitude provides a secondary noise guard so an isolated
    single-sample glitch or DC step does not falsely register as speech without
    sustained acoustic energy.

    Handles empty, odd-length, and malformed buffers safely without raising.
    """
    if not pcm or len(pcm) < 2:
        return AudioEnergyResult(
            has_speech=False,
            global_rms=0.0,
            peak_window_rms=0.0,
            peak_amplitude=0,
            sample_count=0,
            duration_ms=0.0,
            window_count=0,
        )

    # Ensure even byte count for 16-bit linear PCM (2 bytes per sample).
    usable_len = len(pcm) - (len(pcm) % 2)
    raw_bytes = bytes(pcm[:usable_len]) if isinstance(pcm, bytearray) else pcm[:usable_len]

    samples = array.array("h")
    try:
        samples.frombytes(raw_bytes)
    except (ValueError, OverflowError):
        # Malformed buffer fallback
        return AudioEnergyResult(
            has_speech=False,
            global_rms=0.0,
            peak_window_rms=0.0,
            peak_amplitude=0,
            sample_count=0,
            duration_ms=0.0,
            window_count=0,
        )

    sample_count = len(samples)
    if sample_count == 0:
        return AudioEnergyResult(
            has_speech=False,
            global_rms=0.0,
            peak_window_rms=0.0,
            peak_amplitude=0,
            sample_count=0,
            duration_ms=0.0,
            window_count=0,
        )

    duration_ms = round(sample_count / sample_rate_hz * 1000.0, 1)

    # Global energy & peak amplitude calculation
    total_sq = 0
    peak_amplitude = 0
    for sample in samples:
        abs_sample = abs(sample)
        if abs_sample > peak_amplitude:
            peak_amplitude = abs_sample
        total_sq += sample * sample

    global_rms = round(math.sqrt(total_sq / sample_count), 2)

    # Windowed RMS calculation (window_ms, minimum 1 sample per window)
    window_samples = max(1, int(sample_rate_hz * (window_ms / 1000.0)))
    # 50% hop for smooth overlap detection
    hop_samples = max(1, window_samples // 2)

    peak_window_rms = 0.0
    window_count = 0

    idx = 0
    while idx < sample_count:
        end_idx = min(idx + window_samples, sample_count)
        window_len = end_idx - idx
        if window_len <= 0:
            break

        window_sq = sum(samples[i] * samples[i] for i in range(idx, end_idx))
        window_rms = math.sqrt(window_sq / window_len)
        if window_rms > peak_window_rms:
            peak_window_rms = window_rms
        window_count += 1
        idx += hop_samples

    peak_window_rms = round(peak_window_rms, 2)

    # Windowed RMS is the primary criterion. Both windowed RMS and peak amplitude
    # must satisfy their respective thresholds so neither pure noise floors nor
    # isolated 1-sample spikes falsely classify as speech.
    has_speech = (peak_window_rms >= min_rms) and (peak_amplitude >= min_peak)

    return AudioEnergyResult(
        has_speech=has_speech,
        global_rms=global_rms,
        peak_window_rms=peak_window_rms,
        peak_amplitude=peak_amplitude,
        sample_count=sample_count,
        duration_ms=duration_ms,
        window_count=window_count,
    )
