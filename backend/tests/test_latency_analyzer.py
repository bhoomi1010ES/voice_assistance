from __future__ import annotations

from scripts.analyze_latency import metrics_for_turn


def _record(event: str, monotonic_ms: float, timestamp_ms: int, domain: str = "backend") -> dict:
    return {
        "event": event,
        "monotonic_ms": monotonic_ms,
        "timestamp_ms": timestamp_ms,
        "clock_domain": domain,
        "turn_id": "turn-1",
    }


def test_analyzer_uses_monotonic_for_local_and_wall_clock_for_cross_process() -> None:
    records = [
        _record("speech_start", 1000, 10_000),
        _record("speech_end", 1500, 10_500),
        _record("stt_final", 1700, 10_700),
        _record("tts_first_chunk_received", 9000, 11_900, "android"),
        _record("tts_playback_start", 9050, 11_950, "client"),
        _record("tts_playback_complete", 10_000, 12_900, "client"),
    ]

    values = metrics_for_turn(records)
    assert values["speech_duration"] == 500.0
    assert values["speech_end_to_stt_final"] == 200.0
    assert values["playback_buffer"] == 50.0
    assert values["end_to_end"] == 2_400.0
