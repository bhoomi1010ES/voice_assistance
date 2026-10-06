from __future__ import annotations

import itertools
import json
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.planning.policy import (
    AUTOMATIC_OPERATION_FIELDS,
    CONFIRMATION_OPERATIONS,
    PlanningConsent,
    automatic_operation_allowed,
    planning_capabilities,
)
from app.planning.service import recognize_mode_control, recognize_plan_selection
from app.services.recurrence import RecurrenceResolutionError, parse_recurrence_rule

OWNER = UUID("00000000-0000-0000-0000-000000000101")
OTHER = UUID("00000000-0000-0000-0000-000000000102")
SESSION = UUID("00000000-0000-0000-0000-000000000201")
FIXTURES = Path(__file__).parent / "fixtures" / "planning"
CORPUS = json.loads((FIXTURES / "conversations.json").read_text(encoding="utf-8"))
CONSENT = PlanningConsent(
    user_id=OWNER,
    session_id=SESSION,
    authenticated=True,
    session_active=True,
    mode="plan",
    state_version=2,
    expected_state_version=2,
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # Test defaults independently of developer shell and local secret dotenv.
    import os

    for key in os.environ:
        if key.startswith(("PLAN_", "LLM_", "MEMORY_", "OKF_", "KNOWLEDGE_")):
            monkeypatch.delenv(key)


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def enabled_settings(**overrides) -> Settings:
    values = {
        "plan_mode_enabled": True,
        "plan_extraction_mode": "on",
        "plan_auto_actions_enabled": True,
        "plan_test_user_ids": (OWNER,),
    }
    values.update(overrides)
    return settings(**values)


def test_default_and_example_settings_preserve_existing_behavior():
    example = Path(__file__).parents[2] / ".env.example"
    for config in (settings(), Settings(_env_file=example)):
        assert config.plan_mode_enabled is False
        assert config.plan_extraction_mode == "off"
        assert config.plan_auto_actions_enabled is False
        assert config.plan_test_user_ids == ()
        assert config.plan_extraction_timeout_ms == 3000
        assert config.plan_extraction_max_concurrent == 2
        assert config.plan_max_actions_per_turn == 8
        assert config.plan_policy_version == "plan-v1"
        result = planning_capabilities(config, CONSENT)
        assert not result.extract and not result.persist_proposals and not result.automatic_writes


def test_controls_can_be_enabled_without_extraction_or_owner_rollout():
    config = settings(plan_mode_enabled=True)
    result = planning_capabilities(config, CONSENT)
    assert config.plan_mode_enabled and config.plan_test_user_ids == ()
    assert not result.extract and not result.persist_proposals and not result.automatic_writes


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_extraction_configuration_loads_from_environment(monkeypatch, mode):
    monkeypatch.setenv("PLAN_MODE_ENABLED", "true")
    monkeypatch.setenv("PLAN_EXTRACTION_MODE", mode)
    monkeypatch.setenv("PLAN_TEST_USER_IDS", json.dumps([str(OWNER)]))
    monkeypatch.setenv("PLAN_EXTRACTION_TIMEOUT_MS", "1250")
    monkeypatch.setenv("PLAN_EXTRACTION_MAX_CONCURRENT", "3")
    monkeypatch.setenv("PLAN_MAX_ACTIONS_PER_TURN", "6")
    config = settings()
    assert config.plan_extraction_mode == mode
    assert config.plan_test_user_ids == (OWNER,)
    assert config.plan_extraction_timeout_ms == 1250
    assert config.plan_extraction_max_concurrent == 3
    assert config.plan_max_actions_per_turn == 6


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"plan_extraction_mode": "invalid"}, "plan_extraction_mode"),
        ({"plan_extraction_mode": "shadow"}, "requires PLAN_MODE_ENABLED"),
        (
            {"plan_mode_enabled": True, "plan_extraction_mode": "on"},
            "requires explicit PLAN_TEST_USER_IDS",
        ),
        ({"plan_auto_actions_enabled": True}, "PLAN_AUTO_ACTIONS_ENABLED requires"),
        (
            {
                "plan_mode_enabled": True,
                "plan_extraction_mode": "shadow",
                "plan_test_user_ids": (OWNER,),
                "plan_auto_actions_enabled": True,
            },
            "PLAN_AUTO_ACTIONS_ENABLED requires",
        ),
        ({"plan_test_user_ids": (OWNER, OWNER)}, "must not contain duplicates"),
        ({"plan_test_user_ids": (UUID(int=0),)}, "nil UUID"),
        ({"plan_test_user_ids": ("not-a-uuid",)}, "plan_test_user_ids"),
        ({"plan_test_user_ids": tuple(UUID(int=i) for i in range(1, 66))}, "plan_test_user_ids"),
        ({"plan_extraction_timeout_ms": 99}, "plan_extraction_timeout_ms"),
        ({"plan_extraction_timeout_ms": 30001}, "plan_extraction_timeout_ms"),
        ({"plan_extraction_max_concurrent": 0}, "plan_extraction_max_concurrent"),
        ({"plan_extraction_max_concurrent": 17}, "plan_extraction_max_concurrent"),
        ({"plan_max_actions_per_turn": 0}, "plan_max_actions_per_turn"),
        ({"plan_max_actions_per_turn": 9}, "plan_max_actions_per_turn"),
        ({"plan_policy_version": ""}, "plan_policy_version"),
        ({"plan_policy_version": "contains spaces"}, "plan_policy_version"),
        ({"plan_policy_version": "x" * 65}, "plan_policy_version"),
    ],
)
def test_unsafe_or_unbounded_configuration_is_rejected(overrides, reason):
    with pytest.raises(ValueError, match=reason):
        settings(**overrides)


@pytest.mark.parametrize(
    "feature,mode,auto", itertools.product([False, True], ["off", "shadow", "on"], [False, True])
)
def test_runtime_policy_rechecks_flags_even_for_invalid_or_changed_configuration(
    feature, mode, auto
):
    # Deliberately bypass startup validation to exercise runtime fail-closed gates.
    config = Settings.model_construct(
        plan_mode_enabled=feature,
        plan_extraction_mode=mode,
        plan_auto_actions_enabled=auto,
        plan_test_user_ids=(OWNER,),
    )
    result = planning_capabilities(config, CONSENT)
    assert result.extract is (feature and mode != "off")
    assert result.persist_proposals is (feature and mode == "on")
    assert result.automatic_writes is (feature and mode == "on" and auto)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"authenticated": False}, "inactive_authority"),
        ({"session_active": False}, "inactive_authority"),
        ({"user_id": OTHER}, "owner_not_allowlisted"),
        ({"mode": "normal"}, "no_consent"),
        ({"memory_excluded": True}, "private_session"),
        ({"state_version": 3}, "stale_consent"),
        ({"state_version": 0, "expected_state_version": 0}, "stale_consent"),
        ({"policy_version": "plan-v0"}, "stale_policy"),
    ],
)
@pytest.mark.parametrize("mode", ["shadow", "on"])
def test_authority_privacy_and_version_fail_closed(changes, reason, mode):
    config = enabled_settings(plan_extraction_mode=mode, plan_auto_actions_enabled=mode == "on")
    result = planning_capabilities(config, replace(CONSENT, **changes))
    assert result.reason == reason
    assert not result.extract and not result.persist_proposals and not result.automatic_writes


def test_shadow_and_proposal_only_modes_cannot_authorize_automatic_operations():
    shadow = planning_capabilities(
        enabled_settings(plan_extraction_mode="shadow", plan_auto_actions_enabled=False), CONSENT
    )
    proposals = planning_capabilities(enabled_settings(plan_auto_actions_enabled=False), CONSENT)
    assert shadow.extract and not shadow.persist_proposals
    assert proposals.extract and proposals.persist_proposals
    for capability in (shadow, proposals):
        for operation, fields in AUTOMATIC_OPERATION_FIELDS.items():
            assert not automatic_operation_allowed(capability, operation, fields)


def test_operation_policy_bounds_fields_and_keeps_confirmation_requirements():
    capability = planning_capabilities(enabled_settings(), CONSENT)
    for operation, fields in AUTOMATIC_OPERATION_FIELDS.items():
        assert automatic_operation_allowed(capability, operation, fields)
        assert not automatic_operation_allowed(capability, operation, fields | {"user_id"})
        assert not automatic_operation_allowed(capability, operation, fields | {"plan_id"})
        assert not automatic_operation_allowed(capability, operation, frozenset())
    for operation in CONFIRMATION_OPERATIONS | {"SEND_MESSAGE", "BOOK_TRAVEL", "PROMOTE_MEMORY"}:
        assert not automatic_operation_allowed(capability, operation, frozenset({"title"}))
    assert not automatic_operation_allowed(capability, "UPDATE_TASK", frozenset({"status"}))
    assert not automatic_operation_allowed(capability, "UPDATE_PLAN", frozenset({"status"}))
    with pytest.raises(TypeError):
        AUTOMATIC_OPERATION_FIELDS["DELETE_TASK"] = frozenset({"title"})


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
def test_frozen_conversation_labels_have_grounded_consistent_evidence(case):
    assert case["split"] in {"development", "held_out"}
    clock = datetime.fromisoformat(case["clock"])
    assert clock.utcoffset() is not None
    zone = ZoneInfo(case["timezone"])
    assert len({turn["id"] for turn in case["turns"]}) == len(case["turns"])
    for turn in case["turns"]:
        transcript = turn["transcript"]
        actions = turn["expected_actions"]
        assert len(actions) <= 8
        if case["initial"]["mode"] == "normal" or case["initial"]["memory_excluded"]:
            assert not actions
        if "retry_of" in turn:
            assert not actions
            assert turn["expected_replay_of"] == turn["retry_of"]
            assert any(original["id"] == turn["retry_of"] for original in case["turns"])
        if "expected_control" in turn:
            expected = {"enable": "plan", "disable": "normal"}.get(turn["expected_control"])
            assert recognize_mode_control(transcript) == expected
            if turn["expected_control"] == "select":
                assert recognize_plan_selection(transcript) is not None
        for action in actions:
            assert action["actor"] in {"user", "team", "context"}
            assert action["disposition"] in {"AUTO", "CONFIRM", "CLARIFY", "NO_ACTION", "DENY"}
            for key in ("source", "temporal_source"):
                if key not in action:
                    continue
                span = action[key]
                start, length = span["start"], span["length"]
                assert start >= 0 and length > 0 and start + length <= len(transcript)
                assert transcript[start : start + length] == span["text"]
            if action["disposition"] == "AUTO":
                assert action["operation"] in AUTOMATIC_OPERATION_FIELDS
            if action["disposition"] == "CONFIRM":
                assert action["operation"] in CONFIRMATION_OPERATIONS
            if action["disposition"] in {"CLARIFY", "DENY", "NO_ACTION"}:
                assert action.get("reason")
                assert "local_at" not in action
            if "local_at" in action:
                assert "temporal_source" in action
                local = datetime.fromisoformat(action["local_at"])
                instant = local.replace(tzinfo=zone).astimezone(UTC)
                assert instant > clock
                if action["operation"] in {"CREATE_TASK", "UPDATE_TASK"}:
                    assert local.strftime("%H:%M:%S") == "23:59:00"
                if "utc_at" in action:
                    assert instant == datetime.fromisoformat(action["utc_at"])
            if "recurrence" in action:
                parse_recurrence_rule(action["recurrence"])
            if "target_revision" in action:
                target = next(
                    target
                    for target in case["initial"]["owned_targets"]
                    if target["ref"] == action["target_ref"]
                )
                assert target["revision"] == action["target_revision"]


def test_corpus_split_coverage_and_lifecycle_labels():
    cases = CORPUS["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert sum(case["split"] == "held_out" for case in cases) == 13
    tags = {tag for case in cases for tag in case["tags"]}
    assert {
        "supplied",
        "multi_action",
        "context",
        "actor",
        "negation",
        "historical",
        "quote",
        "hypothetical",
        "question",
        "correction",
        "ambiguous_target",
        "ownership",
        "recurrence",
        "consent",
        "privacy",
        "identity",
        "replay",
        "midnight",
        "duplicate",
        "dst",
        "overflow",
    } <= tags
    lifecycle = json.loads((FIXTURES / "lifecycle.json").read_text(encoding="utf-8"))["cases"]
    assert {
        "new_session",
        "supported_same_session_resume",
        "disable",
        "reset",
        "end",
        "timeout",
        "logout",
        "device_or_auth_revoked",
        "expired_resume",
        "private_session_enabled",
        "source_content_purge",
    } <= {case["event"] for case in lifecycle}
    for case in lifecycle:
        assert case["saved_resources_retained"]
        assert (case["expected_mode"] == "plan") == case["consent_retained"]


def test_frozen_fixture_manifest_requires_explicit_corpus_revision():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["policy_version"] == "plan-v1"
    assert manifest["schema_version"] == CORPUS["schema_version"]
    for filename, expected_digest in manifest["canonical_json_sha256"].items():
        data = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert sha256(canonical.encode("utf-8")).hexdigest() == expected_digest


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=DAILY",
        "FREQ=WEEKLY;BYDAY=MO,FR",
        "FREQ=DAILY;INTERVAL=366;COUNT=366",
        "FREQ=WEEKLY;UNTIL=20261231",
        "FREQ=DAILY;UNTIL=20261231T235900Z",
    ],
)
def test_frozen_supported_recurrence_subset(rule):
    parse_recurrence_rule(rule)


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=MONTHLY",
        "FREQ=DAILY;BYDAY=MO",
        "FREQ=WEEKLY;BYDAY=1MO",
        "FREQ=DAILY;COUNT=0",
        "FREQ=DAILY;INTERVAL=367",
        "FREQ=DAILY;COUNT=367",
        "FREQ=DAILY;FREQ=DAILY",
        "FREQ=DAILY;BYMONTHDAY=1",
        "FREQ=DAILY;UNTIL=not-a-date",
        "FREQ=DAILY;COUNT=",
    ],
)
def test_frozen_unsupported_recurrence_is_not_approximated(rule):
    with pytest.raises(RecurrenceResolutionError):
        parse_recurrence_rule(rule)
