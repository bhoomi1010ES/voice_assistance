---
name: Barge-in issue fix
overview: Diagnose the remaining physical loudspeaker interruption failure, repair the proven acoustic path, and release any gate or guard change only after echo safety is demonstrated.
todos:
  - id: capture-current-device-trace
    content: Reproduce a long TTS-only segment and a real user interruption on the current Android build; record response IDs, route/AEC health, reference readiness, detector decisions, native stop, cancel, and replacement-turn outcome.
    status: pending
  - id: diagnose-reference-and-route
    content: Determine whether the current failure is missing or unreliable far-end reference, the loudspeaker route gate, playback guard, Silero probability/near-end evidence, or a later lifecycle failure; record the decisive trace for each attempt.
    status: pending
  - id: repair-proven-path
    content: Repair the demonstrated reference or detector path, preserve the existing response-scoped native stop and JS preroll/cancel lifecycle, and add an independent off-by-default loudspeaker override only if no_safe_acoustic_path is proven to block real speech.
    status: pending
  - id: protect-echo-fallback
    content: Ensure missing or unreliable reference and loudspeaker TTS leakage cannot confirm as user speech; cover strict degraded confirmation and echo rejection with focused native tests.
    status: completed
  - id: evaluate-early-guard
    content: Measure first-second interruptions separately; enable the existing 250 ms guard only after TTS-only safety and real-speech tests pass, and retain a rollback switch.
    status: pending
  - id: verify-and-record
    content: Run focused detector/rollout and barge-in lifecycle tests, complete physical TTS-only and interruption acceptance, verify the new query is answered, and write the required timestamped docs note after implementation.
    status: pending
isProject: false
---

# Barge-in issue fix

## Current status and decision

The native stop/JavaScript ordering fix and detector telemetry were implemented on 2026-09-22; see [the lifecycle work note](docs/20260922_104007_barge_in_lifecycle_fix.md). The remaining question is why a real interruption does not produce a usable `BARGE_IN_CONFIRMED` on the physical loudspeaker path. Do not redo the completed stop-race work without evidence of a regression.

The source currently blocks loudspeaker confirmation unless communication mode and AEC are healthy in `VoiceModule.currentBargeInRouteHealth()`. The detector returns `no_safe_acoustic_path` before echo evaluation when that permission is false. The default output preference is `SPEAKER`, and early barge-in remains disabled, leaving a 1,200 ms playback guard.

The latest saved `logs/live_latency_trace.jsonl` has only three `barge_in_degraded` decisions from one recorded segment. All three report `automatic_loudspeaker_barge_in_allowed=true` and `reference_ready=false`; none report `no_safe_acoustic_path`, echo rejection, or confirmation. These are limited historical samples and do not establish what the current APK does during a deliberate interruption. The preceding work note specifically left gate relaxation pending device evidence.

## 1. Capture the failing path

Use the same current build for two response-scoped trials: TTS playing with no user speech, then a clear user question during a long TTS answer. Keep the diagnostic session ID and response ID with each trace. Collect:

- Actual speaker route, `MODE_IN_COMMUNICATION`, platform/software AEC status, and `automaticLoudspeakerBargeInAllowed`.
- PCM reference `referenceReady`, timing confidence, echo similarity/coherence, far-end RMS, mic RMS, and near-end residual.
- Silero start/activity/stop and probability, playback position, detector event/reason, and segment duration.
- Native `stopReason=barge_in`, `client.response.cancel` with `reason=barge_in`, preroll/replacement turn, final transcript, and response audio.

If the device is unavailable, complete the code and automated-test investigation, but leave physical acceptance open. Do not infer a physical pass from unit tests or old traces.

## 2. Repair according to the observed cause

| Observed cause | Planned action |
|---|---|
| `reference_ready=false` or timing confidence unusable during real playback | Trace `VoiceWebSocketTransport.onPcmWritten` into the shared `FarEndReferenceBuffer` and `AudioEngine.assess`; repair the missing or misaligned reference while preserving response ownership. |
| `no_safe_acoustic_path` on the actual speaker route despite real near-end speech | Add a separate, default-off loudspeaker override instead of reusing `automaticLoudspeakerBargeInEnabled` (currently derived from detector rollout flags). In the enabled route, retain AEC/reference health as inputs to the stricter 0.90 probability and 640 ms confirmation policy. |
| Reference is reliable, but TTS-only is treated as near-end speech | Repair the evidence/echo policy before enabling a loudspeaker override. Do not assume `isEchoDominant()` is sufficient when the reference or far-end energy is absent. |
| Real speech is discarded only during the first 1,200 ms | Evaluate the existing 250 ms early guard independently after the TTS-only safety test passes. A 250 ms guard still has a separate 480/640 ms confirmation requirement; it is not a 250 ms stop guarantee. |
| Native confirms but cancellation or replacement fails | Recheck the existing response-scoped stop, `VoiceSocket` pending barge-in ownership, and preroll flow; change only the failing lifecycle step. |

Keep the override and early-guard settings independently reversible. Do not enable WebRTC AEC3 or lower echo thresholds as part of this diagnosis.

## 3. Focused tests

Extend `frontend/android/app/src/test/java/com/voiceaipoc/vad/PlaybackAwareBargeInDetectorTest.kt` to cover real near-end speech on a permitted speaker route with unavailable AEC, high-similarity TTS-only rejection, missing/unreliable reference with sustained TTS-like activity, and the rollout-off `no_safe_acoustic_path` case. Test response replacement and speech-stop reset around the chosen guard. If a new rollout flag is added, cover its default and override in `VoiceRolloutConfigTest.kt`.

Run the focused Kotlin detector/rollout tests and `frontend/__tests__/phase9-barge-in.test.ts` for any lifecycle interaction. Run the existing buffer/audio tests if reference production changes. Avoid a full app or API build unless the specific change requires it.

## 4. Physical acceptance and rollback

On the current physical build, TTS-only and silence must produce zero cancellations and zero false replacement turns. A clear interruption during a long answer must stop the matching AudioTrack response, send a matching cancel, upload the user's speech with preroll, and speak an answer to the new question. Repeat an interruption in the first second and a later one; record detection-to-stop latency and every decision reason. Restore the default-off loudspeaker override or early guard if false confirmations appear.

After code implementation, add `docs/<current date and time>_barge_in_issue_fix.md` with the cause, changed files, automated results, device/build identity, physical traces, remaining limits, and rollback setting. Update this plan's todo statuses only when their checks are complete.
