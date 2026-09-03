from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LLMRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LLMToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=128)
    arguments_json: str = Field(default="", max_length=2 * 1024 * 1024)
    arguments: dict[str, Any] | None = None


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: LLMRole
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[LLMToolCall, ...] = ()

    @model_validator(mode="after")
    def validate_tool_correlation(self) -> LLMMessage:
        if self.role == LLMRole.TOOL and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        if self.role != LLMRole.TOOL and self.tool_call_id is not None:
            raise ValueError("tool_call_id is valid only for tool messages")
        if self.role != LLMRole.ASSISTANT and self.tool_calls:
            raise ValueError("tool_calls are valid only for assistant messages")
        return self


class LLMToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(min_length=1, max_length=1024)
    input_schema: dict[str, Any]


class LLMNamedToolFunction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class LLMNamedToolChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["function"] = "function"
    function: LLMNamedToolFunction


LLMToolChoice = Literal["auto", "none", "required"] | LLMNamedToolChoice


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: uuid.UUID
    turn_id: uuid.UUID
    response_id: uuid.UUID
    system_instructions: str = Field(min_length=1, max_length=16_384)
    messages: tuple[LLMMessage, ...] = Field(min_length=1)
    allowed_tools: tuple[LLMToolDefinition, ...] = ()
    tool_choice: LLMToolChoice = "auto"
    max_output_tokens: int = Field(ge=1, le=16_384)

    @model_validator(mode="after")
    def validate_named_tool_choice(self) -> LLMRequest:
        if isinstance(self.tool_choice, LLMNamedToolChoice) and not any(
            tool.name == self.tool_choice.function.name for tool in self.allowed_tools
        ):
            raise ValueError("A named tool choice must reference an allowed registered tool.")
        return self


class LLMCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    streaming: bool = False
    text_generation: bool = False
    tool_calling: bool = False
    parallel_tool_calling: bool = False
    strict_tool_schema: bool = False
    structured_text_output: bool = False
    usage_reporting: bool = False
    model_listing: bool = False
    cancellation: bool = False


class LLMProviderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    api_family: str
    host: str
    configured_model: str
    capabilities: LLMCapabilities
    live_verified: bool = False


class LLMUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


LLMEventType = Literal[
    "request_started",
    "text_delta",
    "tool_call_started",
    "tool_call_arguments_delta",
    "tool_call_completed",
    "confirmation_required",
    "usage",
    "response_completed",
    "response_failed",
]


class LLMEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: LLMEventType
    session_id: uuid.UUID
    turn_id: uuid.UUID
    response_id: uuid.UUID
    provider: str
    configured_model: str
    monotonic_seconds: float = Field(ge=0)
    sequence: int = Field(ge=0)
    attempt: int = Field(default=1, ge=1)
    delta: str | None = None
    text: str | None = None
    tool_call: LLMToolCall | None = None
    usage: LLMUsage | None = None
    provider_request_id: str | None = None
    returned_model: str | None = None
    finish_reason: str | None = None
    error_code: str | None = None
    retryable: bool | None = None

    @model_validator(mode="after")
    def validate_event_payload(self) -> LLMEvent:
        if self.event_type == "text_delta" and self.delta is None:
            raise ValueError("text_delta events require delta")
        if self.event_type.startswith("tool_call_") and self.tool_call is None:
            raise ValueError("tool-call events require tool_call")
        if self.event_type == "usage" and self.usage is None:
            raise ValueError("usage events require usage")
        if self.event_type == "response_failed" and self.error_code is None:
            raise ValueError("response_failed events require error_code")
        return self
