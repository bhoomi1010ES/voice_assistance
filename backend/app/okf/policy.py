from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from app.memory.policy import validate_safe_json
from app.models import MemoryItem

from .types import OkfConceptProposal

OKF_POLICY_VERSION = "okf-v1"
_KEY_PATTERN = re.compile(
    r"^(?:profile|preferences|projects|relationships|facts)/"
    r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*){0,6}$"
)
_SECRET = re.compile(
    r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret|credential)\b",
    re.IGNORECASE,
)

_PREFERENCE_PREDICATES = {"beverage", "commute", "editor", "language", "response_style", "tool"}
_PROJECT_PREDICATES = {
    "database",
    "embedding_model",
    "framework",
    "language",
    "repository",
    "status",
    "vector_store",
}
_DECISION_PREDICATES = {
    "database",
    "embedding_model",
    "framework",
    "selected_model",
    "vector_store",
}
_RELATIONSHIP_PREDICATES = {
    "colleague",
    "depends_on",
    "manager",
    "member_of",
    "relationship",
    "reports_to",
}
_PROFILE_PREDICATES = {"home_base", "home_location", "name", "occupation", "timezone"}


def normalize_segment(value: str) -> str:
    """Normalize one canonical-key segment using the frozen OKF-0 rules."""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = " ".join(normalized.split())
    if not normalized:
        raise ValueError("okf_canonical_key_empty_segment")
    if any(ord(character) > 127 and character.isalnum() for character in normalized):
        raise ValueError("okf_canonical_key_unmapped_segment")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not slug:
        raise ValueError("okf_canonical_key_unmapped_segment")
    return slug


def validate_canonical_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if len(normalized) > 512 or not _KEY_PATTERN.fullmatch(normalized):
        raise ValueError("okf_canonical_key_invalid")
    return normalized


def validate_value(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError("okf_value_must_be_nonempty_object")
    validate_safe_json(value)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > 16_384:
        raise ValueError("okf_value_too_large")
    _validate_depth(value)
    return value


def _validate_depth(value: Any, *, depth: int = 0) -> None:
    if depth > 4:
        raise ValueError("okf_value_too_deep")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("okf_value_invalid_key")
            if _SECRET.search(key):
                raise ValueError("okf_value_sensitive")
            _validate_depth(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_depth(item, depth=depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("okf_value_invalid_type")
    elif isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
        raise ValueError("okf_value_invalid_number")
    elif isinstance(value, str) and _SECRET.search(value):
        raise ValueError("okf_value_sensitive")


def map_memory_to_proposal(
    memory: MemoryItem, *, policy_version: str = OKF_POLICY_VERSION
) -> OkfConceptProposal | None:
    """Map only frozen structured source fields; never infer from free text."""

    if memory.status != "active" or memory.memory_type not in {
        "fact",
        "preference",
        "project",
        "relationship",
    }:
        return None
    if not isinstance(memory.subject, str) or not memory.subject.strip():
        return None
    if not isinstance(memory.predicate, str) or not memory.predicate.strip():
        return None
    if _SECRET.search(memory.subject) or _SECRET.search(memory.predicate):
        return None
    if not isinstance(memory.object_json, dict):
        return None
    if _SECRET.search(memory.content or ""):
        return None
    try:
        value = validate_value(memory.object_json)
        predicate = normalize_segment(memory.predicate)
        subject = normalize_segment(memory.subject)
    except ValueError:
        return None

    predicate_name = predicate.replace("-", "_")
    if memory.memory_type == "preference" and predicate_name in _PREFERENCE_PREDICATES:
        concept_type = "preference"
        key = f"preferences/{predicate}"
        title = memory.predicate.replace("_", " ").title()
    elif memory.memory_type == "project" and predicate_name in _PROJECT_PREDICATES:
        concept_type = "project"
        key = f"projects/{subject}/{predicate}"
        title = f"{memory.subject.strip()} {memory.predicate.replace('_', ' ')}"
    elif memory.memory_type == "relationship" and predicate_name in _RELATIONSHIP_PREDICATES:
        concept_type = "relationship"
        target = value.get("target") or value.get("name")
        if not isinstance(target, str) or not target.strip():
            return None
        try:
            target_segment = normalize_segment(target)
        except ValueError:
            return None
        key = f"relationships/{subject}/{target_segment}"
        title = f"{memory.subject.strip()} {memory.predicate.replace('_', ' ')}"
    elif memory.memory_type == "fact" and predicate_name in _PROFILE_PREDICATES:
        concept_type = "profile"
        key = f"profile/{predicate}"
        title = memory.predicate.replace("_", " ").title()
    elif (
        memory.memory_type == "fact"
        and predicate_name in _DECISION_PREDICATES
        and value.get("decision") is True
    ):
        concept_type = "decision"
        key = f"projects/{subject}/{predicate}"
        title = f"{memory.subject.strip()} {memory.predicate.replace('_', ' ')} decision"
    elif memory.memory_type == "fact":
        concept_type = "fact"
        key = f"facts/{subject}/{predicate}"
        title = f"{memory.subject.strip()} {memory.predicate.replace('_', ' ')}"
    else:
        return None

    key = validate_canonical_key(key)
    display_text = _render_value(value)
    return OkfConceptProposal(
        concept_type=concept_type,
        canonical_key=key,
        title=title[:512],
        value_json=value,
        display_text=display_text[:2_000],
        confidence=float(memory.confidence),
        valid_from=memory.valid_from,
        valid_to=memory.valid_to,
        source_memory_id=memory.id,
        policy_version=policy_version,
    )


def map_memory_to_proposals(
    memory: MemoryItem, *, policy_version: str = OKF_POLICY_VERSION
) -> tuple[OkfConceptProposal, ...]:
    """Expand a structured project property into identity and child concepts."""

    proposal = map_memory_to_proposal(memory, policy_version=policy_version)
    if proposal is None or memory.memory_type != "project":
        return (proposal,) if proposal is not None else ()
    subject = normalize_segment(memory.subject or "")
    root = OkfConceptProposal(
        concept_type="project",
        canonical_key=f"projects/{subject}",
        title=(memory.subject or "").strip()[:512],
        value_json={"name": (memory.subject or "").strip()},
        display_text=(memory.subject or "").strip()[:2_000],
        confidence=float(memory.confidence),
        valid_from=memory.valid_from,
        valid_to=memory.valid_to,
        source_memory_id=memory.id,
        policy_version=policy_version,
    )
    return root, proposal


def validate_proposal(proposal: OkfConceptProposal) -> OkfConceptProposal:
    validate_canonical_key(proposal.canonical_key)
    validate_value(proposal.value_json)
    if not proposal.title.strip() or not proposal.display_text.strip():
        raise ValueError("okf_proposal_display_invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", proposal.policy_version):
        raise ValueError("okf_policy_version_invalid")
    if proposal.valid_to and proposal.valid_from and proposal.valid_to < proposal.valid_from:
        raise ValueError("okf_validity_range_invalid")
    return proposal


def proposals_match(left: OkfConceptProposal, right: OkfConceptProposal) -> bool:
    return (
        left.concept_type == right.concept_type
        and left.canonical_key == right.canonical_key
        and left.title == right.title
        and left.value_json == right.value_json
        and left.display_text == right.display_text
        and left.confidence == right.confidence
        and left.valid_from == right.valid_from
        and left.valid_to == right.valid_to
        and left.source_memory_id == right.source_memory_id
        and left.policy_version == right.policy_version
    )


def _render_value(value: dict[str, Any]) -> str:
    if len(value) == 1:
        item = next(iter(value.values()))
        if isinstance(item, (str, int, float, bool)):
            return str(item)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
