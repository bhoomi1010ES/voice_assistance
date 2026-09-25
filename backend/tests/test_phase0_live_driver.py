from __future__ import annotations

from scripts.phase0_live_driver import (
    PhysicalBaseline,
    is_active_barge_event,
    voice_control_from_xml,
)


def test_voice_control_from_xml_reads_label_and_center() -> None:
    xml = (
        '<hierarchy><node resource-id="voice-control" '
        'content-desc="Start turn" bounds="[354,549][726,921]" /></hierarchy>'
    )

    assert voice_control_from_xml(xml) == ("Start turn", (540, 735))


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


def test_response_not_active_lifecycle_request_is_not_a_physical_barge_in() -> None:
    assert not is_active_barge_event(
        {
            "event": "barge_in_playback_stop_requested",
            "metadata": {"reason": "response_not_active", "playback_active": False},
        }
    )


def test_confirmed_barge_in_remains_an_anomaly() -> None:
    assert is_active_barge_event(
        {"event": "barge_in_confirmed", "metadata": {"playback_active": False}}
    )


def test_degraded_event_with_active_playback_remains_an_anomaly() -> None:
    assert is_active_barge_event(
        {"event": "barge_in_degraded", "metadata": {"playback_active": True}}
    )


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
