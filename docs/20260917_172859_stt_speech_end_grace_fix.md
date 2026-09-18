# STT speech-end grace fix

Date: 2026-09-17

## Problem

STT stopped before the user finished speaking. The attached recording was treated as reproduction evidence; its content was not treated as additional instructions.

The client committed the STT turn immediately when native Silero VAD reported speech stopped. Native VAD reports that event after a short 320 ms silence hangover, so a natural pause inside a sentence prematurely sent an incomplete audio buffer to the remote STT service.

## Changes

- Added a 900 ms speech-end commit grace period in `frontend/src/voice/VoiceSocket.ts`.
- A new speech-start event cancels the pending commit and keeps the same STT turn open.
- Pending timers are cleared during turn completion, cancellation, stop, disconnect, and transport failure/reset paths.
- Updated `frontend/__tests__/phase9-barge-in.test.ts` to verify delayed commit, resumed speech, and pending server-turn readiness.
- Added this work record to the tracked `docs` allowlist in `.gitignore`.

## Verification

- `npm.cmd test -- --runInBand --silent __tests__/phase9-barge-in.test.ts` — 14 tests passed.
- `npm.cmd run typecheck` — passed.
- `git diff --check` — passed.
