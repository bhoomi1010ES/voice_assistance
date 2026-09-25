from __future__ import annotations

from scripts.analyze_latency import (
    accounting_for_turn,
    correlation_errors,
    filter_turn_records,
    incompatible_metric_keys,
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


def test_backend_speech_to_text_audio_and_turn_metrics_use_one_clock_domain() -> None:
    records = [
        _record("speech_end", 100.0, 1000, "backend_python_perf_counter"),
        _record("first_assistant_text_send_started", 240.0, 1140, "backend_python_perf_counter"),
        _record("tts_first_audio_received", 380.0, 1280, "backend_python_perf_counter"),
        _record("turn_complete", 900.0, 1800, "backend_python_perf_counter"),
    ]
    metrics = metrics_for_turn(records)
    assert metrics["backend_speech_end_to_first_text"] == 140.0
    assert metrics["backend_speech_end_to_first_audio"] == 280.0
    assert metrics["backend_complete_turn"] == 800.0

    records[-1] = _record("turn_complete", 900.0, 1800, "android_elapsed_realtime")
    assert metrics_for_turn(records)["backend_complete_turn"] is None


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


def test_incompatible_clock_intervals_are_not_clamped_to_zero() -> None:
    records = [
        _record("stt_final_received", 9000, 10_000, "backend"),
        _record("orchestration_started", 8000, 10_100, "backend"),
        _record("llm_request_started", 7000, 10_200, "client"),
    ]

    values = pipeline_metrics_for_turn(records)

    assert values["stt_final_to_orchestration"] is None
    assert values["stt_final_to_request"] is None
    assert "stt_final_to_orchestration" in incompatible_metric_keys(records)
    assert "stt_final_to_request" in incompatible_metric_keys(records)


def test_source_local_durations_are_authoritative_and_cross_process_is_n_a() -> None:
    records = [
        {
            **_record("stt_request_started", 1000, 10_000),
            "process": "backend:1",
            "clock_domain": "backend_python_perf_counter",
        },
        {
            **_record("stt_request_completed", 1125, 10_125),
            "process": "backend:1",
            "clock_domain": "backend_python_perf_counter",
            "duration_ms": 125.0,
        },
        {
            **_record("llm_request_started", 1200, 10_200),
            "process": "backend:1",
            "clock_domain": "backend_python_perf_counter",
        },
        {
            **_record("llm_first_token_received", 1350, 10_350),
            "process": "backend:1",
            "clock_domain": "backend_python_perf_counter",
            "duration_ms": 150.0,
        },
        {
            **_record("llm_request_completed", 1500, 10_500),
            "process": "backend:1",
            "clock_domain": "backend_python_perf_counter",
            "duration_ms": 300.0,
        },
        {
            **_record("device_speech_end", 1000, 10_000, "android"),
            "process": "android:com.voiceaipoc",
        },
        {
            **_record("first_assistant_token_received", 2000, 10_000),
            "process": "backend:1",
        },
    ]
    values = metrics_for_turn(records)
    assert values["stt_request_duration"] == 125.0
    assert values["llm_ttft"] == 150.0
    assert values["llm_total"] == 300.0
    assert values["speech_end_to_first_token"] is None
    assert "speech_end_to_first_token" in incompatible_metric_keys(records)


def test_device_commit_and_vad_end_pair_only_with_compatible_clock_boundaries() -> None:
    android = {
        "clock_domain": "android_elapsed_realtime",
        "process": "android:com.voiceaipoc",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "response_id": "response-1",
    }
    rn = {
        "clock_domain": "react_native_performance",
        "process": "react_native",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "response_id": "response-1",
    }
    records = [
        {**_record("vad_end", 100.0, 10_100, "android_elapsed_realtime"), **android},
        {**_record("vad_end", 150.0, 10_150, "android_elapsed_realtime"), **android},
        {
            **_record("native_turn_commit_sent", 160.0, 10_160, "android_elapsed_realtime"),
            **android,
        },
        {**_record("vad_end", 190.0, 10_190, "android_elapsed_realtime"), **android},
        {
            **_record("first_assistant_token_received", 220.0, 10_220, "android_elapsed_realtime"),
            **android,
        },
        {
            **_record("tts_first_chunk_received", 260.0, 10_260, "android_elapsed_realtime"),
            **android,
        },
        {**_record("turn_commit_sent", 400.0, 10_400, "react_native_performance"), **rn},
        {**_record("client_stt_final_received", 430.0, 10_430, "react_native_performance"), **rn},
        {**_record("tts_playback_complete", 500.0, 10_500, "react_native_performance"), **rn},
    ]
    values = metrics_for_turn(records)
    assert values["speech_end_to_first_token"] == 70.0
    assert values["device_commit_to_first_text"] == 60.0
    assert values["device_commit_to_first_audio"] == 100.0
    assert values["device_commit_to_stt_final"] == 30.0
    assert values["device_commit_to_playback_complete"] == 100.0
    assert not {
        "speech_end_to_first_token",
        "device_commit_to_first_text",
        "device_commit_to_first_audio",
        "device_commit_to_stt_final",
        "device_commit_to_playback_complete",
    } & incompatible_metric_keys(records)


def test_invalid_local_duration_is_not_clamped_to_zero() -> None:
    records = [
        {
            **_record("llm_first_token_received", 1000, 10_000),
            "duration_ms": -1.0,
        }
    ]
    assert metrics_for_turn(records)["llm_ttft"] is None
    assert "llm_ttft" in incompatible_metric_keys(records)


def test_correlation_rejects_wrong_response_and_duplicate_tts() -> None:
    records = [
        {**_record("tts_request_started", 1000, 10_000), "response_id": "response-1"},
        {**_record("tts_request_started", 1000, 10_001), "response_id": "other"},
        {**_record("tts_playback_complete", 1100, 10_100), "response_id": "response-1"},
        {**_record("tts_playback_complete", 1100, 10_101), "response_id": "response-1"},
    ]
    errors = correlation_errors(records)
    assert "wrong_response_id" in errors
    assert "duplicate_tts_playback_complete" in errors


def test_multisegment_tts_is_one_response_and_serial_generation_is_summed() -> None:
    records = [
        {
            **_record("tts_request_started", 1000, 10_000),
            "response_id": "response-1",
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_first_audio_received", 1010, 10_010),
            "response_id": "response-1",
            "duration_ms": 10.0,
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_generation_completed", 1050, 10_050),
            "response_id": "response-1",
            "duration_ms": 50.0,
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_request_started", 1070, 10_070),
            "response_id": "response-1",
            "metadata": {"segment_index": 1},
        },
        {
            **_record("tts_first_audio_received", 1085, 10_085),
            "response_id": "response-1",
            "duration_ms": 15.0,
            "metadata": {"segment_index": 1},
        },
        {
            **_record("tts_generation_completed", 1130, 10_130),
            "response_id": "response-1",
            "duration_ms": 60.0,
            "metadata": {"segment_index": 1},
        },
    ]
    assert correlation_errors(records) == []
    values = metrics_for_turn(records)
    assert values["tts_ttfa"] == 10.0
    assert values["tts_generation_total"] == 110.0


def test_single_segment_and_gateway_response_aggregate_are_supported() -> None:
    records = [
        {
            **_record("tts_request_started", 1000, 10_000),
            "response_id": "response-1",
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_first_audio_received", 1010, 10_010),
            "response_id": "response-1",
            "duration_ms": 10.0,
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_generation_completed", 1050, 10_050),
            "response_id": "response-1",
            "duration_ms": 50.0,
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_response_metrics", 1060, 10_060),
            "response_id": "response-1",
            "duration_ms": 49.0,
            "metadata": {"segment_count": 1},
        },
    ]
    assert metrics_for_turn(records)["tts_ttfa"] == 10.0
    assert metrics_for_turn(records)["tts_generation_total"] == 49.0


def test_overlapping_tts_segment_intervals_are_not_summed() -> None:
    records = [
        {
            **_record("tts_request_started", 1000, 10_000),
            "response_id": "response-1",
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_generation_completed", 1050, 10_050),
            "response_id": "response-1",
            "duration_ms": 50.0,
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_request_started", 1040, 10_040),
            "response_id": "response-1",
            "metadata": {"segment_index": 1},
        },
        {
            **_record("tts_generation_completed", 1080, 10_080),
            "response_id": "response-1",
            "duration_ms": 40.0,
            "metadata": {"segment_index": 1},
        },
    ]
    assert metrics_for_turn(records)["tts_generation_total"] is None


def test_duplicate_tts_segment_is_detected_but_out_of_order_segment_is_not() -> None:
    records = [
        {
            **_record("tts_first_audio_received", 1010, 10_010),
            "response_id": "response-1",
            "duration_ms": 10.0,
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_request_started", 1000, 10_000),
            "response_id": "response-1",
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_request_started", 1070, 10_070),
            "response_id": "response-1",
            "metadata": {"segment_index": 1},
        },
        {
            **_record("tts_generation_completed", 1130, 10_130),
            "response_id": "response-1",
            "duration_ms": 60.0,
            "metadata": {"segment_index": 1},
        },
        {
            **_record("tts_request_started", 1140, 10_140),
            "response_id": "response-1",
            "metadata": {"segment_index": 1},
        },
    ]
    assert "duplicate_tts_request_started" in correlation_errors(records)


def test_delayed_out_of_order_segment_ids_are_correlated_by_response_and_time() -> None:
    records = [
        {
            **_record("tts_request_started", 1000, 10_000),
            "response_id": "response-1",
            "metadata": {"segment_index": 1},
        },
        {
            **_record("tts_generation_completed", 1050, 10_050),
            "response_id": "response-1",
            "duration_ms": 50.0,
            "metadata": {"segment_index": 1},
        },
        {
            **_record("tts_request_started", 1100, 10_100),
            "response_id": "response-1",
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_first_audio_received", 1110, 10_110),
            "response_id": "response-1",
            "duration_ms": 10.0,
            "metadata": {"segment_index": 0},
        },
        {
            **_record("tts_generation_completed", 1140, 10_140),
            "response_id": "response-1",
            "duration_ms": 40.0,
            "metadata": {"segment_index": 0},
        },
    ]
    assert correlation_errors(records) == []
    assert metrics_for_turn(records)["tts_ttfa"] == 10.0
    assert metrics_for_turn(records)["tts_generation_total"] == 90.0


def test_barge_in_diagnostics_do_not_create_response_mismatch() -> None:
    records = [
        {**_record("assistant.text.final", 1000, 10_000), "response_id": "response-1"},
        {**_record("barge_in_confirmed", 1001, 10_001), "response_id": "old-response"},
        {
            **_record("barge_in_playback_stop_requested", 1002, 10_002),
            "response_id": "old-response",
        },
        {**_record("tts_playback_complete", 1100, 10_100), "response_id": "response-1"},
    ]
    assert correlation_errors(records) == []


def test_cancelled_response_and_partial_turn_have_no_false_correlation_error() -> None:
    records = [
        {
            **_record("assistant.text.delta", 1000, 10_000),
            "turn_id": "old-turn",
            "response_id": "old-response",
        },
        {
            **_record("response.cancelled", 1010, 10_010),
            "turn_id": "old-turn",
            "response_id": "old-response",
        },
        {
            **_record("assistant.text.delta", 1020, 10_020),
            "turn_id": "new-turn",
            "response_id": "new-response",
        },
    ]
    assert correlation_errors(records[:2]) == []
    assert correlation_errors(records[2:]) == []


def test_accounting_counts_model_usage_and_retrieval_by_attempt() -> None:
    records = [
        {
            **_record("gateway_llm_request_observed", 1000, 10_000),
            "response_id": "response-1",
            "metadata": {
                "attempt": 1,
                "sequence": 0,
                "provider": "local-test",
                "configured_model": "model-a",
            },
        },
        {
            **_record("llm_provider_usage", 1100, 10_100),
            "response_id": "response-1",
            "metadata": {
                "attempt": 1,
                "sequence": 1,
                "input_tokens": 120,
                "output_tokens": 24,
                "total_tokens": 144,
            },
        },
        {
            **_record("gateway_llm_request_observed", 1200, 10_200),
            "response_id": "response-1",
            "metadata": {
                "attempt": 1,
                "tool_round": 2,
                "sequence": 0,
                "provider": "local-test",
                "configured_model": "model-a",
            },
        },
        {
            **_record("llm_provider_usage", 1300, 10_300),
            "response_id": "response-1",
            "metadata": {
                "attempt": 1,
                "tool_round": 2,
                "sequence": 1,
                "input_tokens": 130,
                "output_tokens": 24,
                "total_tokens": 154,
            },
        },
        _record("embedding_started", 1000, 10_000),
        _record("vector_search_started", 1001, 10_001),
        _record("fts_started", 1002, 10_002),
        _record("rrf_started", 1003, 10_003),
        _record("rerank_started", 1004, 10_004),
        _record("memory_context_pipeline_started", 1005, 10_005),
        _record("tool_start", 1006, 10_006),
        _record("tool_write_attempt", 1007, 10_007),
        _record("tool_confirmation_requested", 1008, 10_008),
        _record("tool_write_committed", 1009, 10_009),
    ]
    summary = accounting_for_turn(records)
    assert summary["main_model_calls"] == 2
    assert summary["model_rounds"] == 2
    assert summary["providers"] == ["local-test"]
    assert summary["models"] == ["model-a"]
    assert summary["input_tokens"] == 250
    assert summary["output_tokens"] == 48
    assert summary["total_tokens"] == 298
    assert summary["embedding_calls"] == 1
    assert summary["vector_retrieval_calls"] == 1
    assert summary["fts_calls"] == 1
    assert summary["rrf_executions"] == 1
    assert summary["reranker_calls"] == 1
    assert summary["memory_context_pipeline_calls"] == 1
    assert summary["tool_invocations"] == 1
    assert summary["write_attempts"] == 1
    assert summary["confirmation_requests"] == 1
    assert summary["confirmed_writes"] == 1


def test_accounting_reports_provider_usage_n_a_without_usage_event() -> None:
    summary = accounting_for_turn(
        [
            {
                **_record("gateway_llm_request_observed", 1000, 10_000),
                "response_id": "response-1",
                "metadata": {"attempt": 1, "sequence": 0, "provider": "local-test"},
            }
        ]
    )
    assert summary["main_model_calls"] == 1
    assert summary["input_tokens"] is None
    assert summary["output_tokens"] is None
    assert summary["token_usage_status"] == "n/a — provider usage unavailable"


def test_non_rag_turn_has_no_fake_retrieval_zeroes_and_memory_pipeline_is_named() -> None:
    records = [
        {
            **_record("memory_context_pipeline_completed", 1250, 10_250),
            "duration_ms": 250.0,
        }
    ]
    values = metrics_for_turn(records)
    assert values["memory_context_pipeline"] == 250.0
    assert values["embedding"] is None
    assert values["vector_search"] is None
    assert values["fts"] is None
    assert values["rrf"] is None
    assert values["rerank"] is None


def test_optional_cross_process_metrics_do_not_reject_valid_local_turn() -> None:
    records = [
        {
            **_record("llm_request_completed", 1200, 10_200),
            "process": "backend:1",
            "clock_domain": "backend_python_perf_counter",
            "duration_ms": 200.0,
            "response_id": "response-1",
        },
        {
            **_record("tts_playback_complete", 1300, 10_300, "android"),
            "process": "android:com.voiceaipoc",
            "response_id": "response-1",
            "duration_ms": 500.0,
        },
    ]
    assert correlation_errors(records) == []
    assert metrics_for_turn(records)["llm_total"] == 200.0
