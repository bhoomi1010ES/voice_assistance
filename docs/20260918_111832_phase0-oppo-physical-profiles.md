# Phase 0 Oppo physical profiles — 2026-09-18

## Device and build

- Device: Oppo CPH2527, Android 15, ADB serial `9b0ea196`.
- The current debug APK, including the Phase 0 diagnostic-session changes, was built and installed before this run.
- Samsung SM-M336BU and RMX5070 were not attached and were not claimed as tested.

## Physical capture result

`Phase0PhysicalAudioProfileTest.capturesAllAudioProcessingProfilesSeparately` passed on the connected Oppo: **1 test passed** in 8.575 seconds. Each profile used a separate bounded microphone session and retained no PCM.

| Profile | Diagnostic session | Audio session | AEC | NS | Silero probability | Frames | Errors |
|---|---|---:|---|---|---:|---:|---|
| DISABLED | `diag-1789710579619-1` | 118561 | not created / disabled | not created / disabled | 0.8778 | 68 | 0 |
| AEC_ONLY | `diag-1789710581847-2` | 118569 | created / enabled | not created / disabled | 0.9996 | 68 | 0 |
| NS_ONLY | `diag-1789710583909-3` | 118577 | not created / disabled | created / enabled | 0.9990 | 68 | 0 |
| AEC_NS | `diag-1789710585941-4` | 118585 | created / enabled | created / enabled | 0.9858 | 68 | 0 |

Common metadata recorded for all four sessions:

- Output route types: `2,1,18`.
- Android audio mode: `0`.
- Capture source: `MIC`.
- Capture format: 16 kHz, mono, PCM16.
- Playback usage metadata: `USAGE_MEDIA`.
- AEC/NS support: both reported supported.
- Silero inference ran successfully; no read, pipeline, or overflow errors occurred.

## Still pending

The profile harness did not start TTS, so `echo_similarity` is `N/A` and `barge_in_result` is `NOT_RUN_NO_TTS`. A valid authenticated voice session and human audibility confirmation are still required for:

1. TTS-only false-interruption trace from playback start through VAD and cancellation.
2. Real-user interruption attempt while TTS is playing.
3. Echo similarity and actual barge-in result for each acoustic profile.

The app currently reaches its sign-in screen; no credentials were invented and no app data was cleared.
