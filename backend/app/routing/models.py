from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class RouteDecision(BaseModel):
    """Validated route metadata; it never contains executable write arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: RouteName
    decision_source: DecisionSource = DecisionSource.RULE
    target_tool: str | None = Field(default=None, min_length=1, max_length=64)
    action_domain: ActionDomain | None = None

    @model_validator(mode="after")
    def validate_route_fields(self) -> RouteDecision:
        direct_tools = {"get_current_time", "get_current_date"}
        structured_read_tools = {"list_tasks", "list_reminders"}

        if self.route == RouteName.DIRECT_TOOL:
            if self.target_tool not in direct_tools or self.action_domain is not None:
                raise ValueError("DIRECT_TOOL requires an allowed read-only clock tool")
            return self

        if self.route == RouteName.STRUCTURED_READ:
            if self.target_tool not in structured_read_tools or self.action_domain is not None:
                raise ValueError("STRUCTURED_READ requires an allowed task/reminder read tool")
            return self

        if self.route == RouteName.TASK_ACTION:
            if self.action_domain not in {ActionDomain.TASK, ActionDomain.REMINDER}:
                raise ValueError("TASK_ACTION requires task or reminder action_domain")
            if self.target_tool is not None:
                raise ValueError("TASK_ACTION cannot select an executable tool")
            return self

        if self.route == RouteName.MEMORY_ACTION:
            if self.action_domain not in {ActionDomain.MEMORY_SAVE, ActionDomain.MEMORY_FORGET}:
                raise ValueError("MEMORY_ACTION requires memory_save or memory_forget domain")
            if self.target_tool is not None:
                raise ValueError("MEMORY_ACTION cannot select an executable tool")
            return self

        if self.target_tool is not None or self.action_domain is not None:
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


class RouterCancelledError(Exception):
    """Raised when a cancelled turn reaches a router node."""


class RouterRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: RouterRunStatus
    decision: RouteDecision | None = None
    outcome: RouterOutcome | None = None
    use_legacy_orchestrator: bool = True
