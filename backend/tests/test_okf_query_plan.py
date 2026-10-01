from __future__ import annotations

import pytest

from app.okf.query_plan import plan_okf_query


@pytest.mark.parametrize(
    ("query", "intent", "terms", "concept_types"),
    [
        (
            "Which response style do I prefer?",
            "preference",
            ("response", "style"),
            ("preference",),
        ),
        (
            "Which framework does my Voice Assistant project use?",
            "project",
            ("framework", "voice", "assistant"),
            ("project", "decision"),
        ),
        (
            "What is my relationship with Rahul?",
            "relationship",
            ("rahul",),
            ("relationship",),
        ),
        ("What is my home base?", "profile", ("home", "base"), ("profile",)),
    ],
)
def test_okf_query_planning_is_exact_and_deterministic(
    query: str, intent: str, terms: tuple[str, ...], concept_types: tuple[str, ...]
) -> None:
    plan = plan_okf_query(query)

    assert plan.intent == intent
    assert plan.key_terms == terms
    assert plan.concept_types == concept_types
    assert plan.normalized_query == query


def test_unanchored_general_query_cannot_select_arbitrary_facts() -> None:
    plan = plan_okf_query("What do you remember?")

    assert plan.intent == "general"
    assert plan.key_terms == ("noexactkeyokfmarker",)


def test_p01_boundary_acknowledgements_do_not_block_exact_home_key_match() -> None:
    padded = plan_okf_query("Thank you. What is my home location? Thank you.")
    exact = plan_okf_query("What is my home location?")

    assert padded.normalized_query == exact.normalized_query
    assert padded.intent == "profile"
    assert padded.key_terms == exact.key_terms == ("home",)


@pytest.mark.parametrize("query", ["Where do I live?", "Where is my home?"])
def test_home_location_paraphrases_resolve_to_the_profile_home_key(query: str) -> None:
    plan = plan_okf_query(query)

    assert plan.intent == "profile"
    assert plan.key_terms == ("home",)


def test_project_location_does_not_use_the_personal_home_alias() -> None:
    plan = plan_okf_query("Where is my Willow Beacon project location?")

    assert plan.intent == "project"
    assert "home" not in plan.key_terms
    assert "location" in plan.key_terms


def test_acknowledgement_words_inside_a_query_remain_search_constraints() -> None:
    plan = plan_okf_query("What is the Thank You Project framework?")

    assert plan.normalized_query == "What is the Thank You Project framework?"
    assert "thank" in plan.key_terms


def test_recall_verb_is_not_treated_as_a_canonical_key_term() -> None:
    plan = plan_okf_query("What did I tell you about my Blue Lantern archive serial code?")

    assert plan.key_terms == ("blue", "lantern", "archive", "serial", "code")


@pytest.mark.parametrize(
    ("query", "terms"),
    [
        (
            "Which beverage do I prefer for my Juniper test profile?",
            ("beverage",),
        ),
        (
            "Which drink do I prefer for my Juniper test profile?",
            ("beverage",),
        ),
        (
            "What have I told you about my evening reading routine?",
            ("evening", "reading", "routine"),
        ),
        (
            "What data store did I tell you my Willow Beacon project uses?",
            ("database", "willow", "beacon"),
        ),
    ],
)
def test_query_aliases_remove_only_noncanonical_recall_and_field_context(
    query: str, terms: tuple[str, ...]
) -> None:
    plan = plan_okf_query(query)

    assert plan.key_terms == terms


def test_general_recommendation_does_not_match_a_named_stored_project() -> None:
    plan = plan_okf_query("What database should I use for a new project?")

    assert plan.intent == "project"
    assert "willow" not in plan.key_terms
    assert "new" in plan.key_terms


@pytest.mark.parametrize(
    ("query", "limit", "message"),
    [
        (" ", 20, "okf_query_blank"),
        ("x" * 2_001, 20, "okf_query_too_long"),
        ("test", 0, "okf_query_limit_invalid"),
        ("test", 101, "okf_query_limit_invalid"),
    ],
)
def test_query_plan_rejects_unbounded_or_invalid_inputs(
    query: str, limit: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        plan_okf_query(query, limit=limit)
