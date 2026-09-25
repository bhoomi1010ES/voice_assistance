from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.llm.context import (
    build_voice_llm_request,
    classify_voice_tool_choice,
)
from app.llm.tool_loop import create_default_tool_registry
from app.llm.types import LLMMessage, LLMNamedToolChoice, LLMRequest, LLMRole
from app.memory.tool_tools import register_memory_tools


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        llm_provider="nvidia",
        llm_base_url="https://integrate.api.nvidia.com/v1",
        llm_api_key="test-placeholder-key",
        llm_model="nvidia/nemotron-3-super-120b-a12b",
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Remind me to call Rahul tomorrow at 9 AM.",
        "Remind me to drink water at 10 AM tomorrow.",
        "Create a task to submit my report Friday.",
        "Please remind me to call the doctor tomorrow morning.",
    ],
)
def test_explicit_mutating_voice_intent_selects_only_create_task(prompt: str) -> None:
    choice = classify_voice_tool_choice(prompt, create_default_tool_registry().definitions())

    assert isinstance(choice, LLMNamedToolChoice)
    assert choice.type == "function"
    assert choice.function.name == "create_task"


@pytest.mark.parametrize(
    "prompt",
    [
        "What is a reminder?",
        "Explain task scheduling.",
        "Hello.",
        "How do I create a task?",
        "Can you explain how reminders work?",
        "How can I delete a reminder?",
    ],
)
def test_informational_and_ordinary_voice_intent_remains_auto(prompt: str) -> None:
    choice = classify_voice_tool_choice(prompt, create_default_tool_registry().definitions())

    assert choice == "auto"


@pytest.mark.parametrize(
    "prompt",
    [
        "When should I call Path?",
        "What time is my call with Parth?",
        "What tasks do I have tomorrow?",
        "Did I create a reminder for the doctor?",
    ],
)
def test_saved_task_lookup_selects_read_only_list_tasks(prompt: str) -> None:
    choice = classify_voice_tool_choice(prompt, create_default_tool_registry().definitions())

    assert isinstance(choice, LLMNamedToolChoice)
    assert choice.function.name == "list_tasks"


def test_task_lookup_requires_registered_list_tasks_tool() -> None:
    assert classify_voice_tool_choice("When should I call Path?", ()) == "auto"


def test_routing_requires_registered_create_task_tool() -> None:
    assert (
        classify_voice_tool_choice(
            "Remind me to call Rahul tomorrow at 9 AM.",
            (),
        )
        == "auto"
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Remember that I prefer online morning meetings.",
        "Please remember I live in Mumbai.",
        "Save this in my memory: I prefer jasmine tea.",
    ],
)
def test_explicit_memory_intent_selects_registered_memory_save(prompt: str) -> None:
    registry = create_default_tool_registry()
    register_memory_tools(registry, allow_write=True)

    choice = classify_voice_tool_choice(prompt, registry.definitions())

    assert isinstance(choice, LLMNamedToolChoice)
    assert choice.function.name == "memory_save"


def test_memory_routing_requires_registered_write_tool() -> None:
    registry = create_default_tool_registry()
    register_memory_tools(registry, allow_write=False)

    assert (
        classify_voice_tool_choice(
            "Remember that I prefer online morning meetings.",
            registry.definitions(),
        )
        == "auto"
    )


def test_named_tool_choice_cannot_reference_an_unregistered_tool() -> None:
    with pytest.raises(ValidationError, match="allowed registered tool"):
        LLMRequest(
            session_id=uuid.uuid4(),
            turn_id=uuid.uuid4(),
            response_id=uuid.uuid4(),
            system_instructions="Answer briefly.",
            messages=(LLMMessage(role=LLMRole.USER, content="Hello"),),
            tool_choice=LLMNamedToolChoice(function={"name": "admin_delete_user"}),
            max_output_tokens=32,
        )


def test_voice_request_contains_server_owned_routing_and_confirmation_policy() -> None:
    request = build_voice_llm_request(
        _settings(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        transcript="Remind me to call Rahul tomorrow at 9 AM.",
        allowed_tools=create_default_tool_registry().definitions(),
    )

    assert isinstance(request.tool_choice, LLMNamedToolChoice)
    assert "MUST first call the registered" in request.system_instructions
    assert "create_task tool" in request.system_instructions
    assert "memory_save tool" in request.system_instructions
    assert "server owns confirmation and execution" in request.system_instructions


def test_voice_request_places_bounded_session_history_before_current_transcript() -> None:
    history = (
        LLMMessage(role=LLMRole.USER, content="I live in Mumbai."),
        LLMMessage(role=LLMRole.ASSISTANT, content="Thanks, I’ll remember that."),
    )
    request = build_voice_llm_request(
        _settings(),
        session_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        response_id=uuid.uuid4(),
        transcript="What city did I say I live in?",
        conversation_history=history,
    )

    assert request.messages[-3:] == (
        *history,
        LLMMessage(role=LLMRole.USER, content="What city did I say I live in?"),
    )
