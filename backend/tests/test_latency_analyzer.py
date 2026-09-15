from __future__ import annotations

from scripts.analyze_latency import load_analysis_records, metrics_for_turn


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


def test_analyzer_normalizes_client_event_aliases() -> None:
    records = [
        _record("microphone_speech_start", 1000, 10_000, "client"),
        _record("vad_end", 1500, 10_500, "client"),
        _record("client_stt_final", 1700, 10_700, "client"),
    ]

    values = metrics_for_turn(records)
    assert values["speech_duration"] == 500.0
    assert values["speech_end_to_stt_final"] == 200.0


def test_analyzer_aligns_client_and_android_wall_clocks() -> None:
    records = [
        _record("speech_end", 1000, 10_000, "backend"),
        _record("tts_first_audio_chunk", 2000, 20_000, "backend"),
        {
            **_record("tts.started", 3000, 19_000, "client"),
            "metadata": {"gateway_timestamp_ms": 20_000, "received_at_ms": 19_000},
        },
        _record("tts_first_chunk_received", 3010, 19_010, "android"),
        _record("tts_playback_completed", 12_000, 21_000, "android"),
    ]

    values = metrics_for_turn(records)
    assert values["server_to_client_audio"] == 10.0
    assert values["speech_end_to_first_audio"] == 10_010.0
    assert values["end_to_end"] == 12_000.0


def test_live_trace_joins_explicit_backend_trace(tmp_path) -> None:
    live_path = tmp_path / "live_latency_trace.jsonl"
    backend_path = tmp_path / "backend" / "latency_trace.jsonl"
    live_path.write_text(
        '{"event":"tts_playback_completed","clock_domain":"android",'
        '"timestamp_ms":2000,"monotonic_ms":2000,"turn_id":"turn-1"}\n',
        encoding="utf-8",
    )
    backend_path.parent.mkdir()
    backend_path.write_text(
        '{"event":"speech_end","clock_domain":"backend",'
        '"timestamp_ms":1000,"monotonic_ms":1000,"turn_id":"turn-1"}\n',
        encoding="utf-8",
    )

    records = load_analysis_records(live_path, backend_path)

    assert {record["event"] for record in records} == {
        "speech_end",
        "tts_playback_completed",
    }
