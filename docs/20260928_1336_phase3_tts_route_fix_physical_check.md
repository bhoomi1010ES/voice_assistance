# Phase 3 TTS route fix and physical check — 2026-09-28

## Todo / disposition

- [x] Verify the connected test device and backend readiness.
- [x] Trace the reported “LLM replies but does not speak” failure to the Android output route.
- [x] Remove the debug-only earpiece override and use the configured communication route.
- [x] Run focused Android audio unit tests and install the updated debug APK.
- [x] Verify on-device audio routing selects the loudspeaker; restore diagnostic volume settings.
- [x] Confirm that a completed reply is audible on the physical phone.
- [ ] Raise the active communication-speaker volume and verify the reply is comfortably audible.
- [ ] Complete the fresh labeled Phase 3 shadow sample, review disagreements and prove zero shadow side effects.
- [x] Restore local backend router settings to `off` / cohort `0`; verify health and readiness.

## Diagnosis and change

The debug build of `VoiceModule` always overrode the configured communication
route with `EARPIECE`. The existing default is `SPEAKER`, and the earpiece uses
the call-volume stream, which had a low level on the test phone. The LLM and
remote TTS provider could complete normally while audio was directed to the
quiet earpiece.

Changed `frontend/android/app/src/main/java/com/voiceaipoc/rn/VoiceModule.kt`
to pass `audioConfig.communicationDevicePreference` in debug and release builds
and removed the now-unused `BuildConfig` import. No TTS provider, gateway, or
router behavior was changed.

## Verification

- Connected device: OPPO CPH2527, serial `9b0ea196`; app package
  `com.voiceaipoc`; USB reverse mappings for ports 8000 and 8081 were in use.
- The new APK installed successfully. Android logs for the updated app show
  playback routed to `speaker` (device type 2), rather than `voice-speaker` /
  earpiece.
- `AudioPipelineTest`: 9 passed; `AudioRouteControllerTest`: 7 passed.
- Gradle packaged the debug APK and reported `assembleDebug` successful. The
  overall Gradle invocation returned nonzero at the final problems-report write
  (`AccessDeniedException`); native CMake tasks were excluded after the SDK
  compiler was denied by the environment. The packaged APK reused the existing
  native libraries.
- The automated audible probe did not get a complete STT/assistant/playback
  lifecycle, but the operator subsequently confirmed a direct reply is audible.
  The reply is still too quiet. `dumpsys audio` reports the voice-call stream
  at volume 2 of 9; the app uses `USAGE_VOICE_COMMUNICATION` with speech
  content, so the phone's communication volume controls this playback.
- The communication-speaker level should be raised with the device's volume-up
  button while the assistant is speaking, then checked with another reply.
  Adjusting volume remotely while no communication playback is active could
  change a different stream, so no additional device volume mutation was made.
- Diagnostic `volume_voice` and `volume_voice_earpiece` settings were restored
  to their prior values (`5` and `2`).
- Temporary local shadow observation was stopped. Backend `/health` is `ok`,
  `/ready` is `ready`, and resolved router settings are `off` / `0`.

## Gate status

The TTS route correction is installed and the Android route evidence is good,
physical audibility is confirmed, but the volume is too low pending adjustment.
Phase 3 remains **NOT PASSED**:
the required fresh labeled shadow sample, full category-level disagreement
review, and zero-side-effect evidence are incomplete. The Phase 0 automated
gate remains false; its separate owner manual override is unchanged. No canary
or `on` routing was enabled.
