from __future__ import annotations

import re

from .policy import ExtractionCandidate, validate_candidate
from .types import MemoryType

_EXPLICIT = re.compile(r"^(?:please\s+)?remember(?:\s+that)?\s+(.+)$", re.IGNORECASE)
_EXPLICIT_SAVE = re.compile(
    r"^(?:please\s+)?(?:save|store)\s+(?:this|that)\s+"
    r"(?:in|to)\s+(?:my\s+)?memory\s*[:,-]?\s*(.+)$",
    re.IGNORECASE,
)
_PREFERENCE = re.compile(
    r"^(?:actually\s*,?\s*)?(?:i\s+)?(?:prefer|like|love|hate|dislike)\s+(.+)$",
    re.IGNORECASE,
)
_PREFERENCE_NOMINAL = re.compile(r"^my\s+preference\s+is\s+(.+)$", re.IGNORECASE)
_RELATIONSHIP = re.compile(r"^(.+?)\s+is\s+my\s+(.+)$", re.IGNORECASE)
_DURABLE_FACT = re.compile(
    r"^(?:i\s+work\s+(?:at|for|remotely)|my\s+(?:home\s+base|timezone|laptop)\s+is)\b",
    re.IGNORECASE,
)
_PREFERRED_ATTRIBUTE = re.compile(r"^my\s+preferred\s+(.+?)\s+is\s+(.+)$", re.IGNORECASE)
_ROUTINE = re.compile(
    r"^(?:i\s+usually\s+.+|every\s+\w+\s+i\s+.+)$",
    re.IGNORECASE,
)
_REJECTED_PREFIX = re.compile(
    r"^(?:don['’]t|do\s+not|never)\s+remember\b|^forget\b|^please\s+forget\b"
    r"|^(?:create|make|add|set|schedule|remind)\b|^save\s+this\s+task\b"
    r"|^remember\s+to\b",
    re.IGNORECASE,
)
_UNCERTAIN_PREFIX = re.compile(r"^(?:maybe|might|perhaps|if|what\s+would|could)\b", re.IGNORECASE)
_THIRD_PARTY = re.compile(r"\b(?:said|says|told\s+me)\b", re.IGNORECASE)
_TEMPORARY_MARKER = re.compile(
    r"\b(?:today|right\s+now|at\s+the\s+moment|currently)\b", re.IGNORECASE
)


def extract_explicit_candidates(text: str) -> tuple[ExtractionCandidate, ...]:
    """Extract only explicit, source-grounded memory requests.

    Automatic inference from ordinary conversation is intentionally deferred to
    a separately versioned provider policy. This parser is deterministic and
    therefore safe for the initial async job path.
    """

    normalized = " ".join(text.split())
    if (
        not normalized
        or _REJECTED_PREFIX.search(normalized)
        or _UNCERTAIN_PREFIX.search(normalized)
        or _THIRD_PARTY.search(normalized)
        or _TEMPORARY_MARKER.search(normalized)
    ):
        return ()
    explicit_match = _EXPLICIT.match(normalized) or _EXPLICIT_SAVE.match(normalized)
    explicitly_requested = explicit_match is not None
    plain_preference = _PREFERENCE.match(normalized)
    nominal_preference = _PREFERENCE_NOMINAL.match(normalized)
    relationship = _RELATIONSHIP.match(normalized)
    durable_fact = _DURABLE_FACT.match(normalized)
    preferred_attribute = _PREFERRED_ATTRIBUTE.match(normalized)
    routine = _ROUTINE.match(normalized)
    if not any(
        (
            explicit_match,
            plain_preference,
            nominal_preference,
            relationship,
            durable_fact,
            preferred_attribute,
            routine,
        )
    ):
        return ()

    if explicitly_requested:
        content = explicit_match.group(1).strip().rstrip(".")
    elif plain_preference:
        content = re.sub(r"^actually\s*,?\s*", "", normalized, flags=re.IGNORECASE)
        content = content.rstrip(".")
    else:
        content = normalized.rstrip(".")
    candidate = _candidate_from_content(
        content,
        prefer_plain_statement=not explicitly_requested
        and (
            plain_preference is not None
            or nominal_preference is not None
            or preferred_attribute is not None
        ),
    )
    try:
        validated = validate_candidate(candidate, normalized)
    except ValueError:
        # Policy rejection is a successful no-op, not a retryable worker
        # failure. The source message remains durable and auditable.
        return ()
    return (validated,)


def extract_explicit_tool_candidate(text: str) -> ExtractionCandidate | None:
    """Build a grounded candidate only for an explicit remember/save command."""

    normalized = " ".join(text.split())
    explicit_match = _EXPLICIT.match(normalized) or _EXPLICIT_SAVE.match(normalized)
    if explicit_match is None:
        return None
    candidate = _candidate_from_content(explicit_match.group(1).strip().rstrip("."))
    try:
        return validate_candidate(candidate, normalized)
    except ValueError:
        return None


def _candidate_from_content(
    content: str,
    *,
    prefer_plain_statement: bool = False,
) -> ExtractionCandidate:
    relationship = _RELATIONSHIP.match(content)
    preference = _PREFERENCE.match(content)
    nominal_preference = _PREFERENCE_NOMINAL.match(content)
    preferred_attribute = _PREFERRED_ATTRIBUTE.match(content)
    memory_type = (
        MemoryType.PREFERENCE
        if preference or nominal_preference or preferred_attribute or prefer_plain_statement
        else MemoryType.RELATIONSHIP
        if relationship
        else MemoryType.ROUTINE
        if _ROUTINE.match(content)
        else MemoryType.FACT
    )
    candidate = ExtractionCandidate(
        content=content,
        memory_type=memory_type,
        subject=(
            "user"
            if memory_type == MemoryType.PREFERENCE
            else relationship.group(1).strip()
            if relationship
            else None
        ),
        predicate=(
            "preference"
            if memory_type == MemoryType.PREFERENCE
            else "relationship"
            if relationship
            else None
        ),
        object_json={"value": relationship.group(2).strip()} if relationship else None,
        confidence=1.0,
        salience=0.8,
    )
    return candidate
