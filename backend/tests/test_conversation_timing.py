from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.conversation_logging import (
    LATENCY_FIELDS,
    TIMING_FIELDS,
    TimingPoint,
    build_timing_payload,
)


def _points() -> dict[str, TimingPoint]:
    wall = datetime(2026, 9, 11, 19, 24, 15, 102134, tzinfo=UTC)
    values = {
        "turn_started_at": 100.0,
        "speech_started_at": 100.35,
        "speech_ended_at": 102.17,
        "stt_started_at": 102.18,
        "stt_completed_at": 103.91,
        "llm_started_at": 103.92,
        "llm_first_token_at": 104.35,
        "llm_completed_at": 105.15,
        "tts_requested_at": 105.16,
        "tts_first_audio_at": 105.67,
        "tts_playback_started_at": 105.87,
        "tts_playback_completed_at": 112.11,
        "turn_completed_at": 113.07,
    }
    return {
        name: TimingPoint(
            wall=wall + timedelta(seconds=instant - 100.0),
            monotonic=instant,
        )
        for name, instant in values.items()
    }


def test_timing_payload_has_utc_timestamps_and_monotonic_latencies() -> None:
    payload = build_timing_payload(
        _points(),
        tts={
            "sample_rate": 24_000,
            "channels": 1,
            "encoding": "pcm16",
            "prebuffer_ms": 200,
            "prebuffer_bytes": 9_600,
            "sequence_gaps": 0,
            "duplicate_frames": 0,
            "stale_frames": None,
            "underrun_delta": None,
        },
    )

    assert set(payload["timings"]) == set(TIMING_FIELDS)
    assert all(value.endswith("Z") for value in payload["timings"].values())
    assert set(payload["latency_ms"]) == set(LATENCY_FIELDS)
    assert all(value is None or value >= 0 for value in payload["latency_ms"].values())
    assert payload["latency_ms"]["speech_duration"] == 1820.0
    assert payload["latency_ms"]["llm_time_to_first_token"] == 430.0
    assert payload["latency_ms"]["turn_total"] == 13070.0
    assert payload["tts"]["sample_rate"] == 24_000


def test_unavailable_events_are_null_and_do_not_use_wall_clock_subtraction() -> None:
    points = _points()
    del points["tts_playback_started_at"]
    del points["tts_playback_completed_at"]
    points["turn_completed_at"] = TimingPoint(
        wall=datetime(2035, 1, 1, tzinfo=UTC),
        monotonic=113.07,
    )

    payload = build_timing_payload(points)

    assert payload["timings"]["tts_playback_started_at"] is None
    assert payload["timings"]["tts_playback_completed_at"] is None
    assert payload["latency_ms"]["tts_first_audio_to_playback"] is None
    assert payload["latency_ms"]["tts_playback_duration"] is None
    assert payload["latency_ms"]["turn_total"] == 13070.0


def test_timing_points_are_scoped_to_one_turn() -> None:
    first = build_timing_payload(_points())
    second_points = _points()
    second_points["turn_started_at"] = TimingPoint(
        wall=second_points["turn_started_at"].wall,
        monotonic=500.0,
    )
    second_points["turn_completed_at"] = TimingPoint(
        wall=second_points["turn_completed_at"].wall,
        monotonic=501.0,
    )
    second = build_timing_payload(second_points)

    assert first["latency_ms"]["turn_total"] == 13_070.0
    assert second["latency_ms"]["turn_total"] == 1_000.0
