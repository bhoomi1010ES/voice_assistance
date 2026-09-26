# Phase 0 collector barge marker fix

## Work completed

- Updated `backend/scripts/live_latency.py` so `barge_in_confirmed` with `playback_active=false` is treated as a completed-playback diagnostic marker, not as response cancellation.
- This preserves later confirmation and tool-write telemetry for the same response while retaining cancellation behavior for active barge-in and explicit cancellation events.
- Added a regression test proving a committed tool event remains accepted after a completed-playback marker.

## Evidence

- Run `phase0-live-20260925-1945-writes` showed the confirmed `create_task` commit succeeded, but the collector marked it `stale_cancelled_response` after a normal post-playback barge marker. The harness therefore could not verify the commit.
- Focused tests: `40 passed` across `test_live_latency.py` and `test_phase0_live_driver.py`.

## Files edited

- `backend/scripts/live_latency.py`
- `backend/tests/test_live_latency.py`

No application routing, confirmation-store ownership, or tool authorization behavior was changed.
