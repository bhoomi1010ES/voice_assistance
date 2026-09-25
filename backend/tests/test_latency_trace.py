from __future__ import annotations

import json
import time

from app.services.latency_trace import LatencyTracer, latency_span


def test_latency_trace_writes_correlated_monotonic_jsonl_and_redacts_secrets(tmp_path) -> None:
    path = tmp_path / "latency.jsonl"
    LatencyTracer(path).emit(
        session_id="session",
        turn_id="turn",
        response_id="response",
        component="stt",
        event="stt_final",
        monotonic_ms=123.4,
        duration_ms=9.5,
        metadata={
            "status": 200,
            "api_key": "do-not-write",
            "input_tokens": 123,
            "output_tokens": 17,
            "total_tokens": 140,
            "access_token": "must-stay-redacted",
        },
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["session_id"] == "session"
    assert record["turn_id"] == "turn"
    assert record["response_id"] == "response"
    assert record["monotonic_ms"] == 123.4
    assert record["monotonic_ns"] == 123_400_000
    assert record["duration_ms"] == 9.5
    assert record["metadata"]["api_key"] == "[redacted]"
    assert record["metadata"]["input_tokens"] == 123
    assert record["metadata"]["output_tokens"] == 17
    assert record["metadata"]["total_tokens"] == 140
    assert record["metadata"]["access_token"] == "[redacted]"


def test_latency_span_emits_high_resolution_duration_and_writer_cost(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    tracer = LatencyTracer(path)
    with latency_span(
        tracer.emit,
        component="orchestration",
        event="test_stage",
        session_id="session",
        turn_id="turn",
        response_id="response",
    ):
        time.sleep(0.001)
    tracer.emit(
        component="test",
        event="following_event",
        turn_id="turn",
    )

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["event"] for record in records[:2]] == [
        "test_stage_started",
        "test_stage_completed",
    ]
    assert records[0]["monotonic_ns"] < records[1]["monotonic_ns"]
    assert records[1]["duration_ms"] >= 1
    assert records[-1]["trace_writer_previous"]["event"] == "test_stage_completed"


def test_latency_trace_is_best_effort_when_parent_directory_is_unwritable(tmp_path) -> None:
    tracer = LatencyTracer(tmp_path / "trace.jsonl")
    tracer.emit(component="gateway", event="turn_received")
    assert (tmp_path / "trace.jsonl").exists()


def test_latency_trace_records_explicit_process_domain_and_negative_without_clamping(
    tmp_path,
) -> None:
    path = tmp_path / "trace.jsonl"
    LatencyTracer(path).emit(
        session_id="session",
        turn_id="turn",
        response_id="response",
        component="test",
        event="invalid_duration",
        duration_ms=-3.5,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["process"].startswith("backend:")
    assert record["clock_domain"] == "backend_python_perf_counter"
    assert record["wall_time_utc"] == record["timestamp"]
    assert record["duration_ms"] == -3.5
