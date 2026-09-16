from app.llm.context import classify_voice_tool_choice
from app.llm.tool_loop import create_default_tool_registry
from app.llm.types import LLMNamedToolChoice
from app.llm.wait_status import classify_wait_status
from app.memory.tool_tools import register_memory_tools


def test_wait_status_uses_memory_recall_only_when_retrieval_will_run() -> None:
    registry = create_default_tool_registry()
    choice = classify_voice_tool_choice(
        "When did I last visit Mumbai?",
        registry.definitions(),
    )

    assert choice == "auto"
    assert classify_wait_status(
        "When did I last visit Mumbai?",
        memory_retrieval_will_run=True,
        tool_choice=choice,
    ).category == "memory_recall"
    assert classify_wait_status(
        "When did I last visit Mumbai?",
        memory_retrieval_will_run=False,
        tool_choice=choice,
    ).category == "default"


def test_wait_status_prefers_named_action_and_time_tools() -> None:
    registry = create_default_tool_registry()
    register_memory_tools(registry, allow_write=True)

    assert classify_wait_status(
        "Remember that I prefer jasmine tea.",
        memory_retrieval_will_run=True,
        tool_choice=LLMNamedToolChoice(function={"name": "memory_save"}),
    ).category == "memory_save"
    assert classify_wait_status(
        "Remind me to call Rahul tomorrow.",
        memory_retrieval_will_run=False,
        tool_choice=LLMNamedToolChoice(function={"name": "create_task"}),
    ).category == "task"
    assert classify_wait_status(
        "What time is it?",
        memory_retrieval_will_run=False,
        tool_choice=LLMNamedToolChoice(function={"name": "get_current_time"}),
    ).category == "time_date"


def test_wait_status_variants_rotate_without_extra_model_work() -> None:
    first = classify_wait_status(
        "Hello assistant",
        memory_retrieval_will_run=False,
        variant_index=0,
    )
    second = classify_wait_status(
        "Hello assistant",
        memory_retrieval_will_run=False,
        variant_index=1,
    )

    assert first.category == second.category == "default"
    assert first.phrase != second.phrase
