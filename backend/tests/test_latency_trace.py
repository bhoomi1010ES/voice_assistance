from __future__ import annotations

import json

from app.services.latency_trace import LatencyTracer


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
        metadata={"status": 200, "api_key": "do-not-write"},
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["session_id"] == "session"
    assert record["turn_id"] == "turn"
    assert record["response_id"] == "response"
    assert record["monotonic_ms"] == 123.4
    assert record["duration_ms"] == 9.5
    assert record["metadata"]["api_key"] == "[redacted]"


def test_latency_trace_is_best_effort_when_parent_directory_is_unwritable(tmp_path) -> None:
    tracer = LatencyTracer(tmp_path / "trace.jsonl")
    tracer.emit(component="gateway", event="turn_received")
    assert (tmp_path / "trace.jsonl").exists()
