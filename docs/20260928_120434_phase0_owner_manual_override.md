# Phase 0 owner manual override — 2026-09-28

## Decision

The project owner explicitly accepted the remaining Phase 0 automated-gate risk
after completing manual physical-device verification. Phase 0 is therefore
effective for project progression through an owner manual override.

```text
Phase 0: PASS (Manual Owner Override)
automated_gate_passed: false
manual_verification_passed: true
owner_override: true
effective_gate_status: pass
```

Automated Phase 0 reconciliation did not meet every stored acceptance criterion. The project owner completed manual physical-device verification and explicitly accepted the remaining risk on 2026-09-28. Phase 0 is therefore considered complete for project progression via manual owner override.

## Evidence policy

- `phase_gate_overrides.json` is the versioned machine-readable status.
- Existing Phase 0 summaries, rows, reconciliation JSON, excluded turns,
  metrics, route totals, and write results are unchanged.
- `backend/scripts/phase0_live_driver.py` continues to report the automated
  gate independently; this override does not relabel an automated run.
- The effective status only satisfies the dependency for later project phases.

## Dependency result

Phase 1 and Phase 2 are no longer blocked by the Phase 0 dependency. Phase 3
is unblocked and remains pending its own live shadow acceptance. The router is
not enabled by this record; local `ROUTER_MODE=off` and cohort `0` remain safe
defaults until the Phase 3 run is explicitly started.

The fresh execution plan is in
[`20260928_120434_phase3_live_shadow_owner_override_plan.md`](20260928_120434_phase3_live_shadow_owner_override_plan.md).
