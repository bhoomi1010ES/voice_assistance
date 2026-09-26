# Phase 0 confirmation marker matching fix

## Work completed

- Updated `backend/scripts/phase0_live_driver.py` to accept the narrow `cleanup` / `clean up` phrase variant when validating the structured pending task title.
- Reused the same bounded phrase matcher for the manifest's required transcript markers.
- Left Redis confirmation ownership, pending status, Yes handling, authorization, and write validation unchanged.

## Evidence

- The preserved failed run `phase0-live-20260925-1920-remaining-retry` created a valid `create_task` pending confirmation, but its title was `Perform phase zero test clean up`; the harness expected the equivalent `Phase Zero test cleanup` marker.
- Focused driver tests: `26 passed`.

## Files edited

- `backend/scripts/phase0_live_driver.py`
- `backend/tests/test_phase0_live_driver.py`

No backend service, mobile code, router flags, or confirmation-store behavior was changed.
