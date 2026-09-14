from __future__ import annotations

from pathlib import Path

from scripts.live_latency import FileTail, parse_log_line, record_key


def test_parse_log_line_extracts_trace_without_logging_pcm() -> None:
    record = parse_log_line(
        '09-14 20:00:00.000 I/Voice: LATENCY_TRACE '
        '{"event":"stt_final","turn_id":"turn-1","metadata":{"audio_bytes":10}}'
    )
    assert record == {
        "event": "stt_final",
        "turn_id": "turn-1",
        "metadata": {"audio_bytes": 10},
    }


def test_file_tail_waits_for_complete_jsonl_lines(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text('{"event":"speech_start"', encoding="utf-8")
    tail = FileTail(path, from_end=False)
    assert tail.poll() == []
    with path.open("a", encoding="utf-8") as stream:
        stream.write('}\n')
    assert tail.poll() == [{"event": "speech_start"}]


def test_record_key_matches_offline_merge_identity() -> None:
    record = {
        "clock_domain": "backend",
        "event": "stt_final",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "response_id": "response-1",
        "timestamp_ms": 100,
        "monotonic_ms": 200,
    }
    assert record_key(record) == (
        "backend",
        "stt_final",
        "session-1",
        "turn-1",
        "response-1",
        100,
        200,
    )
