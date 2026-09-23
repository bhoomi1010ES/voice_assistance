from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.graph.types import RelationshipType
from app.memory.types import normalize_memory_text

GRAPH_INDEX_POLICY_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class GraphRelationshipPolicy:
    relationship_type: str
    source_entity_type: str | None
    target_entity_type: str | None


@dataclass(frozen=True, slots=True)
class GraphRelationshipSpec:
    source_name: str
    source_entity_type: str | None
    relationship_type: str
    target_name: str
    target_entity_type: str


# Only explicitly structured predicates are accepted. The deterministic
# transcript extractor also emits ``Rahul is my colleague`` as a generic
# relationship predicate; the role map below is the one reviewed exception.
GRAPH_RELATIONSHIP_POLICIES: dict[str, GraphRelationshipPolicy] = {
    "works_on": GraphRelationshipPolicy("WORKS_ON", "person", "project"),
    "responsible_for": GraphRelationshipPolicy("RESPONSIBLE_FOR", "person", None),
    "tested_by": GraphRelationshipPolicy("TESTED_BY", None, None),
    "manages": GraphRelationshipPolicy("MANAGES", "person", None),
    "reports_to": GraphRelationshipPolicy("REPORTS_TO", "person", "person"),
    "member_of": GraphRelationshipPolicy("MEMBER_OF", "person", None),
    "assigned_to": GraphRelationshipPolicy("ASSIGNED_TO", "person", None),
    "depends_on": GraphRelationshipPolicy("DEPENDS_ON", None, None),
    "blocked_by": GraphRelationshipPolicy("BLOCKED_BY", None, None),
    "discussed_with": GraphRelationshipPolicy("DISCUSSED_WITH", "person", "person"),
}

_SELF_RELATIONSHIP_ROLES: dict[str, tuple[str, str]] = {
    "colleague": (RelationshipType.COLLEAGUE_OF.value, "person"),
    "friend": (RelationshipType.FRIEND_OF.value, "person"),
    "family": (RelationshipType.FAMILY_OF.value, "person"),
    "family member": (RelationshipType.FAMILY_OF.value, "person"),
    "sister": (RelationshipType.FAMILY_OF.value, "person"),
    "brother": (RelationshipType.FAMILY_OF.value, "person"),
    "mother": (RelationshipType.FAMILY_OF.value, "person"),
    "father": (RelationshipType.FAMILY_OF.value, "person"),
    "parent": (RelationshipType.FAMILY_OF.value, "person"),
    "daughter": (RelationshipType.FAMILY_OF.value, "person"),
    "son": (RelationshipType.FAMILY_OF.value, "person"),
}

_ENTITY_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def derive_relationship_spec(
    *,
    memory_type: str,
    subject: str | None,
    predicate: str | None,
    object_json: dict[str, Any] | None,
) -> tuple[GraphRelationshipSpec | None, str | None]:
    """Derive an edge only from a typed, validated structured memory shape."""

    if memory_type != "relationship":
        return None, "unsupported_memory_type"
    if not isinstance(subject, str) or not subject.strip():
        return None, "missing_subject"
    if len(subject.strip()) > 512:
        return None, "missing_subject"
    if not isinstance(predicate, str) or not predicate.strip():
        return None, "unsupported_predicate"
    if not isinstance(object_json, dict):
        return None, "missing_target"

    name = object_json.get("name")
    if name is None:
        # A value-only target is accepted only alongside an explicit type; it
        # is never converted into an entity based on its text or capitalization.
        name = object_json.get("value")
    if not isinstance(name, str) or not name.strip():
        return None, "missing_target"
    if len(name.strip()) > 512:
        return None, "missing_target"

    if predicate.strip().casefold() == "relationship":
        role = normalize_memory_text(name).removeprefix("my ").strip()
        mapped = _SELF_RELATIONSHIP_ROLES.get(role)
        if mapped is None:
            return None, "unsupported_predicate"
        relationship_type, target_entity_type = mapped
        return (
            GraphRelationshipSpec(
                source_name="self",
                source_entity_type="self",
                relationship_type=relationship_type,
                target_name=subject.strip(),
                target_entity_type=target_entity_type,
            ),
            None,
        )

    policy = GRAPH_RELATIONSHIP_POLICIES.get(predicate.strip().casefold())
    if policy is None:
        return None, "unsupported_predicate"

    raw_type = object_json.get("type") or policy.target_entity_type
    if not isinstance(raw_type, str) or not raw_type.strip():
        return None, "invalid_entity_type"
    target_entity_type = raw_type.strip().casefold()
    if not _ENTITY_TYPE.fullmatch(target_entity_type):
        return None, "invalid_entity_type"
    if policy.target_entity_type is not None and target_entity_type != policy.target_entity_type:
        return None, "invalid_entity_type"

    return (
        GraphRelationshipSpec(
            source_name=subject.strip(),
            source_entity_type=policy.source_entity_type,
            relationship_type=policy.relationship_type,
            target_name=name.strip(),
            target_entity_type=target_entity_type,
        ),
        None,
    )
