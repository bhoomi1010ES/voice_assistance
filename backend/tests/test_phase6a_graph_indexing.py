from __future__ import annotations

from app.graph.policy import (
    GRAPH_INDEX_POLICY_VERSION,
    GRAPH_RELATIONSHIP_POLICIES,
    derive_relationship_spec,
)


def test_typed_relationship_is_derived_deterministically() -> None:
    spec, reason = derive_relationship_spec(
        memory_type="relationship",
        subject="Rahul",
        predicate="works_on",
        object_json={"name": "Project Alpha", "type": "project"},
    )

    assert reason is None
    assert spec is not None
    assert spec.source_name == "Rahul"
    assert spec.source_entity_type == "person"
    assert spec.relationship_type == "WORKS_ON"
    assert spec.target_name == "Project Alpha"
    assert spec.target_entity_type == "project"
    assert GRAPH_INDEX_POLICY_VERSION == "v1"


def test_known_relationship_contract_can_type_value_target_without_guessing() -> None:
    spec, reason = derive_relationship_spec(
        memory_type="relationship",
        subject="Rahul",
        predicate="works_on",
        object_json={"value": "Project Alpha"},
    )

    assert reason is None
    assert spec is not None and spec.target_entity_type == "project"


def test_generic_extractor_relationship_predicate_is_not_graphable() -> None:
    spec, reason = derive_relationship_spec(
        memory_type="relationship",
        subject="Rahul",
        predicate="relationship",
        object_json={"value": "my colleague"},
    )

    assert spec is None
    assert reason == "unsupported_predicate"


def test_scalar_preference_and_untyped_targets_do_not_create_entities() -> None:
    scalar_preference, preference_reason = derive_relationship_spec(
        memory_type="preference",
        subject="user",
        predicate="preference",
        object_json={"value": "coffee"},
    )
    untyped_target, target_reason = derive_relationship_spec(
        memory_type="relationship",
        subject="Rahul",
        predicate="responsible_for",
        object_json={"name": "Project Alpha"},
    )

    assert scalar_preference is None
    assert preference_reason == "unsupported_memory_type"
    assert untyped_target is None
    assert target_reason == "invalid_entity_type"


def test_unsupported_predicate_and_wrong_known_target_type_are_skipped() -> None:
    unsupported, unsupported_reason = derive_relationship_spec(
        memory_type="relationship",
        subject="Rahul",
        predicate="likes",
        object_json={"name": "Coffee", "type": "thing"},
    )
    wrong_type, wrong_type_reason = derive_relationship_spec(
        memory_type="relationship",
        subject="Rahul",
        predicate="works_on",
        object_json={"name": "Project Alpha", "type": "person"},
    )

    assert unsupported is None
    assert unsupported_reason == "unsupported_predicate"
    assert wrong_type is None
    assert wrong_type_reason == "invalid_entity_type"


def test_relationship_allowlist_is_explicit_and_does_not_map_unknown_values() -> None:
    assert set(GRAPH_RELATIONSHIP_POLICIES) == {
        "works_on",
        "responsible_for",
        "tested_by",
        "manages",
        "reports_to",
        "member_of",
        "assigned_to",
        "depends_on",
        "blocked_by",
        "discussed_with",
    }
    assert all(
        policy.relationship_type != "RELATED_TO" for policy in GRAPH_RELATIONSHIP_POLICIES.values()
    )
