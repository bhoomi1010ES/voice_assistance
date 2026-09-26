# Phase 0 PCM correlation fix

## Work completed

- Updated `backend/scripts/phase0_live_driver.py` to correlate microphone evidence by both the active session ID and turn ID across all collected records.
- Kept `first_pcm_received` as the primary evidence marker.
- Accepted a positive `frame_count` on a matching `turn_commit_received` event as corroborating PCM evidence when the collector observes the commit marker before the PCM marker.
- Preserved the existing Phase 0 artifacts, including the valid `P0-LIVE-001` row from run `phase0-live-20260925-1710`.

## Regression coverage

- Added session/turn isolation coverage.
- Added positive and zero-frame commit coverage.
- Focused test result: `24 passed` in `backend/tests/test_phase0_live_driver.py`.
- Replayed the preserved 1710 event artifact: `P0-LIVE-002` now reports PCM evidence as `True` from its matching session/turn records.

## Files edited

- `backend/scripts/phase0_live_driver.py`
- `backend/tests/test_phase0_live_driver.py`

No backend service, mobile app, or router behavior was changed.
