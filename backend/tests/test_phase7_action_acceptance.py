from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.llm.context import classify_voice_tool_choice
from app.llm.tool_loop import create_default_tool_registry
from app.memory.forget_resolution import forget_description
from app.memory.tool_tools import build_explicit_memory_save_call, register_memory_tools
from app.routing.models import RouteName
from app.routing.rules import classify_transcript

CORPUS_PATH = Path(__file__).parent / "fixtures" / "phase7_action_acceptance_v1.json"


def test_phase7_action_acceptance_corpus() -> None:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert corpus["corpus_id"] == "phase7-action-acceptance-v1"
    assert corpus["version"] == "1.0.0"
    assert corpus["reminder_semantics"]["notification_delivery_claimed"] is False
    assert "create_task" in corpus["reminder_semantics"]["decision"]
    registry = create_default_tool_registry()
    register_memory_tools(registry, allow_write=True)

    for case in corpus["cases"]:
        decision = classify_transcript(case["utterance"])
        assert decision.route == RouteName(case["expected_route"]), case["utterance"]
        if "expected_tool" in case:
            choice = classify_voice_tool_choice(case["utterance"], registry.definitions())
            assert choice.function.name == case["expected_tool"]
        if "proposal_tool" in case:
            tool = registry.get(case["proposal_tool"])
            assert tool is not None and tool.requires_confirmation and not tool.read_only
            if case["proposal_tool"] == "memory_save":
                assert build_explicit_memory_save_call(case["utterance"], turn_id=uuid.uuid4())
        assert case["rag_calls"] == 0


def test_forget_description_removes_only_action_framing() -> None:
    assert forget_description("Forget that I prefer cappuccino.") == "cappuccino"
    assert forget_description("Delete my saved memory about Phoenix Mall.") == "Phoenix Mall"
    assert forget_description("Forget my preference for a purple helicopter.") == (
        "preference for a purple helicopter"
    )


def test_forget_resolver_is_owner_scoped_active_only_and_requires_unique_match() -> None:
    from app.memory.forget_resolution import resolve_forget_target

    owner = uuid.uuid4()
    expected = uuid.uuid4()
    foreign = uuid.uuid4()

    class Rows:
        def __init__(self, values):
            self.values = values

        def __iter__(self):
            return iter(self.values)

    class Session:
        def __init__(self, values):
            self.values = values

        async def scalars(self, _query):
            return Rows(self.values)

    def row(memory_id, user_id, content, status="active"):
        return type(
            "MemoryRow",
            (),
            {"id": memory_id, "user_id": user_id, "content": content, "status": status},
        )()

    import asyncio

    unique = asyncio.run(
        resolve_forget_target(
            Session(
                [
                    row(expected, owner, "I prefer cappuccino"),
                    row(foreign, uuid.uuid4(), "I prefer cappuccino"),
                ]
            ),
            user_id=owner,
            transcript="Forget that I prefer cappuccino.",
        )
    )
    assert unique.status == "unique"
    assert unique.memory_ids == (expected,)

    ambiguous = asyncio.run(
        resolve_forget_target(
            Session(
                [
                    row(expected, owner, "I prefer cappuccino"),
                    row(uuid.uuid4(), owner, "I prefer cappuccino"),
                ]
            ),
            user_id=owner,
            transcript="Forget that I prefer cappuccino.",
        )
    )
    assert ambiguous.status == "ambiguous"
    assert len(ambiguous.memory_ids) == 2

    no_match = asyncio.run(
        resolve_forget_target(
            Session([row(expected, owner, "I prefer cappuccino", status="deleted")]),
            user_id=owner,
            transcript="Forget that I prefer cappuccino.",
        )
    )
    assert no_match.status == "no_match"
