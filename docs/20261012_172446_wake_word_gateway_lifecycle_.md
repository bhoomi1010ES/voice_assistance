# Wake word and voice gateway lifecycle — 2026-10-12 17:24 IST

## Task checklist

- [x] Inspect the existing microphone, wake detector, transport, router, TTS, and navigation lifecycle.
- [x] Start in local wake-word waiting mode without opening the voice gateway.
- [x] Activate one conversation on “Hey Mycroft” and preserve continuous follow-up turns.
- [x] Route “Stop listening” and “Stop now” as control commands; acknowledge without TTS.
- [x] Abort active work, close the voice gateway, and return to local wake-word capture.
- [x] Reconnect the gateway only after the next wake word.
- [x] Cover navigation, background, permission, stale event, and capture error paths.
- [x] Run focused tests and verify the launch, stop, and reactivation flow on the connected device.

## Lifecycle and implementation

The explicit states are `WAKE_WORD_WAITING`, `ACTIVATING`, `CONVERSATION_ACTIVE`, `STOPPING`, `ERROR_RECOVERY`, and `PAUSED`. The existing Android `AudioRecord` and wake detector remain the only capture pipeline. Wake-only PCM is held in a bounded local pre-roll queue and is not sent to the gateway. Normal VAD and conversation transmission begin after wake activation. An active conversation keeps its existing continuous turn behavior.

The app no longer opens the voice gateway from `MainNavigator` at launch. In wake waiting, the local detector can run with the gateway disconnected. A wake event starts a gateway connection and session, then begins a turn with pre-roll. Repeated wake events are ignored during activation. Control finalization retires the old correlation IDs, stops playback, aborts queued responses, closes the gateway, and re-arms the local detector. A later wake event opens a fresh connection. Navigation and app backgrounding pause capture; foreground return restores the prior mode. The visible wake waiting UI does not show a misleading Connect button or disconnected badge.

The backend keeps control commands on the existing router path. Its matcher now accepts direct, polite, and wake-prefixed stop phrases while keeping questions about stopping outside the control route. The control acknowledgement is text only and does not invoke TTS.

## Files edited

- `backend/app/routing/rules.py`, `backend/app/websocket/gateway.py`: control phrase matching and silent acknowledgement.
- `backend/tests/test_router_rules.py`, `backend/tests/test_llm_voice_gateway.py`: control phrase and no-TTS coverage.
- `frontend/src/voice/VoiceSocket.ts`, `frontend/src/native/VoiceModule.ts`: lifecycle transitions, local wake ownership, gateway disconnect/reconnect, stale callback guards, and native bridge.
- `frontend/src/navigation/MainNavigator.tsx`, `frontend/src/screens/AssistantScreen.tsx`: launch and waiting-state behavior.
- `frontend/android/app/src/main/java/com/voiceaipoc/audio/AudioEngine.kt`, `frontend/android/app/src/main/java/com/voiceaipoc/rn/VoiceModule.kt`, `frontend/android/app/src/main/java/com/voiceaipoc/voice/PcmSendQueue.kt`, `frontend/android/app/src/main/java/com/voiceaipoc/voice/VoiceWebSocketTransport.kt`: wake-only VAD/transmission gate and local pre-roll.
- `frontend/__tests__/wake-word-lifecycle.test.ts`, `frontend/__tests__/assistant-visibility.test.ts`, `frontend/__tests__/main-navigation-profile.test.tsx`, `frontend/android/app/src/test/java/com/voiceaipoc/voice/PcmSendQueueTest.kt`: lifecycle, navigation, and local PCM coverage.
- `.gitignore`: allow this required work record to be tracked under `docs/`.

## Checks

- Frontend: four focused Jest suites, **43 passed**.
- Backend: control gateway test and router rules, **129 passed**.
- Android: `PcmSendQueueTest` JVM suite passed. A debug APK was built and installed on connected device `9b0ea196`; ADB reverse for ports 8000 and 8081 was set.
- Physical device: cold launch displayed “Waiting for Hey Mycroft” without a Connect control; “Hey Mycroft” activated a new session; “Stop now.” was transcribed and acknowledged as “Stopped listening.” before returning to wake waiting; another “Hey Mycroft” reconnected and “What time is it?” received a normal answer. Device logs showed wake detection and a new session on the second activation.
- Local services: backend `/health` returned 200 and Metro `/status` reported running. Redis was restarted through the Ubuntu WSL service from Windows (process PID changed from 153 to 820), then Windows Redis `PING` succeeded. The backend was restarted after the router edit.
- TypeScript typecheck still reports three pre-existing errors: a missing `endSession` method in `voice-turn-completion.test.ts` and an undeclared `process` reference in `KnowledgeModeModal.tsx`. No new errors were reported.

## Limits

Wake capture is foreground-only and pauses when the app is backgrounded or the Assistant section is hidden. Wake-word accuracy and capture across long gateway reconnection delays were not measured. The release configuration still points to the existing placeholder production URL; the installed debug APK uses Metro and ADB reverse for local testing.
