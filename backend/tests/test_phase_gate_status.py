from __future__ import annotations

import json

from app.core.phase_gate_status import (
    Phase0GateState,
    load_phase_gate_state,
    phase0_dependency_is_satisfied,
    phase0_effective_pass,
)


def test_automated_pass_satisfies_phase0_without_override() -> None:
    state = Phase0GateState(automated_phase0_pass=True)

    assert phase0_effective_pass(state) is True


def test_owner_override_satisfies_dependency_without_rewriting_automated_result() -> None:
    state = Phase0GateState(
        automated_phase0_pass=False,
        manual_verification_passed=True,
        owner_override=True,
        effective_gate_status="pass",
    )

    assert state.automated_phase0_pass is False
    assert phase0_effective_pass(state) is True


def test_incomplete_override_does_not_satisfy_phase0() -> None:
    state = Phase0GateState(
        automated_phase0_pass=False,
        manual_verification_passed=True,
        owner_override=False,
        effective_gate_status="pass",
    )

    assert phase0_effective_pass(state) is False


def test_state_file_loader_and_dependency_check(tmp_path) -> None:
    state_path = tmp_path / "phase_gate_overrides.json"
    state_path.write_text(
        json.dumps(
            {
                "phase0": {
                    "automated_phase0_pass": False,
                    "manual_verification_passed": True,
                    "owner_override": True,
                    "effective_gate_status": "pass",
                    "automated_evidence_unchanged": True,
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_phase_gate_state(state_path).phase0_effective_pass is True
    assert phase0_dependency_is_satisfied(state_path) is True
