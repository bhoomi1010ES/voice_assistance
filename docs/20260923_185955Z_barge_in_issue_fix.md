# Barge-in issue fix: fail-closed reference fallback

Date: 2026-09-23 11:59:55 America/Los_Angeles (2026-09-23 18:59:55 UTC)

## Context and diagnosis

The prior saved trace contains three degraded decisions with `reference_ready=false`, but no current physical-device trace. It does not establish whether the reported loudspeaker interruption failure is caused by reference production/alignment, route health, VAD evidence, or a later lifecycle step.

A separate code-level safety defect was reproducible: when far-end RMS was absent, the detector treated that unknown value as a quiet far end. In the degraded playback path, sustained high-energy VAD could therefore be accepted as near-end evidence even though TTS leakage could not be measured. This change closes that fail-open path; it does not prove that it caused the physical-device failure or repair the still-unverified far-end reference path.

## Implemented

- Changed `PlaybackAwareBargeInDetector` so absent far-end RMS is unknown rather than quiet.
- During playback, the detector now emits a degraded reference reason instead of confirming when the presentation-aligned reference is missing or has unreliable timing.
- Preserved the stricter degraded confirmation path when the reference is usable but AEC is unavailable: high Silero probability, measurable near-end residual evidence, and the existing 640 ms confirmation duration remain required.
- Added native coverage for permitted-speaker near-end speech with unavailable AEC and a usable reference, sustained high-energy input with missing or unreliable reference, response-replacement guard reset, and speech-stop confirmation reset. Existing high-similarity echo rejection and rollout-off route-gate tests remain covered.
- Did not add a loudspeaker override or enable the early playback guard; both remain unchanged pending device evidence.

## Files changed

- `frontend/android/app/src/main/java/com/voiceaipoc/vad/PlaybackAwareBargeInDetector.kt`
- `frontend/android/app/src/test/java/com/voiceaipoc/vad/PlaybackAwareBargeInDetectorTest.kt`
- `barge_in_issue_fix.plan.md` (echo-fallback test item completed; physical items remain pending)
- `.gitignore` (allowlist this required work note under the ignored `docs/` directory)

## Verification

- Focused Android unit tests: `gradlew.bat --no-daemon testDebugUnitTest --tests com.voiceaipoc.vad.PlaybackAwareBargeInDetectorTest --tests com.voiceaipoc.rollout.VoiceRolloutConfigTest` — passed. Gradle compiled the changed Kotlin and completed the selected test task.
- JavaScript lifecycle regression: `npm.cmd test -- --runInBand __tests__/phase9-barge-in.test.ts` — passed, 21 tests.
- `git diff --check` — passed (Git printed only its existing LF-to-CRLF notices).
- `adb devices -l` — no devices attached. No APK was deployed and no physical trial or current device trace was produced.

## Remaining acceptance and rollback

Physical loudspeaker TTS-only and real-interruption trials remain open, as do current route/AEC/reference diagnosis and first-second guard measurement. The old trace must not be treated as physical acceptance. The default 1,200 ms guard remains active, and no loudspeaker override was added. Roll back this detector change by reverting the two detector edits if device testing exposes an unacceptable regression; do not loosen the echo threshold or enable AEC3 as part of this fix.
