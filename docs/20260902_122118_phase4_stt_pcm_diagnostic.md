# Phase 4 Windows STT PCM diagnostic instrumentation

## Purpose

Added a disabled-by-default, one-turn diagnostic mode to preserve the exact
PCM payload accepted by the backend immediately before it is supplied to the
Windows Speech worker. This work prepares the required physical quality
diagnosis; it does not consume or create authoritative 10-turn acceptance
evidence.

## Changes

- Added `STT_DIAGNOSTIC_CAPTURE_ENABLED` and
  `STT_DIAGNOSTIC_CAPTURE_DIR` settings.
- Added `backend/app/stt/diagnostic_capture.py`.
- Integrated capture at the `STTTurn` Python-to-engine handoff. The captured
  bytes are unchanged and are bounded by the existing per-turn audio limit.
- Added raw PCM, WAV, metadata, signal statistics, and SHA-256 output at
  `scratch/phase4_stt_diagnostics/` when enabled.
- Added `scripts/phase4_stt_diagnostic_offline.py` for same-audio offline
  recognition through the configured Windows worker and WER calculation.
- Added focused regression tests in
  `backend/tests/test_stt_diagnostic_capture.py`.
- Updated `.env.example` with the temporary diagnostic settings.

## Verified

- Focused diagnostic and Windows STT tests: 9 passed.
- Ruff checks for all changed Python files: pass.
- Python compilation check: pass.
- No Android source or Windows worker implementation was changed.

## Physical status

The diagnostic capture is enabled only for the next live backend session.
The fixed sentence still needs to be spoken manually on the physical device.
The raw/WAV listenability check and live-versus-offline A/B result are pending.
Phase 4 remains `IMPLEMENTED — ACCEPTANCE PENDING`.
