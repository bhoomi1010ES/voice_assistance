"""Print effective project phase-gate status without rewriting evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.phase_gate_status import DEFAULT_PHASE_GATE_STATE_PATH, load_phase_gate_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_PHASE_GATE_STATE_PATH)
    args = parser.parse_args()

    phase0 = load_phase_gate_state(args.state_path)
    print(
        json.dumps(
            {
                "phase0": {
                    **phase0.model_dump(),
                    "phase0_effective_pass": phase0.phase0_effective_pass,
                }
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
