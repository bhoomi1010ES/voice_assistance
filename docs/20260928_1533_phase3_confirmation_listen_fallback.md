# Phase 3 live shadow and confirmation listening — 2026-09-28

## Outcome

Phase 3 remains **NOT PASSED**. The device was online during the sample, the
local backend was healthy, and live shadow observations were recorded. The
remaining evidence is incomplete: two route disagreements need disposition,
the cold route decision exceeded its 250 ms target, and the final physical
reminder cancellation could not be performed after the phone disconnected.

## Todo and verification

- [x] Verify Android device `9b0ea196` (`CPH2527`), app installation, and
  `RECORD_AUDIO` permission. Device was online and the app was foregrounded
  during testing; ADB later listed no devices after the build.
- [x] Verify backend health/readiness before the final live attempt.
- [x] Reconcile labeled physical runs: 16 valid completed turns; 4 excluded
  turns (harness re-entry, transcript threshold, or playback lifecycle).
- [x] Review live shadow telemetry: 19 decided observations plus 1 skipped
  pending-confirmation observation; no shadow-route tool execution or writes.
- [x] Add a microphone-only “Start listening” fallback to the pending-action
  card. It is disabled unless the authenticated session is connected and ready,
  the turn is idle, and TTS is not speaking. It does not approve an action.
- [x] Run focused React Native UI test (1 passed), TypeScript check, ESLint, and
  Android `:app:assembleDebug` (successful using a temporary short drive alias).
- [ ] Install the APK and physically verify the fallback plus spoken rejection.
- [ ] Complete memory-disabled, prompt-injection, and multilingual physical
  cases; disposition all shadow disagreements and cold-start latency.
- [x] Stop the temporary shadow backend; repository configuration remains
  `ROUTER_MODE=off`, `ROUTER_COHORT_PERCENT=0`.

## Live shadow evidence

Privacy-safe `router.shadow.observation` telemetry in
`logs/phase3_shadow_backend_20260928_run2.err.log` and
`logs/phase3_shadow_backend_20260928_run3.err.log` contains 19 decided routes
and 1 skipped turn (`pre_router_confirmation_resolution`). Decided routes span
direct clock tools, structured reads, general LLM, task actions, and mixed
ambiguous intent. The two disagreements are:

- `STRUCTURED_READ -> MIXED_AMBIGUOUS`: safe over-clarification; retain for
  now, but include in the review record.
- `MEMORY_QUERY -> GENERAL_LLM`: unresolved; this may cause a personal-fact
  query to miss memory retrieval. It must not be treated as accepted until the
  exact labeled case is identified and the rule decision is dispositioned.

Observed latency p95/max was **1413.278 ms** across the small decided sample;
the warmed observations were about 6–10 ms. The first cold observation exceeds
the configured **250 ms** deadline, so either graph startup must be included in
bounded latency or explicitly warmed and measured before the Phase 3 gate is
accepted.

Physical and row artifacts include:

- `logs/phase3_shadow_physical_manifest_20260928.json`
- `logs/phase0_physical_baseline_rows_phase3-shadow-*-20260928.jsonl`
- `logs/phase0_physical_baseline_summary_phase3-shadow-*-20260928.json`
- `logs/phase0_physical_baseline_phase3-shadow-*-20260928.jsonl`

The final test used a reminder proposal. The row was excluded because STT
similarity was 0.883; it records zero tool calls, zero write attempts, and zero
confirmed writes. Backend telemetry shows one `TASK_ACTION` shadow decision
agreeing with the legacy route and a `PENDING` legacy confirmation. The app UI
showed the confirmation card but did not enter a listening state, preventing
the rejection turn. The pending confirmation TTL is 120 seconds, so it expires
without a reminder mutation; this attempt is not counted as a successful
cancellation test.

## Listening fallback and device handoff

`ToolConfirmationCard` previously contained only spoken-approval instructions
and replaced the voice control. The automatic follow-up microphone start relies
on the playback-completed lifecycle event. This run did not record that event,
and there was no manual control to recover. The card now provides a
microphone-only **Start listening** button; voice or a later explicit UI path
must still make the decision. It is disabled until the voice session is ready,
the turn is idle, and playback has stopped.

The debug APK built successfully. Installing it failed because ADB no longer
listed `9b0ea196`; restarting the ADB server did not restore the connection.
The package was therefore not changed on the phone. Reconnect/authorize it,
install `frontend/android/app/build/outputs/apk/debug/app-debug.apk`, and run a
spoken “No, cancel that reminder” test before claiming the confirmation/listen
gate.

The phone’s `STREAM_VOICE_CALL` speaker level was raised from 2/9 to 6/9 after
the operator reported low volume. The current Android output route was the
speaker. The operator confirmed speech was audible before the adjustment, but
comfort at 6/9 has not yet been reconfirmed.

## Files changed and checks

- `frontend/src/components/voice/ToolConfirmationCard.tsx` — add safe
  start-listening fallback.
- `frontend/src/screens/AssistantScreen.tsx` — gate the fallback on connected,
  ready, idle, and not-speaking state.
- `frontend/src/i18n/strings.ts` — add the button label.
- `frontend/__tests__/phase7-ui.test.tsx` — verify the fallback calls
  `startTurn` and no approval controls are introduced.
- `plan.md` — update Phase 3 status and blockers.
- `.gitignore` — allowlist this report under the ignored docs directory.

Verification: focused UI test passed; `tsc --noEmit` passed; touched-file
ESLint passed; Android debug build passed. The full UI test file had one
unrelated 5-second timeout in “Tasks destination exposes upcoming, all, and
completed views”; its other six tests passed. The APK install and final physical
cancellation remain blocked by the disconnected ADB device.

No canary or `on` routing was enabled. The temporary local shadow backend was
stopped after this sample; the checked-in runtime setting remains `off`/`0`.
