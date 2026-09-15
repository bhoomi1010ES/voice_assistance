from __future__ import annotations

from scripts.analyze_latency import (
    filter_turn_records,
    load_analysis_records,
    metrics_for_turn,
    pipeline_metrics_for_turn,
)


def _record(event: str, monotonic_ms: float, timestamp_ms: int, domain: str = "backend") -> dict:
    return {
        "event": event,
        "monotonic_ms": monotonic_ms,
        "timestamp_ms": timestamp_ms,
        "clock_domain": domain,
        "turn_id": "turn-1",
    }


def test_turn_id_filter_can_select_exact_multi_turn_cohort() -> None:
    records = [
        {"turn_id": "test-1", "event": "llm_completed"},
        {"turn_id": "test-2", "event": "llm_completed"},
        {"turn_id": "historical", "event": "llm_completed"},
    ]

    selected = filter_turn_records(records, ["test-1", "test-2"])

    assert [record["turn_id"] for record in selected] == ["test-1", "test-2"]


def test_analyzer_uses_monotonic_for_latency_calculations() -> None:
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
    assert values["playback_buffer"] is None
    assert values["end_to_end"] is None


def test_analyzer_normalizes_client_event_aliases() -> None:
    records = [
        _record("microphone_speech_start", 1000, 10_000, "client"),
        _record("vad_end", 1500, 10_500, "client"),
        _record("client_stt_final", 1700, 10_700, "client"),
    ]

    values = metrics_for_turn(records)
    assert values["speech_duration"] == 500.0
    assert values["speech_end_to_stt_final"] == 200.0


def test_analyzer_never_uses_wall_clock_for_cross_process_latency() -> None:
    records = [
        _record("speech_end", 1000, 10_000, "backend"),
        _record("tts_first_audio_chunk", 2000, 20_000, "backend"),
        {
            **_record("tts.started", 3000, 19_000, "client"),
            "metadata": {"gateway_timestamp_ms": 20_000, "received_at_ms": 19_000},
        },
        _record("vad_end", 3000, 19_000, "android"),
        _record("tts_first_chunk_received", 3010, 19_010, "android"),
        _record("first_assistant_token_received", 3050, 19_050, "android"),
        _record("client_stt_final_received", 3020, 19_020, "android"),
        _record("tts_playback_completed", 12_000, 21_000, "android"),
    ]

    values = metrics_for_turn(records)
    assert values["server_to_client_audio"] is None
    assert values["speech_end_to_stt_final"] == 20.0
    assert values["speech_end_to_first_token"] == 50.0
    assert values["speech_end_to_first_audio"] == 10.0
    assert values["end_to_end"] == 9_000.0


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


def test_pipeline_breakdown_uses_monotonic_ns_and_keeps_residual_explicit() -> None:
    origin_ns = 50_000_000_000
    records = []

    def add(event: str, offset_ms: float, duration_ms: float | None = None) -> None:
        record = {
            "event": event,
            "clock_domain": "backend",
            "turn_id": "turn-1",
            "timestamp_ms": 100_000 + int(offset_ms),
            "monotonic_ns": origin_ns + int(offset_ms * 1_000_000),
        }
        if duration_ms is not None:
            record["duration_ms"] = duration_ms
        records.append(record)

    add("speech_end", 0)
    add("stt_final", 40)
    add("stt_final_received", 40)
    add("orchestration_started", 42)
    add("gateway_commit_queue_wait_completed", 12, 5)
    add("turn_persistence_completed", 52, 10)
    add("memory_policy_lookup_completed", 55, 3)
    add("confirmation_routing_completed", 57, 2)
    add("memory_decision_completed", 77, 20)
    add("explicit_memory_routing_completed", 78, 1)
    add("prompt_build_completed", 90, 12)
    add("token_budget_completed", 80, 2)
    add("tool_routing_completed", 81, 1)
    add("llm_prepare_completed", 94, 4)
    add("provider_prepare_completed", 92, 2)
    add("llm_semaphore_acquired", 97, 3)
    add("llm_request_started", 97)
    add("http_request_started", 97)
    add("llm_connection_acquired", 98)
    add("llm_stream_opened", 107)
    add("llm_first_token", 127)

    values = pipeline_metrics_for_turn(records)

    assert values["stt_final_to_orchestration"] == 2
    assert values["gateway_commit_queue_wait"] == 5
    assert values["turn_persistence"] == 10
    assert values["memory_rag_decision"] == 20
    assert values["prompt_build"] == 9
    assert values["token_budget"] == 2
    assert values["orchestration_to_request"] == 55
    assert values["stt_final_to_request"] == 57
    assert values["provider_ttft"] == 30
    assert values["connection_acquisition"] == 1
    assert values["known_measured_stages_total"] == 57
    assert values["unaccounted_latency"] == 0
