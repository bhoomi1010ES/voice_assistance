"""Effective phase-gate status with an explicit, auditable owner override."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PHASE_GATE_STATE_PATH = PROJECT_ROOT / "phase_gate_overrides.json"


class Phase0GateState(BaseModel):
    """Machine-readable Phase 0 automated and owner-override state."""

    model_config = ConfigDict(extra="forbid")

    automated_phase0_pass: bool = False
    manual_verification_passed: bool = False
    owner_override: bool = False
    effective_gate_status: Literal["pass", "not_passed"] = "not_passed"
    override_date: str | None = None
    owner: str | None = None
    reason: str | None = None
    automated_evidence_unchanged: bool = True

    @property
    def phase0_effective_pass(self) -> bool:
        """Return automated pass OR a valid explicit owner/manual override."""

        return self.automated_phase0_pass or (
            self.owner_override
            and self.manual_verification_passed
            and self.effective_gate_status == "pass"
        )


def phase0_effective_pass(state: Phase0GateState) -> bool:
    """Evaluate the Phase 0 dependency without changing automated evidence."""

    return state.phase0_effective_pass


def load_phase_gate_state(path: Path | None = None) -> Phase0GateState:
    """Load the versioned state file, optionally overridden for tests/tools."""

    state_path = path or Path(
        os.environ.get("PHASE_GATE_STATE_PATH", str(DEFAULT_PHASE_GATE_STATE_PATH))
    )
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    return Phase0GateState.model_validate(raw["phase0"])


def phase0_dependency_is_satisfied(path: Path | None = None) -> bool:
    """Return whether later phases may proceed past the Phase 0 dependency."""

    return phase0_effective_pass(load_phase_gate_state(path))
