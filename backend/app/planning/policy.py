"""Frozen PM-0 rollout policy; these decisions are never execution grants.

Callers must supply an authenticated, freshly read server consent snapshot.
PM-3 must still enforce ownership, evidence, revisions, action-bound grants and
the execution barrier before committing. No PM-0 code invokes mutation tools.
"""

from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from app.core.config import Settings

# Proposed organizational fields only. IDs/provenance and grouping are supplied
# by the backend, never accepted as model-controlled authorization fields.
AUTOMATIC_OPERATION_FIELDS = MappingProxyType(
    {
        "CREATE_PLAN": frozenset({"name", "goal", "deadline", "timezone"}),
        "UPDATE_PLAN": frozenset({"name", "goal", "deadline", "timezone"}),
        "ADD_PLAN_CONTEXT": frozenset({"kind", "content", "value"}),
        "CREATE_TASK": frozenset({"title", "description", "priority", "due", "timezone"}),
        "UPDATE_TASK": frozenset({"title", "description", "priority", "due", "timezone"}),
        "COMPLETE_TASK": frozenset({"status"}),
        "CREATE_REMINDER": frozenset({"title", "trigger", "timezone", "recurrence"}),
        "UPDATE_REMINDER": frozenset({"title", "trigger", "timezone", "recurrence"}),
    }
)
CONFIRMATION_OPERATIONS = frozenset(
    {"DELETE_PLAN", "ARCHIVE_PLAN", "DELETE_TASK", "DELETE_REMINDER", "CANCEL", "UNDO", "REASSIGN"}
)


@dataclass(frozen=True)
class PlanningConsent:
    """Server-owned session snapshot, including current identity/version checks."""

    user_id: UUID
    session_id: UUID
    authenticated: bool = False
    session_active: bool = False
    mode: str = "normal"
    state_version: int = 0
    expected_state_version: int = 0
    policy_version: str = "plan-v1"
    memory_excluded: bool = False


@dataclass(frozen=True)
class PlanningCapabilities:
    extract: bool = False
    persist_proposals: bool = False
    automatic_writes: bool = False
    reason: str = "disabled"


def planning_capabilities(settings: Settings, consent: PlanningConsent) -> PlanningCapabilities:
    """Decide eligibility, with no reads, writes, response effects or grants."""
    reason = None
    if not settings.plan_mode_enabled or settings.plan_extraction_mode == "off":
        reason = "disabled"
    elif not consent.authenticated or not consent.session_active:
        reason = "inactive_authority"
    elif consent.user_id not in settings.plan_test_user_ids:
        reason = "owner_not_allowlisted"
    elif consent.memory_excluded:
        reason = "private_session"
    elif consent.mode != "plan":
        reason = "no_consent"
    elif consent.state_version < 1 or consent.state_version != consent.expected_state_version:
        reason = "stale_consent"
    elif consent.policy_version != settings.plan_policy_version:
        reason = "stale_policy"
    if reason is not None:
        return PlanningCapabilities(reason=reason)
    if settings.plan_extraction_mode == "shadow":
        return PlanningCapabilities(extract=True, reason="shadow_observation_only")
    return PlanningCapabilities(
        extract=True,
        persist_proposals=True,
        automatic_writes=settings.plan_auto_actions_enabled,
        reason="eligible",
    )


def automatic_operation_allowed(
    capabilities: PlanningCapabilities, operation: str, fields: frozenset[str]
) -> bool:
    """Policy filter only; validated values and an executor grant are still required."""
    allowed = AUTOMATIC_OPERATION_FIELDS.get(operation)
    return bool(capabilities.automatic_writes and allowed and fields and fields <= allowed)
