from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.okf.policy import (
    map_memory_to_proposal,
    map_memory_to_proposals,
    normalize_segment,
    validate_canonical_key,
    validate_value,
)


def _memory(**overrides):
    values = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "status": "active",
        "memory_type": "preference",
        "subject": "user",
        "predicate": "response_style",
        "object_json": {"value": "concise"},
        "content": "Prefers concise responses.",
        "confidence": 0.91,
        "valid_from": datetime(2026, 1, 1, tzinfo=UTC),
        "valid_to": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_canonical_segments_follow_nfkc_casefold_and_separator_rules() -> None:
    assert normalize_segment("  Voice   Assistant_DB ") == "voice-assistant-db"
    assert validate_canonical_key("preferences/response-style") == "preferences/response-style"


@pytest.mark.parametrize(
    "key",
    [
        "preferences/Uppercase",
        "facts//empty",
        "unknown/value",
        "facts/name/too/many/segments/x/y/z/q",
    ],
)
def test_canonical_key_rejects_noncontract_paths(key: str) -> None:
    with pytest.raises(ValueError, match="okf_canonical_key_invalid"):
        validate_canonical_key(key)


def test_deterministic_mapping_uses_only_allowed_structured_fields() -> None:
    proposal = map_memory_to_proposal(_memory())

    assert proposal is not None
    assert proposal.concept_type == "preference"
    assert proposal.canonical_key == "preferences/response-style"
    assert proposal.value_json == {"value": "concise"}
    assert proposal.display_text == "concise"
    assert proposal.source_memory_id


def test_project_mapping_creates_parent_identity_and_allowlisted_child() -> None:
    proposals = map_memory_to_proposals(
        _memory(
            memory_type="project",
            subject="Voice Assistant",
            predicate="framework",
            object_json={"value": "FastAPI"},
        )
    )

    assert [proposal.canonical_key for proposal in proposals] == [
        "projects/voice-assistant",
        "projects/voice-assistant/framework",
    ]
    assert proposals[0].value_json == {"name": "Voice Assistant"}
    assert proposals[1].value_json == {"value": "FastAPI"}


def test_relationship_and_decision_keys_require_frozen_structured_markers() -> None:
    relationship = map_memory_to_proposal(
        _memory(
            memory_type="relationship",
            subject="Rahul",
            predicate="relationship",
            object_json={"target": "user", "kind": "colleague"},
        )
    )
    ordinary_fact = map_memory_to_proposal(
        _memory(
            memory_type="fact",
            subject="Project Atlas",
            predicate="database",
            object_json={"value": "PostgreSQL"},
        )
    )
    explicit_decision = map_memory_to_proposal(
        _memory(
            memory_type="fact",
            subject="Project Atlas",
            predicate="database",
            object_json={"value": "PostgreSQL", "decision": True},
        )
    )

    assert relationship is not None
    assert relationship.canonical_key == "relationships/rahul/user"
    assert ordinary_fact is not None and ordinary_fact.concept_type == "fact"
    assert ordinary_fact.canonical_key == "facts/project-atlas/database"
    assert explicit_decision is not None and explicit_decision.concept_type == "decision"
    assert explicit_decision.canonical_key == "projects/project-atlas/database"


@pytest.mark.parametrize(
    "memory",
    [
        _memory(memory_type="event"),
        _memory(memory_type="routine"),
        _memory(memory_type="summary"),
        _memory(predicate="unreviewed_predicate"),
        _memory(object_json=None),
        _memory(content="The secret=do-not-store token was exposed."),
        _memory(predicate="database_password"),
        _memory(subject="東京"),
    ],
)
def test_mapper_abstains_for_ineligible_or_sensitive_sources(memory) -> None:
    assert map_memory_to_proposal(memory) is None


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ({}, "okf_value_must_be_nonempty_object"),
        ({"nested": {"secret": "api_key=abc"}}, "memory_candidate_sensitive"),
        ({"nested": {"a": {"b": {"c": {"d": "too deep"}}}}}, "okf_value_too_deep"),
        ({"value": object()}, "memory_candidate_too_large"),
    ],
)
def test_value_policy_rejects_empty_sensitive_deep_and_non_json_values(value, reason: str) -> None:
    with pytest.raises(ValueError):
        validate_value(value)
