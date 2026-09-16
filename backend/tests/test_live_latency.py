from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.live_latency import (
    FileTail,
    LiveCollector,
    build_logcat_command,
    parse_log_line,
    record_key,
)


def test_parse_log_line_extracts_trace_without_logging_pcm() -> None:
    record = parse_log_line(
        "09-14 20:00:00.000 I/Voice: LATENCY_TRACE "
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
        stream.write("}\n")
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
        "monotonic_ns": 200_000_000,
    }
    assert record_key(record) == (
        "backend",
        "stt_final",
        "session-1",
        "turn-1",
        "response-1",
        100,
        200,
        200_000_000,
    )


def test_logcat_command_starts_at_current_device_time_without_clearing() -> None:
    with patch(
        "scripts.live_latency.subprocess.run",
        return_value=SimpleNamespace(stdout="09-15_14:17:22.924\n"),
    ) as run:
        command = build_logcat_command("adb", start_at_now=True)

    run.assert_called_once_with(
        ["adb", "shell", "date", "+%m-%d_%H:%M:%S.%3N"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert command == [
        "adb",
        "logcat",
        "-b",
        "all",
        "-T",
        "09-15 14:17:22.924",
        "-v",
        "threadtime",
    ]


def test_collector_rejects_wrong_response_and_turn_ids(tmp_path: Path) -> None:
    collector = LiveCollector(
        backend_trace=tmp_path / "backend.jsonl",
        output=tmp_path / "live.jsonl",
        metro_log=None,
        adb_command="adb",
        clear_logcat=False,
        from_start=False,
        once=False,
    )
    try:
        collector.accept(
            {
                "event": "turn_ready_received",
                "turn_id": "turn-1",
                "response_id": "response-1",
            },
            source="backend",
        )
        collector.accept(
            {
                "event": "llm_first_token_received",
                "turn_id": "turn-1",
                "response_id": "response-old",
            },
            source="android",
        )
        collector.accept(
            {
                "event": "tts_playback_completed",
                "turn_id": "turn-2",
                "response_id": "response-1",
            },
            source="android",
        )
        assert len(collector.records["turn-1"]) == 1
        assert collector.rejected_records[0]["metadata"]["collector_rejection"] == (
            "wrong_response_id"
        )
        assert collector.rejected_records[1]["metadata"]["collector_rejection"] == ("wrong_turn_id")
    finally:
        collector.close()
