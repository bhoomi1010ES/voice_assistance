from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from scripts.phase0_live_driver import (
    PhysicalBaseline,
    check_acoustic_output_route,
    is_active_barge_event,
    is_current_session_heartbeat,
    is_known_acoustic_path_defect,
    is_native_session_ready_event,
    is_pre_prompt_speech_activity,
    is_session_ready_event,
    normalized_phrase_contains,
    scrollable_view_bounds_from_xml,
    session_pcm_observed,
    transcript_match,
    turn_pcm_observed,
    voice_control_from_xml,
)


def test_live_manifest_finishes_sixteen_read_only_turns_before_write_flows() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "scripts" / "phase0_live_manifest.json"
    cases = json.loads(manifest_path.read_text(encoding="utf-8"))["cases"]
    first_write = next(index for index, case in enumerate(cases) if case.get("confirmation"))

    assert 15 <= first_write <= 20
    assert all(not case.get("confirmation") for case in cases[:first_write])
    assert all(case.get("confirmation") for case in cases[first_write:])
    assert first_write == 16


def test_voice_control_from_xml_reads_label_and_center() -> None:
    xml = (
        '<hierarchy><node resource-id="voice-control" '
        'content-desc="Start turn" bounds="[354,549][726,921]" /></hierarchy>'
    )

    assert voice_control_from_xml(xml) == ("Start turn", (540, 735))


def test_voice_control_from_xml_normalizes_accessibility_ellipsis() -> None:
    xml = (
        '<hierarchy><node resource-id="voice-control" '
        'content-desc="Listening…" bounds="[354,549][726,921]" /></hierarchy>'
    )

    assert voice_control_from_xml(xml) == ("Listening", (540, 735))


def test_scrollable_view_bounds_uses_android_viewport_bounds() -> None:
    xml = (
        '<hierarchy><node class="android.widget.ScrollView" scrollable="true" '
        'bounds="[72,445][1008,2124]" /></hierarchy>'
    )

    assert scrollable_view_bounds_from_xml(xml) == (72, 445, 1008, 2124)


def test_voice_control_from_xml_ignores_other_nodes() -> None:
    xml = (
        '<hierarchy><node resource-id="other" content-desc="Start turn" '
        'bounds="[0,0][1,1]" /></hierarchy>'
    )

    assert voice_control_from_xml(xml) is None


def test_voice_control_from_xml_rejects_malformed_tree() -> None:
    assert voice_control_from_xml("<hierarchy><node") is None


def test_voice_control_from_xml_requires_visible_bounds_and_label() -> None:
    xml = '<hierarchy><node resource-id="voice-control" content-desc="" bounds="bad" /></hierarchy>'

    assert voice_control_from_xml(xml) is None


def test_pre_prompt_pcm_requires_a_frame_from_the_current_session() -> None:
    records = [
        {"event": "first_pcm_received", "session_id": "old-session"},
        {"event": "server.pong", "session_id": "current-session"},
        {"event": "first_pcm_received", "session_id": "current-session"},
    ]

    assert session_pcm_observed(records, "current-session")
    assert not session_pcm_observed(records[:2], "current-session")
    assert not session_pcm_observed(records, "another-session")


def test_turn_pcm_observed_matches_the_current_session_and_turn() -> None:
    records = [
        {
            "event": "first_pcm_received",
            "session_id": "current-session",
            "turn_id": "old-turn",
        },
        {
            "event": "turn_commit_received",
            "session_id": "old-session",
            "turn_id": "current-turn",
            "metadata": {"frame_count": 12},
        },
        {
            "event": "first_pcm_received",
            "session_id": "current-session",
            "turn_id": "current-turn",
        },
    ]

    assert turn_pcm_observed(records, "current-session", "current-turn")
    assert not turn_pcm_observed(records, "current-session", "other-turn")
    assert not turn_pcm_observed(records, "other-session", "current-turn")


def test_turn_commit_frame_count_corroborates_pcm_for_the_same_turn() -> None:
    records = [
        {
            "event": "turn_commit_received",
            "session_id": "current-session",
            "turn_id": "current-turn",
            "metadata": {"frame_count": 192, "byte_count": 122880},
        }
    ]

    assert turn_pcm_observed(records, "current-session", "current-turn")
    records[0]["metadata"]["frame_count"] = 0
    assert not turn_pcm_observed(records, "current-session", "current-turn")


def test_transcript_match_accepts_cleanup_as_two_stt_words() -> None:
    matched, similarity = transcript_match(
        "Remind me to perform Phase Zero test cleanup tomorrow at 9 AM.",
        "Remind me to perform phase zero test clean up tomorrow at 9am.",
        ["phase", "zero", "test", "cleanup"],
    )

    assert matched
    assert similarity >= 0.98


def test_normalized_phrase_contains_accepts_cleanup_title_variant() -> None:
    assert normalized_phrase_contains(
        "Perform phase zero test clean up",
        "Phase Zero test cleanup",
    )


def test_readiness_requires_a_current_generation_and_same_session_heartbeat() -> None:
    ready = {
        "event": "voice_lifecycle_session_ready",
        "session_id": "current-session",
        "metadata": {"connection_generation": 7},
    }
    heartbeat = {
        "event": "voice_lifecycle_session_heartbeat",
        "session_id": "current-session",
        "metadata": {"connection_generation": 7},
    }

    assert is_session_ready_event(ready)
    assert is_current_session_heartbeat(heartbeat, "current-session", 7)
    assert not is_current_session_heartbeat(heartbeat, "other-session", 7)
    heartbeat["metadata"]["connection_generation"] = 6
    assert not is_current_session_heartbeat(heartbeat, "current-session", 7)


def test_native_current_status_can_establish_ready_session() -> None:
    status = {
        "event": "voice_lifecycle_native_status",
        "session_id": "current-session",
        "metadata": {
            "connection_generation": 7,
            "connection_state": "connected",
            "session_state": "ready",
            "heartbeat_state": "healthy",
            "session_id": "current-session",
        },
    }

    assert is_native_session_ready_event(status)
    assert is_current_session_heartbeat(status, "current-session", 7)
    status["metadata"]["heartbeat_state"] = "unhealthy"
    assert not is_current_session_heartbeat(status, "current-session", 7)
    status["metadata"]["heartbeat_state"] = "healthy"
    status["metadata"]["session_id"] = "stale-session"
    assert not is_native_session_ready_event(status)


def test_unprompted_speech_activity_is_detected_only_for_current_session() -> None:
    event = {"event": "microphone_speech_start", "session_id": "current-session"}

    assert is_pre_prompt_speech_activity(event, "current-session")
    assert not is_pre_prompt_speech_activity(event, "other-session")


def test_response_not_active_lifecycle_request_is_not_a_physical_barge_in() -> None:
    assert not is_active_barge_event(
        {
            "event": "barge_in_playback_stop_requested",
            "metadata": {"reason": "response_not_active", "playback_active": False},
        }
    )


def test_normal_speech_without_active_playback_is_not_a_barge_in() -> None:
    assert not is_active_barge_event(
        {"event": "barge_in_confirmed", "metadata": {"playback_active": False}}
    )


def test_confirmed_barge_in_with_active_playback_remains_an_anomaly() -> None:
    assert is_active_barge_event(
        {"event": "barge_in_confirmed", "metadata": {"playback_active": True}}
    )


def test_replacement_turn_ready_marker_is_informational() -> None:
    assert not is_active_barge_event(
        {"event": "barge_in_replacement_turn_ready", "metadata": {}}
    )


async def test_microphone_preflight_defers_pcm_validation_to_scripted_turn() -> None:
    baseline = object.__new__(PhysicalBaseline)
    baseline.args = SimpleNamespace(manual_prompts=False)
    baseline.environment = {}
    baseline.ensure_input_turn = AsyncMock()
    baseline.collect_new = lambda: []
    baseline._guard_events = lambda _records, _turn_id: None
    baseline._guard_pre_prompt_speech = lambda _records: None
    baseline._check_run_health = lambda: None

    await baseline.verify_microphone_before_speech()

    baseline.ensure_input_turn.assert_awaited_once()
    assert baseline.environment["microphone_pcm"].startswith("must be observed")


def test_degraded_event_with_active_playback_remains_an_anomaly() -> None:
    assert is_active_barge_event(
        {"event": "barge_in_degraded", "metadata": {"playback_active": True}}
    )


def test_no_safe_acoustic_path_is_recorded_as_known_nonblocking_baseline_defect() -> None:
    record = {
        "event": "barge_in_degraded",
        "metadata": {"reason": "no_safe_acoustic_path", "playback_active": True},
    }

    assert is_known_acoustic_path_defect(record)
    assert not is_active_barge_event(record)


def test_acoustic_route_preflight_accepts_earpiece_or_wired_headphones(monkeypatch) -> None:
    import scripts.phase0_live_driver as driver

    monkeypatch.setattr(driver.shutil, "which", lambda _: "adb.exe")
    monkeypatch.setattr(
        driver,
        "run_command",
        lambda *_args, **_kwargs: (
            "Active communication device: role:output type:earpiece addr:null"
        ),
    )

    assert check_acoustic_output_route("phone") == "earpiece"


def test_acoustic_route_preflight_rejects_loudspeaker(monkeypatch) -> None:
    import scripts.phase0_live_driver as driver
    from scripts.phase0_live_driver import BaselineAbort

    monkeypatch.setattr(driver.shutil, "which", lambda _: "adb.exe")
    monkeypatch.setattr(
        driver,
        "run_command",
        lambda *_args, **_kwargs: "Active communication device: role:output type:speaker addr:null",
    )

    try:
        check_acoustic_output_route("phone")
    except BaselineAbort as error:
        assert "No prompt was spoken" in str(error)
    else:
        raise AssertionError("speaker route must block baseline capture")


def test_phase0_gate_requires_paired_metrics_and_both_confirmed_writes() -> None:
    categories = [
        "general_llm",
        "current_time",
        "current_date",
        "structured_task_read",
        "structured_reminder_read",
        "memory_query",
        "memory_query_missing",
        "confirmed_task_write",
        "confirmed_task_cleanup",
    ]
    rows = [
        {"case_id": f"repeat-{index}", "legacy_route_category": category}
        for index, category in enumerate(categories)
    ]
    rows.extend(
        {"case_id": f"repeat-{index}", "legacy_route_category": "general_llm"} for index in range(9)
    )
    rows.extend(
        [
            {
                "case_id": "P0-LIVE-013",
                "legacy_route_category": "confirmed_task_write",
                "pending_confirmation_verified_before_speech": True,
                "write_attempts": 1,
                "confirmed_writes": 1,
                "write_commits": 1,
                "unconfirmed_writes": 0,
                "duplicate_writes_prevented": 0,
            },
            {
                "case_id": "P0-LIVE-016",
                "legacy_route_category": "confirmed_task_cleanup",
                "pending_confirmation_verified_before_speech": True,
                "cleanup_verified": True,
                "write_attempts": 1,
                "confirmed_writes": 1,
                "write_commits": 1,
                "unconfirmed_writes": 0,
                "duplicate_writes_prevented": 0,
            },
        ]
    )
    summary = {
        "outcome": "complete",
        "valid_completed_physical_turns": 20,
        "excluded_turns": 0,
        "lifecycle_anomaly_count": 0,
        "langgraph_router_calls": 0,
        "router_mode": "off",
        "router_cohort_percent": 0,
        "metric_statistics": {
            key: {"count": 20}
            for key in (
                "backend_speech_end_to_first_text",
                "backend_speech_end_to_first_audio",
                "backend_complete_turn",
            )
        },
        "rows": rows,
        "route_totals": {},
    }
    assert PhysicalBaseline.phase0_gate(summary)

    summary["metric_statistics"]["backend_complete_turn"]["count"] = 0
    assert not PhysicalBaseline.phase0_gate(summary)


def test_incomplete_prompt_is_recorded_as_excluded_without_transcript(tmp_path) -> None:
    baseline = object.__new__(PhysicalBaseline)
    baseline.rows = []
    baseline.row_path = tmp_path / "rows.jsonl"
    baseline.session_id = "session-1"
    baseline.append_incomplete_case(
        {"case_id": "P0-LIVE-TEST", "category": "general_llm", "text": "Tell a short joke."},
        [
            {
                "event": "turn_started",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "response_id": "response-1",
            }
        ],
        "playback timeout",
    )
    assert baseline.rows[0]["completion_status"] == "excluded_incomplete_turn"
    assert baseline.rows[0]["exclusion_reason"] == "playback timeout"
    assert baseline.rows[0]["turn_id"] == "turn-1"
    assert "stt_final_transcript" not in baseline.rows[0]
