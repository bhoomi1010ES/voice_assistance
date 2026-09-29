# Phase 3 reconnect, muted TTS, and physical follow-up — 2026-09-28

## Outcome

Phase 3 remains **NOT PASSED**. The Android device is connected, the updated
debug APK is installed, and a physical clock response was heard. The muted-TTS
lifecycle deadlock was fixed and its build/unit test passed. Gate blockers
remain: shadow/legacy route disagreements, cold-start latency above 250 ms, no
completed physical rejection/cancellation test, and missing memory-disabled,
prompt-injection, and multilingual physical cases.

## Device and verification

- Phone: CPH2527 (`9b0ea196`), `RECORD_AUDIO` granted; USB ADB and reverse ports
  8000/8081 verified.
- Rebuilt `frontend/android/app/build/outputs/apk/debug/app-debug.apk` installed
  successfully. Metro and a local legacy (`off`) backend were running for the
  live voice checks.
- A fresh spoken clock question finalized through STT, matched `DIRECT_TOOL` in
  shadow and legacy, and produced TTS. The cold shadow decision took
  **1254.616 ms**, above the **250 ms** target.
- The user subsequently confirmed hearing a fresh spoken clock answer. The
  app's Voice output preference is enabled; phone volume was not changed during
  this follow-up.
- Repository `.env` is `ROUTER_MODE=off`, `ROUTER_COHORT_PERCENT=0`. No canary
  or `on` route was enabled.

## Muted-output lifecycle fix

Android discarded TTS frames when Voice output was muted but emitted no
terminal playback event for the final frame. The JS voice state therefore
remained `waiting`, disabling the safe “Start listening” fallback and blocking
the follow-up confirmation turn. The transport now emits
`tts.playback.completed` when it intentionally skips a muted response's final
frame; it still does not play or claim to play audio. A focused Kotlin test
covers muted final/non-final frames and enabled output.

Verification passed:

- `:app:testDebugUnitTest --tests com.voiceaipoc.voice.TtsAudioFrameTest`
- `:app:assembleDebug`
- APK install on the connected CPH2527
- Physical follow-up microphone turn after a confirmation proposal while TTS
  output was muted

During that live action test, the phone accepted an affirmative voice response
and created the proposed `phase three test` task. The user asked for deletion.
I matched the exact task by title, owner, and creation time from the test
confirmation evidence, deleted only that pending task, and verified zero
matching test tasks remain. This is **not** a successful rejection/cancellation
test and must not be counted as zero-write evidence.

## Remaining Phase 3 blockers

- The physical spoken rejection/cancellation path is not yet verified. The
  confirmation test above ended in an affirmative and its test task was
  removed afterward.
- Six additional shadow decisions included further disagreements, including
  `TASK_ACTION` vs `STRUCTURED_READ` and `TASK_ACTION` vs `GENERAL_LLM`; these
  must be identified/dispositioned before any cutover. The prior unresolved
  `MEMORY_QUERY` vs `GENERAL_LLM` mismatch remains open.
- The fresh cold route decision exceeded the configured 250 ms deadline; the
  historical sample p95/max was 1413.278 ms. A complete warm/cold latency
  acceptance and agreed target remain outstanding.
- Memory-disabled, prompt-injection, and multilingual physical cases remain
  untested.
- The UIAutomator physical harness could not locate the React Native scroll
  view during its startup sample. Manual device interaction was used instead;
  repair or disposition the harness accessibility limitation before relying
  on that automated workflow.
- Phase 9 rollout acceptance is still NOT PASSED; keep router configuration
  `off`/`0`.

## Files changed in this follow-up

- `frontend/android/app/src/main/java/com/voiceaipoc/voice/TtsAudioFrame.kt` —
  helper to recognize muted final-frame completion.
- `frontend/android/app/src/main/java/com/voiceaipoc/voice/VoiceWebSocketTransport.kt` —
  emit the playback terminal event for skipped muted output.
- `frontend/android/app/src/test/java/com/voiceaipoc/voice/TtsAudioFrameTest.kt` —
  focused lifecycle coverage.
- `plan.md` and `.gitignore` — update Phase 3 status and retain this report.

The temporary shadow backend was stopped after sampling. A local backend in
legacy `off` mode remains available for the connected test device; the normal
runtime configuration is unchanged.
