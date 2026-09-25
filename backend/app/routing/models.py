from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class RouteName(StrEnum):
    CONTROL = "CONTROL"
    DIRECT_TOOL = "DIRECT_TOOL"
    STRUCTURED_READ = "STRUCTURED_READ"
    MEMORY_QUERY = "MEMORY_QUERY"
    MEMORY_ACTION = "MEMORY_ACTION"
    TASK_ACTION = "TASK_ACTION"
    GENERAL_LLM = "GENERAL_LLM"
    MIXED_AMBIGUOUS = "MIXED_AMBIGUOUS"


class ActionDomain(StrEnum):
    TASK = "task"
    REMINDER = "reminder"
    MEMORY_SAVE = "memory_save"
    MEMORY_FORGET = "memory_forget"


class DecisionSource(StrEnum):
    RULE = "rule"
    CLASSIFIER = "classifier"
    LEGACY = "legacy"


class TaskReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["pending", "in_progress", "completed", "cancelled"] | None = None
    limit: StrictInt = Field(default=20, ge=1, le=50)
    date_window: Literal["today", "tomorrow"] | None = None
    next_only: StrictBool = False
    active_only: StrictBool = False
    search_terms: list[StrictStr] = Field(default_factory=list, max_length=3)

    @field_validator("search_terms")
    @classmethod
    def validate_search_terms(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.strip() or len(value) > 48:
                raise ValueError("search terms must contain 1 to 48 characters")
            if not all(character.isalnum() or character in " '-" for character in value):
                raise ValueError("search terms contain unsupported characters")
        return values


class ReminderReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["scheduled", "sent", "failed", "cancelled"] | None = None
    upcoming: StrictBool = False
    limit: StrictInt = Field(default=20, ge=1, le=50)
    date_window: Literal["today", "tomorrow"] | None = None
    next_only: StrictBool = False
    search_terms: list[StrictStr] = Field(default_factory=list, max_length=3)

    @field_validator("search_terms")
    @classmethod
    def validate_search_terms(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.strip() or len(value) > 48:
                raise ValueError("search terms must contain 1 to 48 characters")
            if not all(character.isalnum() or character in " '-" for character in value):
                raise ValueError("search terms contain unsupported characters")
        return values


class RouteDecision(BaseModel):
    """Validated route metadata; it never contains executable write arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: RouteName
    decision_source: DecisionSource = DecisionSource.RULE
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    target_tool: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    action_domain: ActionDomain | None = None
    read_arguments: dict[str, object] | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def confidence_must_not_be_boolean(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("confidence must be numeric, not boolean")
        return value

    @model_validator(mode="after")
    def validate_route_fields(self) -> RouteDecision:
        direct_tools = {"get_current_time", "get_current_date"}
        structured_read_tools = {"list_tasks", "list_reminders"}

        if self.route == RouteName.DIRECT_TOOL:
            if (
                self.target_tool not in direct_tools
                or self.action_domain is not None
                or self.read_arguments is not None
            ):
                raise ValueError("DIRECT_TOOL requires an allowed read-only clock tool")
            return self

        if self.route == RouteName.STRUCTURED_READ:
            if self.target_tool not in structured_read_tools or self.action_domain is not None:
                raise ValueError("STRUCTURED_READ requires an allowed task/reminder read tool")
            args_model = (
                TaskReadArguments if self.target_tool == "list_tasks" else ReminderReadArguments
            )
            parsed = args_model.model_validate(self.read_arguments or {})
            object.__setattr__(
                self,
                "read_arguments",
                parsed.model_dump(exclude_defaults=True, exclude_none=True),
            )
            return self

        if self.route == RouteName.TASK_ACTION:
            if self.action_domain not in {ActionDomain.TASK, ActionDomain.REMINDER}:
                raise ValueError("TASK_ACTION requires task or reminder action_domain")
            if self.target_tool is not None or self.read_arguments is not None:
                raise ValueError("TASK_ACTION cannot select an executable tool")
            return self

        if self.route == RouteName.MEMORY_ACTION:
            if self.action_domain not in {ActionDomain.MEMORY_SAVE, ActionDomain.MEMORY_FORGET}:
                raise ValueError("MEMORY_ACTION requires memory_save or memory_forget domain")
            if self.target_tool is not None or self.read_arguments is not None:
                raise ValueError("MEMORY_ACTION cannot select an executable tool")
            return self

        if (
            self.target_tool is not None
            or self.action_domain is not None
            or self.read_arguments is not None
        ):
            raise ValueError(f"{self.route.value} does not accept a target tool or action domain")
        return self


class RouterOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: RouteName
    needs_clarification: bool = False


@dataclass(frozen=True)
class RouterRuntimeContext:
    """Ephemeral authenticated turn context; never stored in graph state."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    response_id: uuid.UUID
    cancellation_check: Callable[[], bool] = field(default=lambda: False, repr=False)


class RouterMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    CANARY = "canary"
    ON = "on"


class RouterRunStatus(StrEnum):
    DISABLED = "disabled"
    OUT_OF_COHORT = "out_of_cohort"
    DECIDED = "decided"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    CONFIRMATION_HANDLED = "confirmation_handled"
    SKIPPED_MEMORY_POLICY = "skipped_memory_policy"
    SKIPPED_CAPACITY = "skipped_capacity"


class RouterCancelledError(Exception):
    """Raised when a cancelled turn reaches a router node."""


class RouterRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: RouterRunStatus
    decision: RouteDecision | None = None
    outcome: RouterOutcome | None = None
    use_legacy_orchestrator: bool = True
