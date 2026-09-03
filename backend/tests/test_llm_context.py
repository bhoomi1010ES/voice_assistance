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
    ],
)
def test_informational_and_ordinary_voice_intent_remains_auto(prompt: str) -> None:
    choice = classify_voice_tool_choice(prompt, create_default_tool_registry().definitions())

    assert choice == "auto"


def test_routing_requires_registered_create_task_tool() -> None:
    assert classify_voice_tool_choice(
        "Remind me to call Rahul tomorrow at 9 AM.",
        (),
    ) == "auto"


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
    assert "server owns confirmation and execution" in request.system_instructions
