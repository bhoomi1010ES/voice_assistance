# AEC, Playback-Echo Rejection, and Reliable Barge-In — End-to-End Implementation Plan

**Document status:** Implementation-ready plan  
**Prepared:** 2026-09-17  
**Repository scope:** React Native Android client in `frontend/`, native Kotlin audio pipeline, React Native voice orchestration, and existing FastAPI cancellation lifecycle  
**Primary outcome:** Assistant loudspeaker audio must not interrupt its own response, while real user speech during playback must stop TTS quickly and become a complete replacement turn without clipping the start of the utterance.

---

## 1. Executive decision

The current repository has a complete response-cancellation and replacement-turn flow, but it does not yet have a reliable acoustic separation layer.

The implementation must be completed in this order:

1. Put microphone capture and TTS playback on one Android communication route.
2. Measure the PCM that is actually presented by `AudioTrack`, rather than treating accepted writes as rendered sound.
3. Preserve capture timestamps and playback-reference metadata with the exact PCM that produces each Silero inference.
4. Make one native, playback-aware near-end speech decision.
5. Stop local playback immediately in native code, then reuse the existing JavaScript/server cancellation and replacement-turn lifecycle.
6. Add software AEC3 only when the required device matrix proves that platform AEC is insufficient.

Threshold changes alone are not an acceptable fix. The current playback policy is already stricter than the proposed `0.75 / 150 ms` policy, yet false interruptions can still occur because the echo signal and VAD decision are not correctly aligned.

---

## 2. Verified current implementation

### 2.1 Active application and audio formats

- The active React Native application is under `frontend/`.
- Android minimum SDK is 24, target SDK is 36, and compile SDK is 37.
- Microphone capture is 16 kHz, mono, signed PCM16.
- The microphone pipeline produces 20 ms frames: 320 samples / 640 bytes.
- Silero consumes 512 new samples per inference: 32 ms.
- TTS playback is 24 kHz, mono, signed PCM16.
- The microphone remains open during assistant playback.

### 2.2 Current capture and preprocessing path

```text
AudioRecord(AudioSource.MIC)
  -> platform AcousticEchoCanceler attached to the AudioRecord session when available
  -> platform NoiseSuppressor attached to the AudioRecord session when available
  -> PcmAudioPipeline, 20 ms frames
       -> PlaybackEchoReference.assess(...)
       -> energy VAD
       -> asynchronous Silero queue
       -> wake-word queue
       -> VoiceWebSocketTransport / pre-roll
```

Relevant implementation:

- `frontend/android/app/src/main/java/com/voiceaipoc/audio/AudioConfig.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/audio/AudioEngine.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/audio/AudioEffectsManager.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/audio/PcmAudioPipeline.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/vad/silero/SileroVadEngine.kt`

### 2.3 Current playback path

```text
24 kHz TTS PCM from WebSocket
  -> TtsAudioPlayer queue
  -> 200 ms startup prebuffer
  -> AudioTrack with USAGE_MEDIA
  -> blocking AudioTrack.write(...)
  -> written bytes copied into PlaybackEchoReference
```

The callback named `onPcmRendered` is invoked after `AudioTrack.write()` accepts bytes. It does not prove that those samples have left the speaker. The class therefore tracks written position, not presentation position.

Relevant implementation:

- `frontend/android/app/src/main/java/com/voiceaipoc/voice/TtsAudioFrame.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/voice/TtsAudioPlayer.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/voice/VoiceWebSocketTransport.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/audio/PlaybackEchoReference.kt`

### 2.4 Current echo classification

`PlaybackEchoReference` currently:

- downsamples the 24 kHz TTS bytes into a 16 kHz bounded ring;
- keeps up to 3 seconds of reference PCM;
- compares microphone PCM with reference PCM over lags from 0 to 500 ms;
- declares likely echo at normalized correlation `>= 0.58`;
- keeps a fixed 180 ms playback-tail state.

This is a playback-echo classifier. It does not subtract the far-end signal or produce cleaned microphone PCM.

The intended 40 ms comparison window also collapses to the available 20 ms microphone frame during normal operation.

### 2.5 Current Silero and barge-in policy

Native Silero uses:

- probability threshold `0.5`;
- five 32 ms speech chunks, therefore 160 ms, to start speech;
- ten 32 ms silence chunks, therefore 320 ms, to stop speech;
- a separate bounded worker queue.

JavaScript playback-time policy uses:

- 1,200 ms playback-start guard;
- playback probability threshold `0.9`;
- 480 ms sustained speech confirmation;
- rejection when echo similarity is `>= 0.58` or native metadata says echo is likely;
- rejection when no playback reference is available.

Relevant implementation:

- `frontend/android/app/src/main/java/com/voiceaipoc/vad/silero/SileroVadConfig.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/vad/silero/SileroVadStateMachine.kt`
- `frontend/src/voice/playbackBargeInPolicy.ts`
- `frontend/src/voice/VoiceSocket.ts`

### 2.6 Existing lifecycle that should be preserved

The following flow is already implemented and tested:

1. A confirmed barge-in stops local playback.
2. The old response correlation is retired.
3. `client.response.cancel` is sent with reason `barge_in`.
4. The server cancels old TTS/LLM/STT work.
5. The microphone stays open.
6. Native PCM pre-roll is included in the replacement turn.
7. The replacement turn starts without waiting for the old cancellation acknowledgement.
8. Late old-response events and TTS frames are ignored.
9. The new turn is committed after speech ends.

The backend already protects the pending replacement turn during a barge-in cancellation. No redesign of the WebSocket protocol or backend state machine is required for the acoustic fix.

### 2.7 Existing evidence and constraints

- Focused JavaScript policy/lifecycle tests pass: 21 tests across the playback policy and Phase 9 barge-in suite.
- Existing native echo tests use perfectly aligned synthetic audio. They do not cover AudioTrack buffering, loudspeaker-to-microphone delay, room response, volume changes, nonlinear speaker distortion, or double-talk.
- The project plan records that AEC+NS on Samsung SM-M336BU previously suppressed or altered real speech enough that Silero produced no confirmed speech; AEC-only and NS-only must therefore remain independently selectable.
- Android unit tests are currently blocked before test execution because Gradle cannot snapshot the reparse-point file `frontend/node_modules/react-native-safe-area-context/src/specs/NativeSafeAreaContext - Copy.ts`.
- `implementation.md` contains conflicting historical Phase 9 status text. Completion must be based on new acoustic evidence, not existing checked boxes.

---

## 3. Root-cause model

The expected false-interruption sequence is:

```text
TTS plays as USAGE_MEDIA
  -> loudspeaker audio enters microphone
  -> capture uses generic AudioSource.MIC
  -> platform AEC may be enabled but is not operating on a fully configured
     communication route
  -> residual assistant speech reaches Silero
  -> PlaybackEchoReference compares against bytes written ahead of the audible
     playback head
  -> the asynchronously emitted Silero event reads the latest global echo
     assessment, not necessarily the assessment for its source PCM
  -> correlation is low/stale even though the signal is assistant echo
  -> after the startup guard, high-probability residual TTS satisfies the
     playback confirmation rule
  -> local TTS is incorrectly stopped
```

Four separate defects must be addressed:

1. **Route defect:** `MIC` capture and `USAGE_MEDIA` playback do not explicitly select Android's duplex communication path.
2. **Signal defect:** the explicit TTS reference is used only for classification, not cancellation.
3. **Timing defect:** accepted AudioTrack writes are not the same as samples presented by the speaker.
4. **Ownership defect:** echo assessment, Silero inference, and the final JavaScript decision operate on different frames and clocks.

---

## 4. Target architecture

### 4.1 Primary platform-AEC path

```text
                              Native Android duplex session

24 kHz TTS PCM -----------------------------------------------------------+
  |                                                                      |
  v                                                                      |
AudioTrack                                                               |
USAGE_VOICE_COMMUNICATION                                                |
  |                                                                      |
  +-> written-frame counter                                              |
  +-> AudioTimestamp / playback-head position                            |
  +-> timestamped 24 kHz far-end ring                                    |
             |                                                           |
             v                                                           |
      quality resampler 24 kHz -> 16 kHz                                 |
             |                                                           |
             +---------------------- aligned far-end reference --------+ |
                                                                         | |
AudioRecord                                                              | |
AudioSource.VOICE_COMMUNICATION                                          | |
  -> platform AEC                                                        | |
  -> optional platform NS                                                | |
  -> 16 kHz / mono / PCM16 / 20 ms frames                                | |
  -> capture timestamp + frame sequence                                  | |
  -> exact reference alignment <-----------------------------------------+ |
  -> echo / double-talk features                                           |
  -> Silero inference with the same frame metadata                         |
  -> native PlaybackAwareBargeInDetector                                   |
       |                                                                   |
       +-- TTS-only / echo --> continue playback                            |
       |                                                                   |
       +-- near-end user speech --> stop/flush local TTS immediately       |
                                    emit BARGE_IN_CONFIRMED                 |
                                    preserve mic + pre-roll                 |
                                    JS sends existing response cancel       |
                                    JS starts replacement turn              |
```

### 4.2 Conditional software-AEC path

If platform AEC fails the required device/route matrix, insert a software echo canceller behind a common interface:

```text
timestamped 16 kHz far-end PCM -> Software AEC reverse/render stream
raw/communication mic PCM      -> Software AEC capture stream
                                 -> cleaned near-end PCM
                                 -> Silero + STT transport + detector
```

The recommended fallback is WebRTC Audio Processing/AEC3 through Android NDK/JNI. A home-grown subtraction filter is out of scope because it will not reliably handle delay changes, nonlinear loudspeaker response, or double-talk.

Platform AEC and software AEC must not be active simultaneously unless controlled measurements prove that the combination is safe. The selected implementation must be visible in diagnostics.

### 4.3 Required ownership boundary

All low-latency acoustic decisions should be native:

- PCM capture and playback remain native.
- Reference alignment remains native.
- Echo/double-talk metadata remains attached to native PCM/inference units.
- Barge-in confirmation occurs natively.
- Local TTS stop occurs natively and idempotently.
- React Native receives only semantic metadata and continues to own UI and conversation orchestration.
- Raw PCM must not cross the React Native bridge.

---

## 5. Configuration model

Add a single native configuration model rather than scattering constants across Kotlin and TypeScript.

```text
DuplexAudioConfig
  captureSource = VOICE_COMMUNICATION
  playbackUsage = VOICE_COMMUNICATION
  audioMode = MODE_IN_COMMUNICATION
  platformAecRequested = true
  platformNsRequested = configurable
  softwareAecMode = PLATFORM | AUTO | WEBRTC_AEC3

BargeInConfig
  normalSpeechThreshold
  normalStartConfirmationChunks
  playbackSpeechThreshold
  playbackStartConfirmationChunks
  playbackGuardMs
  echoSimilarityRejectThreshold
  minimumNearEndToFarEndRatioDb
  playbackTailMs
  activityIntervalChunks
```

Initial calibration values—not final release values:

| State | Probability | Confirmation | Notes |
|---|---:|---:|---|
| TTS inactive | `0.50–0.55` | 4–5 chunks, `128–160 ms` | 100 ms cannot be represented exactly with 32 ms Silero chunks |
| TTS active, aligned reference and AEC healthy | start at `0.85` | 5–8 chunks, `160–256 ms` | Tune downward toward `0.75` only with device evidence |
| TTS active, reference/AEC degraded | `0.90` | 15 chunks, `480 ms` | Conservative fallback matching current behavior |

The current 1,200 ms no-interruption guard should be retained behind a feature flag during development, then reduced using measured reference-readiness time. The release goal is `<= 250 ms`; it must not be reduced until TTS-only trials are clean.

---

## 6. Phased implementation

## Phase 0 — Baseline, build hygiene, and reproducible evidence

### Goal

Create a reliable before-state and unblock native tests before changing signal behavior.

### Development steps

- [x] Remove the reparse/copy anomaly from `frontend/node_modules` using a clean dependency reinstall; do not commit generated dependency contents.
- [x] Run the focused Android JVM suites and record their results:
  - `AudioPipelineTest`
  - `PlaybackEchoReferenceTest`
  - `TtsAudioPlayerTest`
  - `SileroVadStateMachineTest`
  - `SileroVadEngineTest`
- [x] Run the focused React Native suites:
  - `playback-barge-in-policy.test.ts`
  - `phase9-barge-in.test.ts`
  - `continuous-chat.test.ts`
- [x] Add a repeatable diagnostic-session identifier to native TTS, capture, VAD, and barge-in logs.
- [x] Record a baseline physical run on each currently available reference device, including Samsung SM-M336BU and RMX5070 where available.
- [x] Capture four effect profiles separately: disabled, AEC-only, NS-only, and AEC+NS.
- [ ] Record route, Android mode, capture source, playback usage, audio session ID, AEC/NS support/created/enabled state, Silero probability, echo similarity, and barge-in result.
- [x] Do not store diagnostic PCM by default. Require an explicit diagnostic toggle and keep any captures app-private with a deletion path.

### Gate

- [x] All focused automated tests reach execution.
- Baseline false-interruption reproduction has timestamped logs.
- At least one TTS-only failure and one real-user interruption attempt can be traced from playback start through VAD and cancellation.
- [x] The chosen baseline is archived in a dated document under `docs/`.

---

## Phase 1 — Android duplex route and lifecycle correctness

### Goal

Give platform AEC the audio route it is designed to process and make route ownership deterministic.

### New implementation

Create:

- `frontend/android/app/src/main/java/com/voiceaipoc/audio/AudioRouteController.kt`
- `frontend/android/app/src/test/java/com/voiceaipoc/audio/AudioRouteControllerTest.kt`

Modify:

- `AudioConfig.kt`
- `AudioEngine.kt`
- `AudioEffectsManager.kt`
- `TtsAudioPlayer.kt`
- `VoiceModule.kt`
- `VoiceModule.ts`
- `DiagnosticScreen.tsx`

### Development steps

- [x] Replace the legacy `AudioRecord(...)` constructor with `AudioRecord.Builder`.
- [x] Set capture source to `MediaRecorder.AudioSource.VOICE_COMMUNICATION` for duplex voice sessions.
- [x] Preserve an explicit diagnostic option for `MIC` so the route can be A/B tested without code changes.
- [x] Change TTS `AudioAttributes` from `USAGE_MEDIA` to `USAGE_VOICE_COMMUNICATION` with `CONTENT_TYPE_SPEECH`.
- [x] Add `AudioRouteController` to acquire `AudioManager.MODE_IN_COMMUNICATION` before creating capture/playback objects.
- [x] Save and restore the prior `AudioManager.mode` on final session release.
- [x] Ensure mode acquisition/release is reference-counted or session-owned so reconnects and repeated start/stop calls cannot restore the mode too early.
- [x] On API 31+, expose and optionally select an `AudioManager` communication device; clear it at session end.
- [x] Define fallback behavior for speaker, earpiece, wired headset, Bluetooth SCO, and BLE audio.
- [x] Request audio focus using communication audio attributes and handle focus loss by stopping or pausing TTS without leaking microphone resources.
- [x] Attach AEC/NS only after a valid `AudioRecord` session exists.
- [x] Treat AEC and NS as independent policies. Default NS must remain configurable because prior device evidence shows speech suppression.
- [x] Detect whether the platform route already exposes an enabled effect and avoid treating “created” as proof of acoustic effectiveness.
- [x] Make startup transactional: if route, recorder, downstream worker, or playback initialization fails, restore mode/device/focus and release all partial resources.
- [x] Add diagnostics for requested and actual source, usage, mode, communication device type, playback route, audio focus, and restoration state.

### Required tests

- [x] Mode is acquired before recorder/player creation.
- [x] Previous mode is restored exactly once after normal stop, initialization failure, transport failure, and module invalidation.
- [x] Repeated start/stop and reconnect cycles do not leak focus or communication-device selection.
- [x] AEC-only, NS-only, combined, and disabled configurations remain independently selectable.
- [x] TTS cancellation still stops and releases the active `AudioTrack`.

### Gate

- [ ] Diagnostics prove `VOICE_COMMUNICATION` capture and playback plus `MODE_IN_COMMUNICATION` are active during a duplex session.
- [ ] TTS and real speech remain audible/detectable in AEC-only and NS-only comparison runs.
- [ ] No microphone, AudioTrack, effect, focus, mode, or route leak occurs across 20 start/stop cycles.

### Phase 1 implementation evidence — 2026-09-18

- [x] Android JVM lifecycle suite passes, including mode ordering, transactional failure cleanup, focus-loss behavior, and 20 reconnect cycles.
- [x] Android pipeline suite covers the communication-source default, MIC A/B override, AEC-only, NS-only, combined, and disabled policy selections.
- [x] Frontend typecheck and Jest suites pass; diagnostics expose requested/actual source, usage, mode, device, playback route, focus, restoration, and effect effectiveness.
- [x] Backend suite passes under the no-device harness mode: 327 passed, 53 skipped. The physical-validation runner's ADB clock probe was bypassed for this software-only run.
- [ ] Physical-device acoustic and 20-cycle acceptance remains pending because no Android device is available in this environment.

---

## Phase 2 — Presentation-timed far-end reference

### Goal

Build a reference timeline that represents what the speaker is actually presenting.

### New or replaced implementation

Create or refactor toward:

- `frontend/android/app/src/main/java/com/voiceaipoc/audio/FarEndReferenceBuffer.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/audio/PcmResampler.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/audio/DuplexAudioFrame.kt`
- corresponding JVM tests under `frontend/android/app/src/test/java/com/voiceaipoc/audio/`

`PlaybackEchoReference.kt` may be migrated in place initially, but the final name/API must stop describing accepted writes as rendered PCM.

### Development steps

- [x] Extend `TtsAudioTrack` with playback-head and timestamp access suitable for fakes and Android production code.
- [x] Track written frames and presented frames separately.
- [x] Poll `AudioTrack.getTimestamp(AudioTimestamp)` where supported; use a wrap-safe `playbackHeadPosition` fallback.
- [x] Timestamp every far-end chunk in the monotonic `elapsedRealtimeNanos` clock domain.
- [x] Replace the current sample-skipping 24-to-16 kHz conversion with a stateful, anti-aliased resampler.
- [x] Keep the far-end ring bounded and preallocated; no allocation should occur for every 20 ms microphone frame.
- [x] Query the reference by microphone capture interval, not by “latest bytes written minus guessed lag.”
- [x] Preserve a bounded delay-search correction for acoustic propagation/HAL latency, but anchor it to presentation time.
- [x] Increase analysis context to multiple frames, initially 80–160 ms, while maintaining a low-latency rolling result.
- [x] Track reference readiness, timestamp confidence, estimated delay, correlation/coherence, far-end RMS, mic RMS, and near-end residual ratio.
- [x] Replace the fixed 180 ms playback tail with a configurable route policy; start with a conservative 300 ms diagnostic default.
- [x] Do not emit `tts.playback.completed` until queued PCM has been presented or explicitly stopped/flushed.
- [x] Distinguish these lifecycle states: buffering, playing, draining, tail suppression, stopped, and completed.

### Required tests

- [x] Reference alignment with AudioTrack writes ahead of the playback head by 50, 100, 200, and 500 ms.
- [x] Playback-head wraparound.
- [x] Missing/unreliable `AudioTimestamp` fallback.
- [x] Partial AudioTrack writes and queue backpressure.
- [x] 24-to-16 kHz resampler continuity across arbitrary chunk boundaries.
- [x] Impulse, swept-frequency, speech-like, gain-scaled, delayed, and phase-shifted reference cases.
- [x] Playback completion waits for drain, while cancellation stops and flushes immediately.
- [x] Tail state expires from the actual final presentation timestamp.

### Gate

- [x] For deterministic fixtures, the estimated reference interval is within one 20 ms capture frame of the injected acoustic delay.
- [ ] A TTS-only device recording produces stable far-end/reference evidence through the full utterance and tail.
- [x] TTS completion no longer advances ahead of actual playback completion in the playback-head drain tests.

### Phase 2 implementation evidence — 2026-09-18

- [x] `FarEndReferenceBuffer` replaces the write-relative `PlaybackEchoReference` API.
- [x] `PcmResampler` is stateful, FIR low-pass/anti-aliased, and allocation-free during steady-state processing.
- [x] `DuplexAudioFrame` provides a passive capture-interval/reference metadata carrier for the Silero ownership queue.
- [x] Focused Android JVM suite passes with presentation alignment, delay search, timestamp fallback, resampler, drain, cancellation, and wraparound coverage.
- [ ] Physical TTS-only recording and route-specific tail measurement remain pending because no Android device is available.

---

## Phase 3 — Preserve frame ownership through Silero

### Goal

Ensure every Silero probability and transition carries the acoustic metadata for the exact PCM interval that produced it.

### New implementation

Introduce a bounded metadata-aware queue rather than using a PCM-only `AudioRingBuffer` inside `SileroVadEngine`.

Suggested structures:

```text
DuplexAudioFrame
  frameSequence
  captureStartNs
  captureEndNs
  pcm16[320]
  playbackState
  playbackResponseId
  referenceReady
  referenceConfidence
  estimatedEchoDelayMs
  echoSimilarity
  farEndRms
  micRms

SileroInferenceObservation
  inferenceIndex
  sourceFrameSequenceStart
  sourceFrameSequenceEnd
  captureStartNs
  captureEndNs
  probability
  aggregated echo/reference metadata
```

### Development steps

- [x] Add a monotonic sequence and capture timestamp when each 20 ms frame leaves `PcmAudioPipeline`.
- [x] Compute or attach the aligned reference assessment before offering the frame to Silero.
- [x] Replace Silero's PCM-only frame queue with a fixed-capacity queue that copies PCM and metadata together.
- [x] When 512 samples are assembled from multiple 20 ms frames, aggregate metadata over the same contributing sample interval.
- [x] Emit the inference observation with every speech-start, activity, and speech-stop event.
- [x] Remove `playbackEchoReference.latestAssessment()` from `VoiceModule` event emission.
- [x] Use native monotonic timestamps as the source of ordering and duration. Keep wall-clock timestamps only for logs/UI.
- [x] Reset PCM, metadata, recurrent Silero state, and sequence ownership together on every session restart.
- [x] When the Silero queue drops a frame, mark the next observation discontinuous and reset/re-prime recurrent state rather than silently retaining state across missing audio.
- [x] Extend `SileroVadEvent` in TypeScript to include `SILERO_VAD_SPEECH_ACTIVITY` and the new typed metadata fields.

### Required tests

- [x] A transition carries metadata from its source frames, even when newer microphone frames have already been assessed.
- [x] A 512-sample inference assembled across frame boundaries reports the correct interval.
- [x] Queue overflow cannot associate one frame's PCM with another frame's metadata.
- [x] Session restart cannot emit stale playback response IDs or assessments.
- [x] Wall-clock changes do not affect playback age or speech duration.

### Gate

- [x] Logs can trace one Silero event back to exact capture frame sequences and one TTS presentation interval.
- [x] No global “latest assessment” is used in the decision path.

### Phase 3 implementation evidence — 2026-09-18

- [x] `PcmAudioPipeline` emits session-local monotonic frame sequences and capture intervals.
- [x] `DuplexAudioFrameQueue` preallocates PCM/metadata slots and drops PCM plus metadata atomically.
- [x] `SileroInferenceObservation` aggregates the exact 512-sample source interval and carries presentation metadata through all Silero events.
- [x] Queue overflow resets/re-primes the recurrent runtime and marks the next observation discontinuous.
- [x] Android JVM and frontend suites pass with frame ownership, restart isolation, overflow, monotonic timing, and typed activity-event coverage.

---

## Phase 4 — Native playback-aware near-end detector

### Goal

Replace split native/JavaScript classification with one deterministic native detector that differentiates TTS echo from real user double-talk.

### New implementation

Create:

- `frontend/android/app/src/main/java/com/voiceaipoc/vad/BargeInConfig.kt`
- `frontend/android/app/src/main/java/com/voiceaipoc/vad/PlaybackAwareBargeInDetector.kt`
- `frontend/android/app/src/test/java/com/voiceaipoc/vad/PlaybackAwareBargeInDetectorTest.kt`

### Detector state machine

```text
IDLE
  -> PLAYBACK_GUARD
  -> ECHO_ONLY
  -> NEAR_END_PENDING
  -> NEAR_END_CONFIRMED
  -> COOLDOWN / RESET
```

### Decision inputs

- actual playback/tail state;
- playback response ID;
- reference readiness and timestamp confidence;
- aligned echo similarity/coherence;
- far-end energy;
- microphone/residual energy;
- Silero probability;
- consecutive qualifying inference duration;
- route and AEC implementation health;
- discontinuity/drop flags.

### Development steps

- [x] Keep normal non-playback speech behavior independent from playback barge-in behavior.
- [x] Reject TTS-only candidates when aligned far-end evidence dominates.
- [x] Allow real double-talk even when some echo correlation is present; do not use a single correlation threshold as an unconditional veto.
- [x] Require both a Silero condition and near-end/residual evidence while TTS is active.
- [x] Apply stricter fallback settings when the reference is unavailable or timing confidence is poor.
- [x] Do not permanently suppress a genuine user utterance merely because it began during a fixed startup guard. Continue evaluating the segment as reference/AEC confidence becomes available.
- [x] Emit typed semantic events:
  - `BARGE_IN_CANDIDATE`
  - `BARGE_IN_REJECTED_ECHO`
  - `BARGE_IN_CONFIRMED`
  - `BARGE_IN_DEGRADED`
- [x] Include reason codes and metrics, never raw PCM.
- [x] Make confirmation idempotent per playback response ID.
- [x] Reset detector state on playback completion, playback stop, response replacement, microphone restart, and transport teardown.

### Initial reason codes

```text
normal_speech
playback_guard
reference_not_ready
reference_timing_unreliable
far_end_dominant
echo_similarity_high
near_end_energy_insufficient
silero_probability_low
confirmation_incomplete
near_end_confirmed
queue_discontinuity
aec_unavailable
```

### Required tests

- [x] TTS-only speech-like input never confirms barge-in.
- [x] Near-end-only speech during playback confirms.
- [x] Mixed user+TTS speech confirms when near-end evidence is sufficient.
- [x] A guard-started real utterance can become confirmed after reference readiness.
- [x] A guard-started continuous echo never becomes near-end speech merely because time passes.
- [x] Short impacts, keyboard clicks, TV/music, and noise do not confirm.
- [x] Reference loss moves to the documented degraded policy.
- [x] Only one confirmation is emitted per response.

### Gate

- [x] Synthetic fixtures pass TTS-only, near-end-only, double-talk, normal-speech, degraded-reference, and discontinuity classification.
- [ ] Recorded loudspeaker/device fixtures pass TTS-only, near-end-only, and double-talk classification (pending Android device access).
- [x] The decision no longer depends on React Native timer accuracy.

### Phase 4 implementation evidence — 2026-09-18

- [x] `PlaybackAwareBargeInDetector` consumes native Silero capture/presentation metadata, tracks the documented state machine, and confirms using monotonic capture duration rather than React Native timers.
- [x] `BargeInConfig` keeps thresholds, guard/confirmation durations, fallback policy, and reason codes configurable.
- [x] `VoiceModule` emits metric-only typed semantic events and resets detector ownership on playback, response, microphone, transport, and module lifecycle boundaries.
- [x] Android `VoiceSocket` adapters expose the semantic stream; production bypasses the legacy JavaScript playback classifier while compatibility fakes retain their existing test behavior.
- [x] Focused Phase 4 Android JVM tests pass (12 detector tests); frontend TypeScript and Jest suites pass (`tsc --noEmit`, 7 suites, 50 tests).
- [ ] Physical device/audio gate remains pending because no Android device is available in this environment.

---

## Phase 5 — Immediate local stop and existing conversation handoff

### Goal

Stop audible TTS with minimum latency while retaining the proven client/server cancellation lifecycle.

### Development steps

- [x] Wire `BARGE_IN_CONFIRMED` to the active native playback response ID.
- [x] Stop and flush `TtsAudioPlayer` immediately on the native side before waiting for the React Native bridge.
- [x] Make native stop idempotent so the later existing `stopVoicePlayback()` call is harmless.
- [x] Emit the confirmed event to React Native after local stop has been requested, including response ID and monotonic detection/stop timestamps.
- [x] Update `VoiceSocket.ts` to treat native confirmation as authoritative.
- [x] Keep `playbackBargeInPolicy.ts` temporarily as a legacy/fallback detector behind a feature flag; do not allow both detectors to trigger independently.
- [x] Preserve the existing order: retire old correlation, cancel old response, start replacement turn with pre-roll.
- [x] Ensure pre-roll contains the cleaned/selected microphone path, not far-end TTS reference audio.
- [x] Preserve pending confirmation behavior: a tool-confirmation prompt must follow its explicit product policy and must not be cancelled by generic playback VAD.
- [x] Add an acknowledgement path in diagnostics showing local-stop request, AudioTrack stopped/flushed, cancel sent, server cancelled, replacement turn ready, and first replacement PCM sent.

### Backend scope

No protocol redesign is expected. Re-run and extend existing tests around:

- cancel-before-turn-start;
- turn-start-before-cancel;
- delayed cancellation acknowledgement;
- stale old TTS chunks;
- old cleanup after replacement response creation;
- Redis cancellation failure fallback.

### Required latency metrics

```text
first qualifying near-end frame -> BARGE_IN_CONFIRMED
BARGE_IN_CONFIRMED -> AudioTrack stop/flush requested
AudioTrack stop/flush -> client.response.cancel queued
client.response.cancel -> response.cancelled received
BARGE_IN_CONFIRMED -> replacement turn ready
BARGE_IN_CONFIRMED -> first replacement PCM sent
```

### Gate

- Local playback stops without waiting for network acknowledgement.
- The first user word is present in replacement-turn pre-roll.
- A late event or audio frame from the old response cannot restart playback or reset the new turn.

### Phase 5 implementation evidence — 2026-09-18

- Native `TtsAudioPlayer.stopForBargeIn(responseId)` is response-scoped, issues `AudioTrack.stop()` and `flush()` synchronously, defers only release when a blocking write is active, and suppresses duplicate stop notifications.
- Android emits `BARGE_IN_CONFIRMED` only after the local stop request, with response ID, detection/stop monotonic timestamps, stop/flush/release state, and release-pending state. `DiagnosticScreen` displays the acknowledgement without raw PCM.
- `VoiceSocket` accepts native confirmation as authoritative, skips the second JavaScript stop, retires old correlation, queues cancellation, and starts the replacement turn with microphone pre-roll. Adapters without native semantic events retain the guarded legacy fallback.
- Automated validation: Android JVM audio/VAD/TTS lifecycle tests pass; frontend typecheck passes; frontend Jest passes 7 suites / 51 tests; focused backend lifecycle/protocol tests pass 15 tests; full backend suite reaches 327 passed / 53 skipped, with the existing physical-validation artifact test blocked by repository-directory permissions.
- The physical-device gate remains open because no Android device is available. Device evidence is still required for local audible-stop latency, first-word pre-roll capture, stale old-response audio suppression, and the 20-cycle no-leak check.

---

## Phase 6 — Conditional WebRTC AEC3 fallback

### Entry condition

Enter this phase if any required loudspeaker route fails the platform-AEC release gate after Phases 1–5.

Examples:

- repeated TTS-only false barge-ins;
- residual assistant speech remains highly intelligible in captured PCM;
- platform AEC is unavailable;
- OEM AEC destroys near-end speech or behaves inconsistently across routes;
- reference classification is correct but residual echo still harms STT.

### Development steps

- [x] Select and pin a reproducible WebRTC Audio Processing/AEC3 source contract and record the BSD/PATENTS notice requirements. The source is intentionally not vendored in this checkout; see `WEBRTC_AEC3_INTEGRATION.md`.
- [x] Add an NDK/JNI boundary owned by a narrow `EchoCanceller` Kotlin interface. The JNI shim builds safely without the optional backend and reports `WEBRTC_AEC3_UNAVAILABLE`.
- [x] Normalize render and capture streams to the exact frame size/sample rate required by the selected AEC API, using synchronized 10 ms mono frames.
- [x] Feed presentation-aligned far-end PCM to the reverse/render stream.
- [x] Feed captured PCM to the capture stream with the configured render-to-capture delay.
- [x] Return cleaned PCM and AEC metrics without exposing native pointers or PCM to React Native.
- [x] Route cleaned PCM consistently to Silero, wake-word processing, pre-roll, and STT transport through the existing native PCM pipeline.
- [x] Disable platform AEC while software AEC is active; `AUTO` retains platform AEC when the optional backend is absent.
- [x] Keep software NS independently selectable from platform NS. Route-specific WebRTC-vs-platform NS effectiveness measurement remains a device task.
- [x] Add watchdog/fallback behavior: an AEC error or invalid frame enters a visible degraded bypass and keeps capture alive.
- [ ] Record CPU, memory, thermal, and battery impact in release builds.

### Phase 6 implementation evidence — 2026-09-18

- The opt-in `PLATFORM`/`AUTO`/`WEBRTC_AEC3` policy is wired through `AudioConfig`, `AudioEngine`, native diagnostics, and TypeScript diagnostics. `PLATFORM` remains the safe default.
- The optional JNI library and all four configured Android ABIs compile. The actual WebRTC APM/AEC3 backend is still an explicit build input (`-PvoiceAec3BackendDir=...`) and is not present in this checkout, so no software-AEC effectiveness claim is made yet.
- `SoftwareAecControllerTest` and the broader Android audio/VAD/TTS JVM suite pass; frontend typecheck passes. The physical-device gate remains open because no Android device is available.

### Gate

- Software AEC passes every required device/route scenario that failed platform AEC.
- No audible instability, pumping, clipping, or unacceptable near-end speech damage.
- Sustained processing stays inside the release CPU/thermal budget with zero frame drops.

---

## Phase 7 — Diagnostics, calibration, and observability

### Goal

Make acoustic behavior explainable on a physical device without logging private speech content.

### Development steps

- [x] Extend native and TypeScript status models with:
  - requested/actual capture source;
  - playback usage/content type;
  - Android audio mode;
  - input/output device types;
  - platform/software AEC selection and health;
  - NS selection and health;
  - written/presented playback frames;
  - reference readiness/timestamp confidence;
  - estimated echo delay;
  - echo similarity/coherence;
  - near-end/far-end energy ratio;
  - detector state and last reason;
  - confirmed/rejected/degraded counters;
  - local-stop latency.
- [x] Add these fields to `DiagnosticScreen.tsx` with a compact live session view.
- [x] Add a scripted trial runner for TTS-only, user-only, and double-talk trials.
- [x] Export metadata-only JSON evidence using session-owned IDs.
- [x] Redact transcript, PCM, tokens, and provider secrets from acoustic logs.
- [x] Add explicit diagnostic PCM capture consent, maximum duration, app-private storage, and delete action only if recorded-fixture generation is needed.
- [ ] Update `implementation.md` and `vad_report.md` only after new evidence resolves their historical status contradictions.

### Gate

- Every accepted/rejected candidate has a reason and exact response/frame correlation.
- A tester can distinguish route failure, AEC failure, reference-timing failure, VAD failure, and orchestration failure from metadata alone.

---

## Phase 8 — Automated, recorded-fixture, and physical acceptance

### 8.1 Automated test layers

#### Kotlin/JVM

- audio route ownership and restoration;
- effect profile selection;
- playback-head/reference timing;
- resampler continuity;
- metadata-aware Silero queue;
- playback-aware detector state machine;
- AudioTrack drain versus immediate cancellation;
- stale response and repeated lifecycle cleanup.

#### React Native/Jest

- authoritative native barge-in event handling;
- legacy detector feature flag;
- no duplicate interruption;
- confirmation prompt policy;
- replacement turn and pre-roll;
- stale event/audio rejection;
- disconnect/reconnect cleanup.

#### Backend/Pytest

- existing barge-in lifecycle suite;
- delayed and reordered cancel/start events;
- cancellation when Redis marking fails;
- cancellation during STT, LLM, tool, and TTS stages;
- late cleanup isolation.

### 8.2 Recorded acoustic fixtures

Build a versioned fixture manifest containing metadata and expected classification for:

- clean TTS-only playback;
- TTS-only playback with room reverb and multiple delays;
- near-end user only;
- user plus TTS double-talk at several signal ratios;
- TV/music/background conversation;
- keyboard, taps, coughs, and short non-speech impulses;
- changing speaker volume during playback;
- reference discontinuity and queue drop;
- playback stop plus echo tail.

Do not commit real user speech without explicit consent. Prefer synthetic, licensed, or purpose-recorded test voices.

### 8.3 Physical matrix

Run at minimum:

| Dimension | Required values |
|---|---|
| Device | Samsung SM-M336BU, RMX5070, plus at least one additional required production-class device |
| Output route | loudspeaker, earpiece where supported, wired headset, Bluetooth headset |
| Loudspeaker volume | 25%, 50%, 75%, 100% |
| User distance | 0.5 m, 1 m, 2 m |
| Environment | quiet room, moderate room noise, TV/music background |
| Speech timing | first 250 ms, middle of response, end/tail |
| Effect profile | AEC-only, NS-only, AEC+NS, disabled diagnostic baseline |
| Voice | at least two TTS voices and multiple user voices |

### 8.4 Release acceptance targets

- **TTS-only false barge-in:** zero in 100 complete TTS responses per required loudspeaker volume/device combination.
- **Continuous soak:** zero false barge-ins in a 30-minute TTS-only run per required loudspeaker route.
- **True barge-in detection:** at least 95% of scripted user interruptions at 0.5 m and 1 m in quiet/moderate noise.
- **Early interruption:** real speech beginning in the first 250 ms is not permanently suppressed.
- **Local stop latency:** p95 `<= 350 ms` from first qualifying user speech to local AudioTrack stop; report detection and stop components separately.
- **Post-confirmation stop latency:** p95 `<= 50 ms` from native confirmation to AudioTrack stop request.
- **First-word retention:** 100% of scripted replacement utterances retain the first intended word in the committed turn.
- **Duplicate replacement turns:** zero.
- **Stale old-response playback:** zero.
- **Ordinary-load drops:** zero PCM pipeline drops and zero Silero/AEC processing drops.
- **Runtime stability:** no crash, ANR, leaked audio mode/device/focus, or stuck microphone across 20 consecutive duplex turns.
- **Speech quality:** no accepted route/profile may reproduce the earlier failure where effects prevent Silero from detecting normal near-end speech.

Any target that cannot be met must result in a documented route/device fallback, not an undocumented threshold exception.

### Phase 8 implementation status

- [x] Existing Kotlin/JVM, React Native/Jest, and backend/Pytest acceptance layers cover the listed lifecycle, routing, timing, detector, playback, and cleanup behaviors.
- [x] Added the versioned metadata-only acoustic fixture manifest at `backend/tests/fixtures/phase8_acoustic_fixtures_v1.json`.
- [x] Added `scripts/phase8_acceptance.py` to evaluate release targets, enforce redaction, preserve pending status for missing evidence, and require documented fallbacks for failed targets.
- [ ] Execute the physical device/route/volume/distance/environment matrix and attach metadata-only evidence.
- [ ] Mark release targets accepted only after the physical evidence satisfies the thresholds above.

---

## Phase 9 — Rollout and safe fallback

### Feature flags

Introduce release-configurable flags:

```text
duplexCommunicationRouteEnabled
presentationTimedReferenceEnabled
nativeBargeInDetectorEnabled
legacyJsBargeInDetectorEnabled
platformAecEnabled
platformNsEnabled
softwareAecMode
earlyBargeInEnabled
```

### Rollout sequence

1. Ship diagnostics only; behavior remains legacy.
2. Enable communication routing for internal devices.
3. Enable presentation-timed reference while retaining legacy decisions.
4. Run native detector in shadow mode and compare its decisions with legacy JavaScript.
5. Enable native local stop for internal/canary devices.
6. Enable native detector as authoritative; disable legacy detector.
7. Enable software AEC only for device/routes that fail platform-AEC acceptance.
8. Remove obsolete guard/correlation code only after at least one stable release and complete evidence review.

### Runtime fallback hierarchy

```text
platform communication AEC healthy
  -> platform AEC + calibrated detector

platform AEC inadequate and software AEC accepted
  -> software AEC + calibrated detector

reference timing unavailable or AEC degraded
  -> conservative playback threshold/confirmation
  -> expose degraded status

no safe acoustic path
  -> disable automatic loudspeaker barge-in for that route
  -> preserve explicit/manual stop and headset behavior
```

The application must prefer a predictable degraded experience over repeatedly cancelling its own speech.

### Phase 9 implementation status

- [x] Added all eight native release switches as Gradle BuildConfig fields with safe defaults.
- [x] Added a typed JavaScript runtime override model for internal/canary builds.
- [x] Implemented native-authoritative, native-shadow/legacy-authoritative, legacy-only, and diagnostics-only detector modes.
- [x] Gated native local playback stop on native-authoritative mode.
- [x] Added conservative untimed-reference behavior and an explicit no_safe_acoustic_path degraded decision for unsafe loudspeaker routes.
- [x] Exposed rollout mode and route-policy state through native status and metadata-only diagnostic evidence.
- [ ] Complete physical canary rollout and acoustic acceptance evidence before changing release defaults or removing legacy code.

---

## 7. File-level change inventory

### Native Android audio

| File | Planned change |
|---|---|
| `audio/AudioConfig.kt` | Add capture source, playback usage, route, AEC/NS, reference, and detector configuration |
| `audio/AudioEngine.kt` | Use communication capture, timestamp frames, select cleaned PCM path, attach exact metadata |
| `audio/AudioEffectsManager.kt` | Separate effect policy from capability; improve platform-effect diagnostics |
| `audio/PcmAudioPipeline.kt` | Emit sequenced/timestamped frames without per-frame allocation |
| `audio/PlaybackEchoReference.kt` | Replace write-relative matching with presentation-timed far-end alignment or migrate to `FarEndReferenceBuffer` |
| `audio/AudioRouteController.kt` | New owner for mode, focus, communication device, and restoration |
| `audio/PcmResampler.kt` | New stateful 24-to-16 kHz reference resampler |
| `audio/DuplexAudioFrame.kt` | New fixed metadata contract for PCM ownership |

### Native VAD and playback

| File | Planned change |
|---|---|
| `vad/silero/SileroVadEngine.kt` | Replace PCM-only queue, preserve metadata through 512-sample inference |
| `vad/silero/SileroVadConfig.kt` | Keep model threshold separate from playback-aware decision configuration |
| `vad/PlaybackAwareBargeInDetector.kt` | New native double-talk/near-end state machine |
| `vad/BargeInConfig.kt` | New typed configuration and validation |
| `voice/TtsAudioPlayer.kt` | Communication usage, playback timestamps/head, drain-aware completion, native idempotent stop |
| `voice/VoiceWebSocketTransport.kt` | Wire playback timeline, response correlation, and local-stop telemetry |
| `rn/VoiceModule.kt` | Remove global latest-assessment lookup; emit typed native barge-in events/status |

### React Native

| File | Planned change |
|---|---|
| `src/native/VoiceModule.ts` | Type route/reference/detector status and activity/confirmed events |
| `src/voice/playbackBargeInPolicy.ts` | Convert to legacy fallback/shadow comparison, then retire |
| `src/voice/VoiceSocket.ts` | Consume authoritative native confirmation and preserve existing cancellation/new-turn flow |
| `src/screens/DiagnosticScreen.tsx` | Display route, effect, reference, detector, and latency state |

### Backend

No production behavior change is initially required. Revalidate:

- `backend/app/websocket/gateway.py`
- `backend/app/websocket/protocol.py`
- `backend/tests/test_voice_barge_in_lifecycle.py`
- `backend/tests/test_voice_stt_integration.py`
- `backend/tests/test_voice_gateway_integration.py`

---

## 8. Definition of done

This project is complete only when all of the following are true:

- [ ] Capture and playback use a verified Android communication route.
- [ ] The application knows written versus actually presented TTS frames.
- [ ] Every playback-time Silero decision carries its exact reference/capture metadata.
- [ ] The native detector separates TTS-only echo, real near-end speech, and double-talk.
- [ ] Local TTS stops natively and immediately on one idempotent confirmation.
- [ ] Existing cancellation, pre-roll, replacement-turn, and stale-response protections still pass.
- [ ] AEC-only, NS-only, combined, and disabled diagnostic profiles are measurable.
- [ ] Required physical devices and routes pass the complete volume/distance/noise matrix.
- [ ] Release latency, false-trigger, true-detection, first-word, stability, and drop targets pass.
- [ ] Platform/software AEC fallback behavior is documented and observable.
- [ ] `implementation.md`, `README.md`, and `vad_report.md` are updated to one consistent evidence-backed status.
- [ ] A dated implementation record is created under `docs/` with changed files, test commands, results, device evidence, thresholds, and remaining limitations.

Until these gates pass, Phase 9 should be described as **software lifecycle implemented; acoustic acceptance pending**, not fully complete.
