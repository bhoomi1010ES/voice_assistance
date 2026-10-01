from __future__ import annotations

import re
import unicodedata

from .types import OkfQueryPlan

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "am",
        "an",
        "are",
        "as",
        "at",
        "did",
        "do",
        "does",
        "for",
        "from",
        "have",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "say",
        "said",
        "saved",
        "the",
        "tell",
        "told",
        "to",
        "use",
        "used",
        "uses",
        "using",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "with",
        "you",
        "remember",
        "remembered",
    }
)
_TYPE_TERMS = frozenset(
    {
        "decision",
        "decisions",
        "fact",
        "facts",
        "preference",
        "preferences",
        "prefer",
        "project",
        "projects",
        "relationship",
        "relationships",
        "profile",
        "profiles",
        "remember",
    }
)
_RELATION_KINDS = frozenset(
    {"colleague", "colleagues", "friend", "friends", "family", "manager", "partner"}
)
_ALIASES = {
    "db": "database",
    "datastore": "database",
    "drink": "beverage",
    "frameworks": "framework",
    "favourite": "favorite",
    "favorites": "favorite",
    "languages": "language",
    "tools": "tool",
}
_PREFERENCE_FIELD_TERMS = (
    frozenset({"beverage", "drink"}),
    frozenset({"response", "style", "tone"}),
    frozenset({"editor", "ide"}),
    frozenset({"language"}),
    frozenset({"commute", "commuting"}),
    frozenset({"tool", "tools"}),
)
_LEADING_ACKNOWLEDGEMENT = re.compile(r"^(?:(?:thank\s+you|thanks)[.!?,;:]+\s*)+", re.I)
_TRAILING_ACKNOWLEDGEMENT = re.compile(r"([.!?])\s*(?:thank\s+you|thanks)[.!?,;:]*$", re.I)
_HOME_LOCATION_QUERY = re.compile(
    r"\bwhere\s+do\s+i\s+(?:live|usually\s+stay)\b|"
    r"\bwhere\s+is\s+my\s+home(?:\s+location)?\b",
    re.I,
)


def plan_okf_query(query: str, *, limit: int = 20) -> OkfQueryPlan:
    """Plan only exact canonical-key lookups; never infer keys from free text."""

    normalized = " ".join(unicodedata.normalize("NFKC", query).split())
    if not normalized:
        raise ValueError("okf_query_blank")
    if len(normalized) > 2_000:
        raise ValueError("okf_query_too_long")
    # Voice STT can retain a clearly separated acknowledgement around the
    # actual question. Strip only these boundary clauses; words inside the
    # request continue to constrain canonical-key matching.
    without_leading_ack = _LEADING_ACKNOWLEDGEMENT.sub("", normalized).strip()
    without_boundary_acks = _TRAILING_ACKNOWLEDGEMENT.sub(r"\1", without_leading_ack).strip()
    if without_boundary_acks:
        normalized = without_boundary_acks
    if not 1 <= limit <= 100:
        raise ValueError("okf_query_limit_invalid")

    token_source = re.sub(r"\bdata[\s-]+store\b", "database", normalized.casefold())
    tokens = [
        _ALIASES.get(token, token)
        for token in _TOKEN.findall(token_source)
        if token not in _STOP_WORDS
    ]
    lowered = normalized.casefold()
    home_location_lookup = "project" not in tokens and (
        ("home" in tokens and "location" in tokens) or bool(_HOME_LOCATION_QUERY.search(lowered))
    )
    preference = any(term in tokens for term in ("prefer", "preference", "favorite"))
    relationship = any(term in tokens for term in ("relationship", "relationships")) or any(
        term in tokens for term in _RELATION_KINDS
    )
    project = "project" in tokens or "projects" in tokens
    profile = (
        any(term in tokens for term in ("profile", "name", "home", "occupation"))
        or home_location_lookup
    )

    if preference:
        intent = "preference"
        concept_types = ("preference",)
    elif relationship:
        intent = "relationship"
        concept_types = ("relationship",)
    elif project:
        intent = "project"
        concept_types = ("project", "decision")
    elif profile:
        intent = "profile"
        concept_types = ("profile",)
    else:
        intent = "general"
        concept_types = ("decision", "fact")

    key_terms: list[str] = []
    token_set = set(tokens)
    explicit_preference_fields = tuple(
        field & token_set for field in _PREFERENCE_FIELD_TERMS if field & token_set
    )
    preference_key_terms = (
        set().union(*explicit_preference_fields) if explicit_preference_fields else set()
    )
    for token in tokens:
        if home_location_lookup and token in {"home", "location", "live", "usually", "stay"}:
            continue
        if preference and preference_key_terms and token not in preference_key_terms:
            continue
        if token in _TYPE_TERMS or (relationship and token in _RELATION_KINDS):
            continue
        if token not in key_terms:
            key_terms.append(token)
    if home_location_lookup:
        key_terms.append("home")
    if not key_terms and intent == "general":
        # Unanchored broad requests must not return arbitrary personal facts.
        key_terms = ["noexactkeyokfmarker"]

    return OkfQueryPlan(
        normalized_query=normalized,
        intent=intent,
        key_terms=tuple(key_terms[:8]),
        concept_types=concept_types,
        project_expansion=project,
        limit=limit,
    )
