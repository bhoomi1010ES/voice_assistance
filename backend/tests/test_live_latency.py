from __future__ import annotations

from pathlib import Path

from scripts.analyze_latency import _record_key
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
        None,
        None,
    )
    assert record_key(record) == _record_key(record)


def test_record_key_keeps_segment_identity_when_timestamps_collide() -> None:
    base = {
        "event": "tts_first_audio_received",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "response_id": "response-1",
        "timestamp_ms": 100,
        "monotonic_ms": 200,
        "monotonic_ns": 200_000_000,
    }
    first = {**base, "metadata": {"segment_index": 0}}
    second = {**base, "metadata": {"segment_index": 1}}
    assert record_key(first) != record_key(second)


def test_logcat_command_starts_at_live_end_without_device_clock_or_clearing() -> None:
    command = build_logcat_command("adb", start_at_now=True)
    assert command == [
        "adb",
        "logcat",
        "-b",
        "all",
        "-T",
        "0",
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


def _collector(tmp_path: Path) -> LiveCollector:
    return LiveCollector(
        backend_trace=tmp_path / "backend.jsonl",
        output=tmp_path / "live.jsonl",
        metro_log=None,
        adb_command="adb",
        clear_logcat=False,
        from_start=False,
        once=False,
    )


def test_normal_response_and_multiple_tts_segments_share_response(tmp_path: Path) -> None:
    collector = _collector(tmp_path)
    try:
        for event, sequence in (
            ("assistant.text.delta", 1),
            ("tts_request_started", 2),
            ("tts_first_audio_received", 3),
            ("tts_request_started", 4),
            ("tts_first_audio_received", 5),
            ("tts_playback_complete", 6),
        ):
            collector.accept(
                {
                    "event": event,
                    "session_id": "session-1",
                    "turn_id": "turn-1",
                    "response_id": "response-1",
                    "metadata": {"segment_index": sequence // 2},
                    "monotonic_ns": sequence,
                },
                source="backend",
            )
        assert collector.rejected_records == []
        assert len(collector.records["turn-1"]) == 6
    finally:
        collector.close()


def test_barge_in_lifecycle_does_not_claim_replacement_response(tmp_path: Path) -> None:
    collector = _collector(tmp_path)
    try:
        collector.accept(
            {
                "event": "barge_in_confirmed",
                "session_id": "session-1",
                "turn_id": "old-turn",
                "response_id": "old-response",
            },
            source="android",
        )
        collector.accept(
            {
                "event": "turn_started",
                "session_id": "session-1",
                "turn_id": "new-turn",
                "response_id": "new-response",
            },
            source="backend",
        )
        collector.accept(
            {
                "event": "tts_playback_complete",
                "session_id": "session-1",
                "turn_id": "new-turn",
                "response_id": "new-response",
            },
            source="android",
        )
        assert collector.rejected_records == []
        assert collector.turn_response_ids == {"new-turn": "new-response"}
        assert ("session-1", "old-response") in collector.cancelled_response_ids
    finally:
        collector.close()


def test_completed_playback_barge_in_marker_does_not_cancel_response(tmp_path: Path) -> None:
    collector = _collector(tmp_path)
    try:
        collector.accept(
            {
                "event": "barge_in_confirmed",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "response_id": "response-1",
                "metadata": {"playback_active": False, "playback_state": "COMPLETED"},
            },
            source="android",
        )
        collector.accept(
            {
                "event": "tool_write_committed",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "response_id": "response-1",
                "metadata": {"tool_name": "create_task"},
            },
            source="backend",
        )

        assert collector.rejected_records == []
        assert collector.records["turn-1"][-1]["event"] == "tool_write_committed"
        assert ("session-1", "response-1") not in collector.cancelled_response_ids
    finally:
        collector.close()


def test_stale_cancelled_tts_and_unrelated_response_are_rejected(tmp_path: Path) -> None:
    collector = _collector(tmp_path)
    try:
        collector.accept(
            {
                "event": "turn_started",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "response_id": "response-1",
            },
            source="backend",
        )
        collector.accept(
            {
                "event": "response.cancelled",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "response_id": "response-1",
            },
            source="android",
        )
        collector.accept(
            {
                "event": "tts_first_audio_received",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "response_id": "response-1",
            },
            source="backend",
        )
        assert collector.rejected_records[-1]["metadata"]["collector_rejection"] == (
            "stale_cancelled_response"
        )

        collector.accept(
            {
                "event": "tts_first_audio_received",
                "session_id": "session-1",
                "turn_id": "turn-2",
                "response_id": "response-2",
            },
            source="backend",
        )
        collector.accept(
            {
                "event": "tts_playback_complete",
                "session_id": "session-1",
                "turn_id": "turn-3",
                "response_id": "response-2",
            },
            source="android",
        )
        assert collector.rejected_records[-1]["metadata"]["collector_rejection"] == (
            "wrong_turn_id"
        )
    finally:
        collector.close()


def test_cancellation_lifecycle_is_owned_by_old_response_and_replacement_turn(
    tmp_path: Path,
) -> None:
    collector = _collector(tmp_path)
    try:
        collector.accept(
            {"event": "turn_started", "session_id": "s", "turn_id": "old", "response_id": "r1"},
            source="backend",
        )
        collector.accept(
            {
                "event": "response.cancelled",
                "session_id": "s",
                "turn_id": "old",
                "response_id": "r1",
            },
            source="android",
        )
        collector.accept(
            {"event": "turn_started", "session_id": "s", "turn_id": "new", "response_id": "r2"},
            source="backend",
        )
        collector.accept(
            {
                "event": "tts_first_audio_received",
                "session_id": "s",
                "turn_id": "old",
                "response_id": "r1",
            },
            source="backend",
        )
        collector.accept(
            {
                "event": "tts_first_audio_received",
                "session_id": "s",
                "turn_id": "new",
                "response_id": "r2",
            },
            source="backend",
        )
        assert collector.rejected_records[-1]["metadata"]["collector_rejection"] == (
            "stale_cancelled_response"
        )
        assert [record["event"] for record in collector.records["new"]] == [
            "turn_started",
            "tts_first_audio_received",
        ]
        assert collector.turn_response_ids == {"old": "r1", "new": "r2"}
    finally:
        collector.close()


def test_distinct_sessions_can_reuse_response_id_without_cross_turn_rejection(
    tmp_path: Path,
) -> None:
    collector = _collector(tmp_path)
    try:
        for session, turn in (("s1", "t1"), ("s2", "t2")):
            collector.accept(
                {
                    "event": "tts_playback_complete",
                    "session_id": session,
                    "turn_id": turn,
                    "response_id": "reused",
                },
                source="android",
            )
        assert collector.rejected_records == []
        assert len(collector.records["t1"]) == len(collector.records["t2"]) == 1
    finally:
        collector.close()


def test_cancelled_response_id_is_scoped_to_its_session(tmp_path: Path) -> None:
    collector = _collector(tmp_path)
    try:
        collector.accept(
            {
                "event": "response.cancelled",
                "session_id": "s1",
                "turn_id": "t1",
                "response_id": "same",
            },
            source="android",
        )
        collector.accept(
            {
                "event": "tts_first_audio_received",
                "session_id": "s2",
                "turn_id": "t2",
                "response_id": "same",
            },
            source="backend",
        )
        assert collector.rejected_records == []
        assert len(collector.records["t2"]) == 1
    finally:
        collector.close()


def test_diagnostic_barge_in_event_does_not_hide_genuine_wrong_response(tmp_path: Path) -> None:
    collector = _collector(tmp_path)
    try:
        collector.accept(
            {
                "event": "assistant.text.delta",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "response_id": "response-1",
            },
            source="backend",
        )
        collector.accept(
            {
                "event": "barge_in_playback_stop_requested",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "response_id": "previous-response",
            },
            source="android",
        )
        collector.accept(
            {
                "event": "tts_first_audio_received",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "response_id": "wrong-response",
            },
            source="backend",
        )
        assert len(collector.records["turn-1"]) == 2
        assert collector.rejected_records[-1]["metadata"]["collector_rejection"] == (
            "wrong_response_id"
        )
    finally:
        collector.close()
