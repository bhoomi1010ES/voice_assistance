# VAD Implementation Verification Report

**Date:** 2026-09-17  
**Scope:** Android microphone capture, energy VAD, Silero ONNX VAD, playback-echo filtering, React Native event forwarding, and JavaScript turn/barge-in handling.

## Executive result

The VAD implementation is structurally wired and internally consistent, but it is **conditionally verified**, not fully acceptance-tested.

| Area | Result |
|---|---|
| Native PCM capture and framing | Pass by code inspection |
| Energy VAD state machine | Pass by code inspection and dedicated tests present |
| Silero VAD state machine | Pass by code inspection and dedicated tests present |
| Silero model asset integrity | Pass: size and SHA-256 match the contract |
| Playback echo/barge-in policy | Pass by code inspection and dedicated TypeScript tests present |
| Focused Android test execution | Blocked before tests ran by React Native Gradle plugin compilation |
| Fresh physical-device VAD acceptance | Not verified in this audit |

## Implemented audio path

```text
AudioRecord
  -> 16 kHz / mono / signed PCM16
  -> 20 ms native PCM frames (320 samples)
  -> bounded PCM pipeline
  -> energy VAD + Silero VAD worker + wake-word worker
  -> semantic VAD events through the native module
  -> VoiceSocket turn and barge-in state machine
```

Relevant implementation files:

- [`AudioConfig.kt`](frontend/android/app/src/main/java/com/voiceaipoc/audio/AudioConfig.kt)
- [`PcmAudioPipeline.kt`](frontend/android/app/src/main/java/com/voiceaipoc/audio/PcmAudioPipeline.kt)
- [`AudioEngine.kt`](frontend/android/app/src/main/java/com/voiceaipoc/audio/AudioEngine.kt)
- [`VadEngine.kt`](frontend/android/app/src/main/java/com/voiceaipoc/vad/VadEngine.kt)
- [`SileroVadEngine.kt`](frontend/android/app/src/main/java/com/voiceaipoc/vad/silero/SileroVadEngine.kt)
- [`VoiceSocket.ts`](frontend/src/voice/VoiceSocket.ts)

## Configuration verified

### Energy VAD

The parallel deterministic energy VAD uses:

- Threshold: `-42 dBFS`.
- Input frame: `20 ms`.
- Speech start confirmation: `5` frames, therefore `100 ms`.
- Speech end confirmation: `15` frames, therefore `300 ms`.
- RMS-to-dBFS conversion with a `-120 dBFS` floor.
- Exact frame-size validation and an observable error counter.
- Reset of state and counters at every new capture session.

### Silero VAD

The active model path uses:

- Silero VAD `v6.2.1` through Android ONNX Runtime CPU `1.24.3`.
- Input sample rate: `16,000 Hz`.
- Inference input: `512` new samples, or `32 ms`, plus `64` samples of model context.
- Speech probability threshold: `0.5` inclusive.
- Speech start confirmation: `5` inference chunks, or `160 ms`.
- Speech stop hangover: `10` inference chunks, or `320 ms`.
- Worker queue: `8` native 20 ms frames, or `160 ms` of queue capacity.
- Explicit model presence, size, SHA-256, tensor-name, tensor-type, and tensor-shape validation.
- Recurrent state and 64-sample audio context retained inside the ONNX runtime adapter, not React Native.

The packaged model was independently checked during this audit:

```text
Size:   2,327,524 bytes
SHA256: 1A153A22F4509E292A94E67D6F9B85E8DEB25B4988682B7E174C65279D8788E3
```

These values match [`SileroVadModelContract.kt`](frontend/android/app/src/main/java/com/voiceaipoc/vad/silero/SileroVadModelContract.kt).

## State-machine verification

The Silero state machine correctly models:

`SILENCE -> SPEECH_START_PENDING -> SPEECH -> SPEECH_STOP_PENDING -> SILENCE`

The inspected tests cover threshold inclusivity, interrupted speech-start confirmation, one quiet chunk during speech, stop hangover, repeated speech cycles, reset behavior, and session stop behavior. The engine tests cover malformed frames, bounded queue overflow, runtime initialization failure, inference failure, frame assembly, repeated start/stop, and semantic event types that do not expose PCM arrays.

The energy VAD tests cover silence, speech classification, consecutive-frame start/end confirmation, duration calculation, threshold changes, malformed frames, non-mutating input, multiple segments, and session reset.

## Playback and barge-in behavior

The microphone remains active during assistant playback so real barge-in is possible. Playback audio is recorded in a bounded native reference buffer and compared against the microphone input.

Current playback protection is:

- Native playback reference: 3-second bounded ring.
- Correlation window: 40 ms.
- Echo lag search: `0–500 ms` in 20 ms steps.
- Echo similarity rejection threshold: `0.58`.
- TTS tail suppression: `180 ms` after playback ends.
- JavaScript playback-start guard: `1,200 ms`.
- Playback speech probability required for barge-in: `0.9`.
- Sustained speech required after a confirmed start: `480 ms`.
- Speech-end commit grace for a normal pause: `900 ms`.

Therefore, the current implementation does **not** silence VAD for five seconds while the assistant speaks. It uses an evidence-based guard and echo comparison while keeping the microphone active for interruption support.

## Test execution

The focused command attempted was:

```text
gradlew.bat :app:testDebugUnitTest \
  --tests com.voiceaipoc.vad.VadEngineTest \
  --tests com.voiceaipoc.vad.silero.SileroVadStateMachineTest \
  --tests com.voiceaipoc.vad.silero.SileroVadEngineTest \
  --tests com.voiceaipoc.audio.PlaybackEchoReferenceTest
```

Gradle 9.4.1 downloaded successfully, but execution stopped in `settings.gradle` before test discovery:

```text
Error resolving plugin [id: 'com.facebook.react.settings']
Unable to compile generated classes
```

This is an environment/build-plugin blocker, not a VAD assertion failure. No focused Android test result is being reported as passed until the Gradle plugin issue is resolved.

## Findings and risks

1. **Core VAD path is sound.** The capture format, frame sizes, Silero chunk contract, model validation, state transitions, native worker separation, and semantic event boundary are coherent.

2. **Queue overflow can reduce VAD accuracy.** Both the PCM pipeline and Silero queue use a drop-oldest policy. If inference falls behind, the recurrent model receives a discontinuous stream while retaining prior recurrent state. The status counters expose this through `droppedFrames`, so a physical acceptance run must confirm these counters remain zero under normal use.

3. **Two VAD sources feed the JavaScript turn state.** Energy VAD and Silero VAD have different start/end timings and both emit speech start/stop events. The existing timer clearing and 900 ms grace reduce duplicate commits, but physical testing should verify that one utterance produces exactly one commit and that short pauses do not split the turn.

4. **Silero activity is emitted during stop-pending.** The native engine emits periodic activity while either `SPEECH` or `SPEECH_STOP_PENDING`. The activity duration includes the configured hangover. The playback policy also requires high probability and echo checks, which limits false interruption risk, but this boundary should be monitored in device logs.

5. **Device validation remains required.** AEC and noise suppression availability varies by Android device and audio route. The implementation reports their status, but this repository audit cannot substitute for the required quiet-room, distance, loudspeaker-echo, real interruption, noise, and rapid pause/resume tests on the target phone.

## Recommended acceptance checklist

Run the focused Android tests after fixing the React Native Gradle plugin environment, then validate on the physical device:

- `sileroVad.available = true`
- `runtimeInitialized = true`
- `inferenceAvailable = true`
- `modelSha256Verified = true`
- `failedInferenceCount = 0`
- `droppedFrames = 0` during ordinary speech
- One speech-start and one speech-stop event per utterance
- A pause shorter than 900 ms does not commit the turn
- Assistant playback alone does not trigger barge-in
- Real speech after the 1,200 ms guard interrupts playback after the 480 ms confirmation window
- TTS tail audio does not trigger a new turn

No VAD source code was changed while producing this report. Only this report file was added.
