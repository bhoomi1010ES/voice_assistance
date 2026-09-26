# Phase 0 STT cleanup phrase matching fix

## Work completed

- Updated `backend/scripts/phase0_live_driver.py` so the acceptance matcher treats the STT phrase `clean up` as the spoken equivalent of the manifest marker `cleanup`.
- Kept the remaining required markers, transcript similarity threshold, confirmation checks, and write-safety checks unchanged.

## Evidence

- The preserved run `phase0-live-20260925-1900-remaining` recorded the correct spoken request as `Remind me to perform phase zero test clean up tomorrow at 9am.` with similarity `0.984`; it was rejected only because of the spacing variant.
- Focused driver tests: `25 passed`.

## Files edited

- `backend/scripts/phase0_live_driver.py`
- `backend/tests/test_phase0_live_driver.py`

No backend service behavior, mobile code, router flags, or confirmation semantics were changed.
