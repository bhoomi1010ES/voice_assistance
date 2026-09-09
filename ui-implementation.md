# Voice AI Assistant — UI Implementation Plan

**Companion document:** `implementation.md`  
**Primary client:** React Native Android + TypeScript  
**Current system phase:** Phase 5 — LLM orchestration in progress  
**UI delivery model:** UI work runs in parallel with the existing system phases  
**Document purpose:** Define what user-facing UI must be built in each phase, how it integrates with the native Android and backend contracts, how it is tested, and what must pass before the UI for that phase is accepted.

---

## 1. Decision and current starting point

The main implementation plan is not backend-only. React Native is the product UI/application layer, while latency-sensitive microphone capture, AEC/NS, wake-word inference, VAD, PCM buffering, audio playback, and interruption detection remain in native Android code.

UI development technically began in Phase 0 through the diagnostic screen. However, the product UI was not given its own explicit phase-by-phase track. This document adds that track without renumbering or replacing the system phases in `implementation.md`.

At the current Phase 5 position, implement the complete UI foundation and the first usable assistant conversation experience. Do not wait for Phases 6–12. Later phases must extend the same screens, stores, components, and state machine rather than creating separate applications.

### Current UI priorities

1. Preserve the Phase 0 diagnostic controls, but keep them out of the normal production flow.
2. Complete the app shell, navigation, design system, authentication flow, and error boundaries.
3. Implement the assistant screen against the existing Phase 3 WebSocket, Phase 4 STT, and Phase 5 streamed-LLM events.
4. Make manual microphone start/stop the supported development path until the Phase 0 wake-word/background gate passes.
5. Show final-only STT honestly; never fabricate a live partial transcript when the configured remote STT endpoint does not provide one.
6. Add Memory, Tasks, Reminders, TTS, barge-in, privacy, and diagnostics UI only when their corresponding contracts are available.

### Phase status and UI consequence

| System phase | Current status | UI consequence |
|---|---|---|
| Phase 0 | Partially implemented; gate not passed | Retain manual microphone controls and diagnostic status. Do not present hands-free/background listening as reliable. |
| Phase 1 | Implemented; acceptance pending | UI project foundation and automated checks pass; large-font/accessibility physical evidence is still required. |
| Phase 2 | PASS | Login, secure session restoration, logout, and device/session UI can be completed now. |
| Phase 3 | Implemented; physical revalidation pending | The assistant UI uses the authenticated voice WebSocket and correlated session/turn/response IDs. |
| Phase 4 | Implemented; acceptance pending | Transcript UX can be built, but final UI acceptance needs the revised remote-STT physical test. |
| Phase 5 | Implemented; physical acceptance pending | Product shell, streaming conversation, cancellation, retry, and safe error handling are implemented; real Android STT-to-LLM evidence is still required. |
| Phase 6 | Implemented; UI acceptance pending | Backend memory persistence/retrieval foundations and the authenticated mobile memory controls are implemented, but provider, physical, privacy, accessibility, and performance acceptance are not complete. |
| Phases 7–12 | Not started | Add feature UI progressively behind capability checks or disabled navigation until each backend contract is ready. |

---

## 2. Product UI architecture

### 2.1 Responsibility boundary

| React Native / TypeScript owns | Native Android / Kotlin owns | Backend owns |
|---|---|---|
| Navigation and screens | `AudioRecord` microphone capture | Authentication and authorization |
| Design system and accessibility | AEC/NS attachment and status | Voice-session orchestration |
| Voice-state presentation | Wake-word and Silero VAD inference | STT, LLM, RAG, tools, and TTS services |
| Transcript and assistant messages | PCM framing and bounded buffering | Durable conversation/task/memory state |
| User controls and confirmations | Low-latency playback and local stop | Validation, idempotency, and audit |
| REST/WebSocket client state | Barge-in detection signal | Correlated server events and safe errors |
| Local non-secret preferences | Background/foreground audio lifecycle | Retention/deletion enforcement |

Rules:

- Do not move the real-time audio loop onto the JavaScript thread.
- Do not send raw PCM across the React Native bridge for UI rendering.
- Do not store server API keys in the mobile application.
- Do not infer a successful tool action from model text. Render success only after the server reports a durable tool result.
- Reject stale transcript, text, tool, and audio events whose `session_id`, `turn_id`, or `response_id` is no longer current.
- Keep server state authoritative. Optimistic UI is allowed only for reversible, non-destructive changes with rollback.

### 2.2 Recommended mobile source layout

```text
apps/mobile/src/
├── app/
│   ├── App.tsx
│   ├── AppProviders.tsx
│   ├── bootstrap.ts
│   └── featureFlags.ts
├── navigation/
│   ├── RootNavigator.tsx
│   ├── AuthNavigator.tsx
│   └── MainNavigator.tsx
├── screens/
│   ├── SplashScreen.tsx
│   ├── LoginScreen.tsx
│   ├── AssistantScreen.tsx
│   ├── ConversationHistoryScreen.tsx
│   ├── MemoryListScreen.tsx
│   ├── MemoryDetailScreen.tsx
│   ├── TaskListScreen.tsx
│   ├── TaskEditorScreen.tsx
│   ├── ReminderEditorScreen.tsx
│   ├── SettingsScreen.tsx
│   ├── PrivacyScreen.tsx
│   ├── DevicesScreen.tsx
│   └── DiagnosticsScreen.tsx
├── components/
│   ├── design-system/
│   ├── voice/
│   ├── conversation/
│   ├── memory/
│   ├── tasks/
│   ├── feedback/
│   └── forms/
├── stores/
│   ├── authStore.ts
│   ├── voiceStore.ts
│   ├── conversationStore.ts
│   ├── memoryStore.ts
│   ├── taskStore.ts
│   └── settingsStore.ts
├── api/
│   ├── httpClient.ts
│   ├── authApi.ts
│   ├── memoryApi.ts
│   ├── taskApi.ts
│   ├── reminderApi.ts
│   └── errors.ts
├── voice/
│   ├── VoiceManager.ts
│   ├── VoiceSocket.ts
│   ├── VoiceStateMachine.ts
│   ├── VoiceEventReducer.ts
│   ├── AudioPlayback.ts
│   └── types.ts
├── hooks/
├── utils/
└── test/
    ├── fixtures/
    ├── fakes/
    └── renderWithProviders.tsx
```

Adapt paths to the existing repository rather than moving stable code solely to match this example.

### 2.3 Navigation model

Use one root navigator with two guarded branches:

```text
App bootstrap
├── no valid session → Auth stack
│   └── Login
└── authenticated → Main app
    ├── Assistant
    ├── Memories          # enabled in Phase 6
    ├── Tasks             # enabled in Phase 7
    └── Settings
        ├── Voice
        ├── Privacy
        ├── Devices/sessions
        └── Diagnostics   # development/support access only
```

Prefer four or fewer primary navigation destinations. Conversation history may be opened from the Assistant screen instead of consuming a permanent bottom-tab position.

### 2.4 Shared visual states

Every feature screen must define and test these states where applicable:

- loading;
- first-use/empty;
- populated/success;
- refreshing;
- recoverable inline error;
- blocking error;
- unauthenticated/session expired;
- offline or network unavailable;
- permission denied;
- capability unavailable;
- destructive confirmation in progress;
- operation succeeded;
- operation failed without losing the user's input.

### 2.5 Voice UI state model

The UI must render from a single explicit voice state machine, not from unrelated booleans.

```text
idle
→ preparing
→ listening
→ speech_detected
→ transcribing
→ thinking
→ tool_running
→ responding_text
→ speaking
→ completed

Any active state → cancelling → idle/listening
Any active state → recoverable_error
Authentication failure → session_expired
```

Minimum state-to-UI mapping:

| State | Primary label | Main control | Visual behavior |
|---|---|---|---|
| `idle` | “Tap to speak” | Start microphone | Resting voice control |
| `preparing` | “Starting microphone…” | Cancel | Short indeterminate progress |
| `listening` | “Listening…” | Stop | Active microphone animation; no fabricated transcript |
| `speech_detected` | “Listening…” | Stop | Stronger speech activity indication |
| `transcribing` | “Transcribing…” | Cancel | Preserve captured-turn placeholder |
| `thinking` | “Thinking…” | Cancel response | Show final user transcript |
| `tool_running` | Action-specific safe label | Cancel when supported | Never claim success early |
| `responding_text` | Assistant response | Stop response | Append verified streamed text deltas |
| `speaking` | “Speaking…” | Stop playback | Text remains readable while audio plays |
| `cancelling` | “Stopping…” | Disabled briefly | Reject late events immediately |
| `recoverable_error` | Safe error copy | Retry | Preserve transcript/draft where possible |

### 2.6 Design-system baseline

Create reusable tokens and primitives before feature screens multiply:

- color roles for background, surface, text, accent, success, warning, error, and disabled states;
- typography scale with Android font-scaling support;
- spacing, radius, elevation, and motion-duration tokens;
- buttons, icon buttons, text inputs, cards, list rows, chips, banners, sheets, dialogs, snackbars, skeletons, and empty states;
- light and dark themes;
- minimum 48 dp interactive touch targets;
- visible focus and pressed states;
- content descriptions for meaningful controls and icons;
- reduced-motion behavior for voice animations;
- no meaning conveyed by color alone.

---

## 3. Test strategy used by every phase

### 3.1 Test layers

| Layer | Purpose | Suggested mechanism |
|---|---|---|
| Static checks | Type safety, lint, formatting, prohibited imports/secrets | TypeScript, ESLint, Prettier, secret scan |
| Unit | Reducers, state machines, validators, formatting, event filtering | Jest |
| Component | Rendering and user interaction across all states | React Native Testing Library |
| Contract | REST and WebSocket payload compatibility | Typed fixtures + fake HTTP/WebSocket/native modules |
| Integration | Stores, navigation, auth refresh, event ordering, cancellation | Jest/RNTL integration suites |
| End-to-end | Critical user journeys in a built application | Detox or Maestro; choose one and standardize |
| Accessibility | Labels, roles, touch targets, font scaling, screen-reader flow | Automated assertions + TalkBack physical check |
| Physical device | Audio, lifecycle, permissions, playback, barge-in, performance | Representative Android devices including RMX5070 |
| Visual regression | Stable high-value states and themes | Deterministic screenshot comparison |

### 3.2 Required test-fixture rules

- Version all REST and WebSocket fixtures.
- Include out-of-order, duplicate, malformed, oversized, cancelled, and stale events.
- Include a verified final-only STT fixture with `partial_count=0` and the appropriate reason.
- Never place access tokens, refresh tokens, API keys, private transcripts, or real personal memory in fixtures or screenshots.
- Use deterministic clocks for relative times, countdowns, and streaming tests.
- Use deterministic IDs for session/turn/response correlation tests.
- Test provider-neutral UI events; mobile UI must not branch on NVIDIA, OpenAI, Anthropic, Qwen, or another model vendor.

### 3.3 Definition of done for a UI phase

A UI phase is complete only when:

1. all required screens and states are implemented;
2. the UI uses the approved typed API/event contract;
3. loading, empty, error, offline, cancellation, and stale-event cases are covered;
4. unit, component, contract, and relevant end-to-end tests pass;
5. relevant physical-device checks pass;
6. accessibility checks pass for the new workflow;
7. secrets and private content are absent from logs, screenshots, and fixtures;
8. the phase-specific UI gate below passes.

---

# 4. Phase-by-phase UI implementation

## Phase 0 UI — Audio proof-of-concept and diagnostic experience

### Objective

Provide a developer/tester interface that proves microphone permission, native capture, wake-word, VAD, AEC/NS, buffering, and lifecycle behavior without pretending the full product voice experience is ready.

### UI scope

- Diagnostic screen, clearly marked as non-production.
- Microphone permission explanation and system-permission handoff.
- Manual Start Microphone and Stop Microphone controls.
- Audio format/status display: sample rate, channel count, encoding, active/inactive.
- AEC and NS availability, attachment, enabled state, and independent toggle controls for testing.
- Wake-word engine loaded/error state and detection counter.
- Silero VAD loaded/error state and speech start/end indicators.
- PCM frame, ring-buffer overflow/drop, inference-drop, and processing-error counters.
- Calibration/test-mode controls for quiet, distance, TV/music, noise, different voices, and speaker playback.
- Screen-off/background test instructions and a visible warning while not supported.
- Export/share sanitized diagnostic evidence only if the existing app already supports a safe evidence path.

### Implementation steps

1. Wrap native events in typed TypeScript definitions and one adapter.
2. Drive the diagnostic screen through a reducer/store; do not mutate UI state directly from callbacks.
3. Implement permission states: unknown, requesting, granted, denied, and permanently denied.
4. Add manual start/stop as the default development activation path.
5. Display semantic native events without forwarding raw audio to React Native.
6. Add independent AEC-only, NS-only, both-enabled, and both-disabled test controls.
7. Add bounded counters and reset-session behavior.
8. Hide engineering details from release builds unless an explicit diagnostics flag is enabled.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P0-01 | First launch with microphone permission undecided | Explanation is shown before the Android permission prompt. |
| UI-P0-02 | Permission granted | Start control becomes available; capture status can become active. |
| UI-P0-03 | Permission denied/permanently denied | No capture starts; UI offers retry or Settings as appropriate. |
| UI-P0-04 | Rapid Start/Stop taps | Only one native capture session exists; controls remain consistent. |
| UI-P0-05 | Native engine-load failure | Safe component-specific error appears; app does not crash. |
| UI-P0-06 | VAD speech start/end events | Indicator follows event order and resets after the turn. |
| UI-P0-07 | Wake detection event | Counter increments once for one event; no duplicate UI transition. |
| UI-P0-08 | Ring-buffer overflow/drop event | Counter and warning update without rendering raw PCM. |
| UI-P0-09 | App backgrounds during capture | UI reflects actual native state after foreground return. |
| UI-P0-10 | Release build without diagnostics flag | Engineering screen and sensitive details are inaccessible. |
| UI-P0-11 | TalkBack and large font | Controls remain labelled, reachable, and unclipped. |

### UI gate

Manual capture can be started/stopped safely, all native engine states are represented accurately, no raw PCM crosses the bridge for display, and the UI never presents wake-word/background behavior as production-ready while Phase 0 remains unaccepted.

---

## Phase 1 UI — Frontend engineering foundation

### Objective

Create the stable application shell and development standards required by all later screens.

### UI scope

- App bootstrap and provider composition.
- Root, Auth, and Main navigation shells.
- Design tokens and reusable primitives.
- Global error boundary and non-sensitive fallback screen.
- Environment-aware API configuration without secrets.
- Typed HTTP/WebSocket error model.
- Theme selection with system default.
- Localization-ready string organization, initially English.
- Automated UI test harness and CI checks.

### Implementation steps

1. Audit the existing React Native structure and preserve working native integration.
2. Add navigation with authenticated and unauthenticated route guards.
3. Build design tokens and primitives before styling feature screens.
4. Add app bootstrap states: initializing, ready, recoverable failure, and mandatory-upgrade placeholder if later required.
5. Centralize public API base URL configuration; never bundle STT/LLM/TTS service keys.
6. Create normalized client error types and safe user-facing messages.
7. Add test providers, fake time, fake network, fake secure storage, fake native voice module, and fake WebSocket.
8. Run TypeScript, lint, format, unit, and component checks in CI.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P1-01 | Cold bootstrap succeeds | Correct initial route renders without flashing a protected screen. |
| UI-P1-02 | Bootstrap dependency fails | Safe retry UI appears; no stack trace or secret is shown. |
| UI-P1-03 | Theme follows system | Colors update and remain readable in light/dark modes. |
| UI-P1-04 | Font scale at Android maximum supported test value | Core controls and text do not overlap or become unreachable. |
| UI-P1-05 | Error boundary catches a render failure | Fallback is usable and records only sanitized diagnostics. |
| UI-P1-06 | Production bundle inspection | No backend provider key or private service URL credential is present. |
| UI-P1-07 | CI on clean checkout | Static and UI unit/component suites execute predictably. |

### UI gate

A clean checkout can build and test the React Native UI; the app has consistent navigation, theming, error handling, accessibility primitives, and no embedded server credentials.

---

## Phase 2 UI — Authentication, account, and device sessions

### Objective

Provide a secure, understandable sign-in lifecycle using the accepted authentication and device foundation.

### UI scope

- Splash/session-restoration screen.
- Login screen.
- Field validation and password visibility control.
- Loading, invalid-credentials, rate-limit, offline, and service-unavailable states.
- Secure access/refresh token lifecycle through the existing secure-storage layer.
- Logout action.
- Basic profile/account summary from `GET /api/v1/me`.
- Devices/sessions screen if the existing backend exposes list/revoke operations; otherwise keep this sub-screen deferred and do not fabricate data.
- Session-expired dialog that preserves safe unsent local input where possible.

### Implementation steps

1. Implement an auth store with explicit `unknown`, `authenticated`, and `unauthenticated` states.
2. Restore tokens from secure storage before choosing the initial navigator.
3. Integrate login and normalized auth errors.
4. Implement a single-flight refresh mechanism so concurrent 401 responses do not rotate the refresh token multiple times.
5. Retry the original safe request once after successful refresh.
6. Clear local sensitive state on logout or terminal refresh failure.
7. Close the voice WebSocket and stop capture/playback before logout completes.
8. Add account and device/session controls only against implemented, ownership-scoped APIs.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P2-01 | Valid login | Tokens are stored through secure storage and Main navigation opens. |
| UI-P2-02 | Invalid credentials | Inline safe error appears; password is not logged or persisted. |
| UI-P2-03 | App relaunch with valid refresh session | Session restores without displaying Login. |
| UI-P2-04 | Expired access token | One refresh occurs and the original request resumes. |
| UI-P2-05 | Several requests receive 401 together | Only one refresh request runs; waiting requests share the result. |
| UI-P2-06 | Refresh token rejected | Secure tokens are cleared and Login is shown once. |
| UI-P2-07 | Logout during active voice turn | Capture/socket/playback stop; protected stores clear. |
| UI-P2-08 | User A signs out and User B signs in | No conversation, memory, task, or device state from A is visible. |
| UI-P2-09 | Offline login | Recoverable network message is shown without claiming credentials are wrong. |
| UI-P2-10 | Back navigation after logout | Protected screens cannot be reopened. |

### UI gate

Authentication survives restart and token refresh, logout clears protected state and active voice resources, and no user can see cached UI data belonging to a previous account.

---

## Phase 3 UI — Voice connection and session lifecycle

### Objective

Expose an understandable, resilient UI over the authenticated `/v1/voice` WebSocket and its session/turn/response correlation.

### UI scope

- Assistant screen connection states: disconnected, connecting, connected, reconnecting, degraded, and failed.
- Compact network/status banner; avoid constant technical noise during healthy operation.
- Manual voice-session start and end controls.
- Current turn state driven by server/native events.
- Retry/reconnect action.
- Clear stale-session recovery.
- Development diagnostics for session, turn, response, sequence, heartbeat, and reconnect status, with IDs shortened/redacted in UI.

### Implementation steps

1. Implement one lifecycle-owned `VoiceSocket`; screens subscribe but do not independently create sockets.
2. Authenticate only using the approved connection contract.
3. Normalize WebSocket control events into typed internal events.
4. Track the active `session_id`, `turn_id`, `response_id`, and event sequence.
5. Ignore duplicate, out-of-order where disallowed, and stale events.
6. Add bounded reconnect with visible state and no reconnect storm.
7. Reconcile actual socket/native state after background/foreground transitions.
8. Clear session UI only when the session is terminal or the user explicitly starts over.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P3-01 | Authenticated socket connects | Healthy assistant state appears once. |
| UI-P3-02 | Connection attempt times out | Recoverable error and Retry appear. |
| UI-P3-03 | Heartbeat missed | UI enters reconnecting/degraded state and does not accept a ghost turn. |
| UI-P3-04 | Socket reconnects | A new valid connection is used; stale response events remain rejected. |
| UI-P3-05 | Duplicate control event | State changes at most once. |
| UI-P3-06 | Event for old `response_id` | Event is ignored and current response remains unchanged. |
| UI-P3-07 | Ten consecutive manual turns | UI returns to a valid ready state after every turn. |
| UI-P3-08 | App background/foreground | Connection/state reconcile without duplicate session creation. |
| UI-P3-09 | Oversized/malformed server event fixture | Event is rejected safely; app does not crash or render raw payload. |
| UI-P3-10 | Logout while reconnecting | Reconnect stops and auth flow opens. |

### UI gate

Ten consecutive voice turns, reconnect, cancellation, background/foreground, and stale-event scenarios leave the UI in the correct state with only one active connection and no stale response rendered.

---

## Phase 4 UI — Speech-to-text and transcript experience

### Objective

Show what the user said and the STT lifecycle accurately, including the configured remote endpoint's final-only behavior.

### UI scope

- Listening and speech-detected states.
- Transcribing placeholder after commit/VAD end.
- Partial transcript region only when a genuine provider partial event exists.
- Final user transcript message.
- Editable text retry path may be considered only if supported by product policy; it must create a new request rather than silently rewriting audit history.
- STT unavailable, timeout, authentication/configuration, rate-limit, network, empty-audio, too-long, and cancellation errors mapped to safe messages.
- Retry action that reuses the persisted final transcript when available, but never silently resends recorded audio.
- Transcript accessibility announcements that do not read every high-frequency partial delta.

### Implementation steps

1. Add canonical transcript event types independent of the STT provider.
2. Preserve one in-progress user message per `turn_id`.
3. Replace that placeholder with the final transcript atomically.
4. Render partials only from genuine partial events; for final-only remote STT, remain in “Transcribing…” until final.
5. Persist/render final transcript before Phase 5 LLM processing begins.
6. Map stable backend error codes to short user copy and optional details for diagnostics.
7. Preserve the turn for retry where the backend contract allows it.
8. Measure UI-observed speech-end-to-final and compare it with server evidence without treating UI wall-clock time as authoritative server latency.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P4-01 | Genuine partial events then final | Partial region updates in order and final replaces it once. |
| UI-P4-02 | Verified final-only endpoint | No fake partial text appears; “Transcribing…” remains until final. |
| UI-P4-03 | Blank/missing transcript | No LLM request is presented; actionable STT error appears. |
| UI-P4-04 | Late final after cancellation | Final is ignored and does not enter conversation history. |
| UI-P4-05 | STT timeout/network failure | Captured-turn error appears with safe retry guidance. |
| UI-P4-06 | Rate limited | UI distinguishes temporary throttling from bad microphone input. |
| UI-P4-07 | Very long utterance rejected | Limit message appears; app returns to ready state. |
| UI-P4-08 | Ten fixed English sentences on RMX5070 | Each turn shows the correct final and returns to ready without stuck UI. |
| UI-P4-09 | Screen reader during partial streaming | Announcements are throttled; final is announced clearly. |
| UI-P4-10 | Transcript containing long/unbroken text | Message wraps and scrolls without breaking layout. |

### UI gate

The revised Phase 4 ten-turn physical run produces ten correctly correlated final transcript messages, no fabricated partials, no post-cancel final, recoverable error states, and no stuck listening/transcribing state.

---

## Phase 5 UI — Product shell and streaming assistant conversation

**Current UI implementation milestone**

### Objective

Deliver the first genuinely usable assistant experience: authenticated user speech becomes a final transcript, Phase 5 streams safe assistant text, and cancellation/retry/error behavior is understandable. The UI remains provider-neutral.

### Screens and components

- `AssistantScreen` as the default authenticated destination.
- Top app bar with assistant status and conversation-history entry point.
- Scrollable message list with user, assistant, system/status, and tool-status rows.
- Voice control/orb with explicit state label and accessible button behavior.
- Optional text composer for development/accessibility fallback, if allowed by the product requirements.
- Streaming assistant message component.
- Stop response, Retry, Copy, and Start new conversation actions.
- Connection/degraded-service banner.
- Empty/first-use state with a short privacy-safe example.
- Conversation-history shell populated only if an implemented API provides durable history; otherwise keep it local-session-only and label it accurately.
- Tool-call pending/confirmation/result components designed now but enabled only after the server-owned registry and tool loop are accepted.

### Interaction rules

- One user turn maps to one visible user message keyed by `turn_id`.
- One assistant attempt maps to one visible response keyed by `response_id`.
- Append text only from validated `text_delta` events for the current response.
- Batch high-frequency deltas per animation frame or short interval to avoid excessive renders.
- Never display provider reasoning traces or private chain-of-thought.
- Never expose provider names, model IDs, request IDs, or raw errors in normal user copy.
- Keep the final user transcript visible when LLM generation fails.
- Do not mark a tool action successful until a correlated durable server result arrives.
- Cancellation changes local acceptance state immediately; late deltas must be dropped even before the network cancellation completes.
- Switching LLM provider/model is an operator configuration change and must not appear as a normal mobile setting.

### Implementation steps

1. Complete the Phase 1 app shell and Phase 2 auth guard if not already finished.
2. Define canonical mobile `VoiceEvent` and `ConversationMessage` unions.
3. Implement `VoiceEventReducer` with generation/session/turn/response validation.
4. Build the message list with stable keys, bounded rendering, auto-scroll, and user-controlled scroll preservation.
5. Build the accessible voice control from the explicit state machine.
6. Integrate final STT messages from Phase 4.
7. Integrate `request_started`, `text_delta`, `response_completed`, `response_failed`, usage-ignored-for-UI, and cancellation events.
8. Add Stop response and ensure local stale-delta rejection happens immediately.
9. Add Retry using the preserved final transcript and a new `response_id`; never reuse a cancelled response identity.
10. Implement safe error mapping for configuration, authentication, permission, model-not-found, rate-limit, timeout, overloaded, provider, protocol, cancelled, and context-limit errors.
11. Add conversation reset with confirmation when it would remove unsaved local-session content.
12. Add skeleton tool states but guard them behind server capability and feature flags.
13. Add performance markers for first visible text and render completion without logging message content.
14. Validate the full STT-final → LLM-first-text → LLM-final flow on the physical device.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P5-01 | First authenticated launch | Assistant screen shows a useful empty state and ready voice control. |
| UI-P5-02 | STT final starts LLM | User transcript is committed before assistant streaming begins. |
| UI-P5-03 | Hundreds of fragmented text deltas | Text is ordered, batched, complete, and does not create hundreds of message rows. |
| UI-P5-04 | Duplicate text delta/event sequence | Duplicate content is not displayed. |
| UI-P5-05 | Delta for previous `response_id` | Stale text is ignored. |
| UI-P5-06 | User stops during streaming | UI stops accepting deltas immediately and reports a cancelled/incomplete response accurately. |
| UI-P5-07 | Server sends late completion after cancel | Cancelled response is not changed to completed. |
| UI-P5-08 | LLM timeout/outage | Final user transcript remains; assistant row offers Retry. |
| UI-P5-09 | Retry after LLM failure | A new response identity is used; user transcript is not duplicated. |
| UI-P5-10 | Context-limit error | Clear recoverable guidance appears; raw provider error is hidden. |
| UI-P5-11 | Provider/model changes behind backend | UI behavior and event handling remain unchanged. |
| UI-P5-12 | Tool-like text without confirmed tool event | No success badge or completed-action UI appears. |
| UI-P5-13 | Confirmed read-only diagnostic tool round trip | Pending → completed state follows correlated server events. |
| UI-P5-14 | Malformed/unauthorized tool event fixture | No actionable UI or success state appears. |
| UI-P5-15 | Long conversation | List remains responsive and scroll position behaves predictably. |
| UI-P5-16 | TalkBack during streaming | New response is announced without reading every token/delta. |
| UI-P5-17 | Rotate device/app lifecycle transition | Current committed conversation survives; no duplicate request starts. |
| UI-P5-18 | Physical RMX5070 end-to-end turn | Final transcript and complete streamed response render with measured first-text timing. |

### UI gate

Phase 5 UI passes when the configured provider's physical STT-final-to-LLM path renders ordered streamed text, cancellation rejects all stale deltas, retries preserve the user's request without duplication, error messages are safe, and malformed or unauthorized tool activity can never appear successful.

### Explicitly deferred from Phase 5 UI

- Production memory search/edit/delete screens: Phase 6.
- Production task/reminder creation and completion: Phase 7.
- Assistant audio playback and voice selection: Phase 8.
- Full barge-in interaction: Phase 9.
- Complete retention/account deletion workflow: Phase 10.
- User/support diagnostics backed by production telemetry: Phase 11.

---

## Phase 6 UI — Long-term memory and hybrid RAG controls

### Objective

Make personal memory useful, inspectable, correctable, and deletable. The UI must communicate when an answer used memory and must never expose another user's data.

### UI scope

- Memories navigation destination.
- Search and filter by type/date where supported.
- Memory list with concise content, type, occurrence time, and updated time.
- Memory detail with source/provenance summary when policy allows.
- Create, correct/edit, delete, and Forget actions.
- Long-term memory master toggle.
- Delete all personal memory workflow with strong confirmation.
- Exclude conversation from memory control.
- Superseded/conflicting memory presentation.
- Empty, no-result, retrieval-degraded, and permission-error states.
- Assistant response indicator such as “Used memory” only when confirmed by server metadata.

### Implementation steps

1. Integrate the ownership-scoped memory APIs.
2. Build paginated memory list and deterministic refresh behavior.
3. Add debounced search with cancellation and stale-result rejection.
4. Build memory detail/edit forms from server schemas.
5. Require confirmation for delete and stronger typed/step confirmation for delete-all.
6. Implement memory-disabled state across Assistant and Memories screens.
7. Add conflict/supersession labels without presenting two facts as simultaneously current.
8. Refresh or invalidate relevant memory queries after every mutation.
9. Clear memory state on account switch/logout.
10. Add privacy copy describing what is and is not remembered; do not promise retention behavior beyond implemented policy.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P6-01 | First use with no memories | Clear empty state and memory-control link appear. |
| UI-P6-02 | Paginated list | Items do not duplicate or disappear during refresh. |
| UI-P6-03 | Rapid search query changes | Only latest query result renders. |
| UI-P6-04 | Exact name and semantic paraphrase searches | Returned records render using one consistent model. |
| UI-P6-05 | Edit/correct a memory | Updated record renders and stale detail cache is invalidated. |
| UI-P6-06 | Delete one memory | Confirmation is required; deleted item does not reappear after refresh. |
| UI-P6-07 | Delete all cancelled | No mutation occurs. |
| UI-P6-08 | Delete all confirmed and succeeds | List clears only after server confirmation. |
| UI-P6-09 | Memory disabled | New memory claims are not shown; UI accurately reflects disabled state. |
| UI-P6-10 | Direct memory question during service outage | UI states memory is unavailable and does not display an invented result. |
| UI-P6-11 | Cross-user fixture/cache switch | No data from the previous user remains. |
| UI-P6-12 | Superseded/conflicting record | Current versus historical status is clear and accessible. |

### UI gate

Users can view, search, correct, delete, disable, and delete all memory through ownership-scoped APIs; deleted or cross-user memories never reappear; degraded retrieval is communicated without invented answers.

---

## Phase 7 UI — Tasks, reminders, and confirmed tool actions

### Objective

Provide dependable task/reminder management and trustworthy action feedback for tool calls.

### UI scope

- Tasks navigation destination.
- Upcoming, all, and completed views.
- Task create/edit/detail/delete/complete flows.
- Reminder create/edit/delete flows.
- Due date/time and explicit timezone display.
- One-shot reminders first; recurrence controls enabled only after recurrence backend acceptance.
- Push-notification permission onboarding and disabled state.
- Tool confirmation sheet for actions that require user confirmation.
- Tool status rows: awaiting confirmation, running, succeeded, failed, cancelled.
- Retry guidance for delivery/tool failures without duplicating side effects.
- Optional audit/detail view showing safe action time/status, not private internal payloads.

### Implementation steps

1. Integrate task and reminder CRUD APIs with pagination/filtering.
2. Build accessible date/time selection and always resolve relative dates to an explicit preview.
3. Display the user's configured timezone beside scheduled time.
4. Ask for clarification when server/tool state reports an ambiguous time; do not guess in the client.
5. Implement confirmation UI driven by server-owned tool metadata.
6. Send only the confirmation decision and correlated tool-call identity; never accept or construct tool schemas from model text.
7. Render success only after durable committed result.
8. Make repeated taps idempotent in the UI while backend idempotency remains authoritative.
9. Add push permission flow after the task/reminder is durable, with clear fallback if permission is denied.
10. Enable recurrence UI only after one-shot and recurrence backend gates pass.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P7-01 | Create one-shot reminder | Explicit local date/time/timezone preview is shown before submission. |
| UI-P7-02 | Relative request “tomorrow” | Resolved absolute time is displayed for confirmation. |
| UI-P7-03 | Ambiguous time | Clarification state appears; no reminder is shown as created. |
| UI-P7-04 | Double-tap Create/Complete | One operation is sent or safely correlated; one resulting item exists. |
| UI-P7-05 | Replayed tool result | UI shows one action result and one task/reminder. |
| UI-P7-06 | Unauthorized/malformed tool call | No confirmation or success UI is produced. |
| UI-P7-07 | User denies confirmation | Action is cancelled and no success language appears. |
| UI-P7-08 | Tool execution fails | Failure is honest; retry does not claim the prior action succeeded. |
| UI-P7-09 | App/server restarts after scheduling | Reminder remains visible after reload. |
| UI-P7-10 | Push permission denied | Durable reminder remains; UI explains notification limitation. |
| UI-P7-11 | Timezone changes | Existing schedule is displayed according to product policy without silent mutation. |
| UI-P7-12 | Recurrence capability disabled | Recurrence controls are absent/disabled, not simulated locally. |

### UI gate

Task/reminder CRUD and tool confirmation flows are accessible and timezone-explicit, duplicate interactions cannot create duplicate visible actions, and the UI never reports success before durable server confirmation.

---

## Phase 8 UI — Text-to-speech playback

### Objective

Add low-latency assistant speech while keeping text usable and making playback state and cancellation clear.

### UI scope

- Speaking state on Assistant screen.
- Immediate Stop playback control.
- Text caption remains visible and selectable/copyable.
- Mute/voice-output preference.
- Voice selection and sample playback only for server-supported voices.
- Playback buffering/degraded indicator shown only when delay is noticeable.
- TTS failure fallback to completed text response.
- Audio-route/interruption handling where exposed by the native contract.

### Implementation steps

1. Add binary TTS chunk handling outside the generic JSON event parser.
2. Queue only chunks matching the current `response_id`.
3. Keep a small bounded native jitter buffer and surface semantic playback state to React Native.
4. Start speaking UI on actual playback start, not when TTS was merely requested.
5. Stop local playback first when the user presses Stop.
6. Clear queued chunks on cancel, response supersession, logout, or terminal playback error.
7. Persist only non-sensitive voice/output preferences.
8. Ensure unconfirmed tool statements are never queued for speech.
9. Fall back to text without marking the reasoning/tool result failed when TTS alone fails.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P8-01 | First audio chunk arrives | Speaking state begins only when playback actually starts. |
| UI-P8-02 | Multiple chunks for current response | Audio plays in order without UI state flicker. |
| UI-P8-03 | Chunk for stale response | Chunk is discarded and never played. |
| UI-P8-04 | Stop tapped during speech | Local audio and queued chunks stop immediately. |
| UI-P8-05 | TTS service fails after text completes | Full text remains; a non-blocking voice-output error appears. |
| UI-P8-06 | Unconfirmed tool language received | No corresponding audio is played. |
| UI-P8-07 | Voice preference changes | Only supported voice is stored and used on the next response. |
| UI-P8-08 | Headphones/audio focus changes | Native and UI states reconcile without overlapping playback. |
| UI-P8-09 | TalkBack active while TTS plays | Controls remain operable and announcements do not fight playback excessively. |

### UI gate

Assistant speech starts from correctly correlated chunks, continues smoothly, stale audio never plays, Stop clears local playback promptly, and a TTS failure leaves the completed text response intact.

---

## Phase 9 UI — Full barge-in and interruption experience

### Objective

Make interruption feel immediate and unambiguous while preventing assistant playback from repeatedly triggering false user-speech transitions.

### UI scope

- During speaking, the voice control remains available and microphone state is truthful.
- On real user speech: speaking animation stops immediately and listening animation begins.
- Interrupted assistant message is labelled incomplete/interrupted where appropriate.
- New user turn is visually separated from the cancelled response.
- No stale text or audio from the previous response can reappear.

### Implementation steps

1. Consume the semantic native `barge_in.detected` event.
2. Stop local playback before waiting for server acknowledgement.
3. Mark the old response locally non-accepting and send `client.response.cancel`.
4. Create a new turn identity before accepting new STT events.
5. Transition Speaking → Cancelling/Listening without returning through an incorrect Idle state.
6. Drop all later text/tool/audio events for the cancelled response.
7. Reconcile false-trigger and no-speech cases according to the native/backend contract.
8. Test across speaker volume, noise, distance, packet delay, and deliberately slow TTS.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P9-01 | User interrupts assistant speech | Playback stops and listening UI begins immediately. |
| UI-P9-02 | Old text/audio arrives after barge-in | Nothing stale is rendered or played. |
| UI-P9-03 | New utterance completes | New transcript/response attaches only to the new turn. |
| UI-P9-04 | Assistant playback with no user speech | UI remains speaking; no false listening loop occurs. |
| UI-P9-05 | High speaker volume matrix | False barge-in rate remains within the accepted system gate. |
| UI-P9-06 | Repeated rapid interruption | State machine never has two current responses. |
| UI-P9-07 | Network delay during cancel | Local stop is immediate and server-late events remain rejected. |
| UI-P9-08 | Physical acceptance phrase scenario | “No, what about Friday?” becomes the new turn and old response A never resumes. |

### UI gate

Physical-device barge-in tests show immediate local stop, correct Speaking → Listening transition, exactly one new turn, and zero visible/audible stale output from the cancelled response.

---

## Phase 10 UI — Security, privacy, permissions, and data controls

### Objective

Expose the privacy and security behavior required for a personal assistant in clear user controls rather than burying it in backend policy.

### UI scope

- Privacy overview explaining microphone, transcript, and long-term-memory handling.
- Microphone and notification permission status with system Settings links.
- Long-term memory toggle and memory-management link.
- Transcript/audio retention settings only where backed by implemented policy.
- Exclude conversation from memory.
- Device/session list and revoke controls when API-supported.
- Delete personal memory and delete account/data workflows.
- Confirmation/re-authentication for destructive or security-sensitive actions.
- Security-safe error copy and session-expiration handling.
- App version, policy version/date, and support path.

### Implementation steps

1. Map each visible privacy statement to enforced backend/native behavior.
2. Add just-in-time permission education before Android system prompts.
3. Build retention controls from server-provided allowed values; do not hard-code unsupported guarantees.
4. Require re-authentication or the approved strong confirmation for high-risk deletion/revocation.
5. Display server-confirmed completion for deletion and revocation.
6. Clear corresponding local caches immediately after confirmed deletion.
7. Ensure screenshots, analytics, logs, and diagnostics exclude private content by default.
8. Add privacy/accessibility review to release checks.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P10-01 | Microphone permission denied | Voice cannot start; Settings path and explanation are available. |
| UI-P10-02 | Memory toggle disabled | UI and subsequent server-backed behavior remain consistent after restart. |
| UI-P10-03 | Exclude conversation | Confirmation is reflected and the control persists after reload. |
| UI-P10-04 | Delete memory/account cancelled | No destructive request is sent. |
| UI-P10-05 | Delete confirmed but server fails | UI does not clear/show success prematurely; safe retry is available. |
| UI-P10-06 | Delete confirmed and succeeds | Relevant local caches and protected navigation state clear. |
| UI-P10-07 | Revoke current device/session | Voice and protected screens close and authentication is required. |
| UI-P10-08 | Screenshot/log/analytics inspection | No token, key, raw audio, private transcript, or hidden reasoning is captured. |
| UI-P10-09 | Cross-user navigation/cache test | User ownership isolation remains intact across every screen. |
| UI-P10-10 | TalkBack destructive dialog | Action, consequence, cancel, and confirm controls are announced clearly. |

### UI gate

Every user-facing privacy statement matches implemented behavior; permissions and retention are inspectable; destructive actions require confirmation and only show success after server completion; local/private data is removed or invalidated consistently.

---

## Phase 11 UI — User diagnostics, supportability, and perceived performance

### Objective

Make failures diagnosable without exposing private conversation content, and ensure the UI itself does not become the source of voice-loop latency.

### UI scope

- Development/support diagnostics screen behind an explicit gate.
- Current connectivity and service availability summary using safe readiness information.
- Sanitized recent failure codes and timestamps.
- Optional “Copy diagnostic summary” containing app/device/version/correlation suffixes but no transcript, token, key, prompt, or tool arguments.
- User-facing slow/degraded connection banner based on stable thresholds.
- Internal capacity dashboard is an operations web surface, not part of the normal mobile UI.

### Implementation steps

1. Add mobile performance marks for voice-state transition, first transcript render, first assistant-text render, and first playback-state render.
2. Correlate safe client measurements with `session_id`/`turn_id`/`response_id` without logging content.
3. Measure render count and dropped-frame/jank behavior during text streaming.
4. Add sanitized diagnostics export with an allow-list of fields.
5. Add error-report action only if a privacy-reviewed destination exists.
6. Define P50/P95/P99 dashboards for product latency; do not show raw engineering percentiles to normal users.
7. Add alerts/tests for excessive rerenders, unbounded message growth, and leaked event listeners.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P11-01 | Long streamed response | UI remains responsive and delta batching limits rerenders. |
| UI-P11-02 | Repeated sessions | No accumulating listeners, timers, sockets, or playback objects. |
| UI-P11-03 | Copy diagnostic summary | Only allow-listed non-sensitive fields are present. |
| UI-P11-04 | Provider raw error contains secret/private text | Normal and diagnostic UI redact it. |
| UI-P11-05 | Slow-network threshold crossed | One useful degraded banner appears without notification spam. |
| UI-P11-06 | Healthy recovery | Degraded banner clears and state returns to ready. |
| UI-P11-07 | Performance trace comparison | Client marks can be correlated to server stages without conflicting clock assumptions. |
| UI-P11-08 | Release build | Developer-only verbose panels remain inaccessible unless explicitly enabled. |

### UI gate

Every slow or failed turn can be correlated safely, the mobile UI remains responsive during sustained streaming, diagnostics contain no sensitive content, and repeated use does not leak listeners, sockets, buffers, or screens.

---

## Phase 12 UI — Resilience, accessibility, onboarding, and production release

### Objective

Finish the product experience across failure, lifecycle, device, accessibility, and release scenarios.

### UI scope

- First-run onboarding for microphone, notification, memory, and voice behavior.
- Production-quality empty, offline, degraded, maintenance, and retry states.
- Background/foreground reconciliation.
- Network-change and reconnect experience.
- Crash/restart recovery for committed local UI state.
- Accessibility and supported font-scale completion.
- Dark/light theme completion.
- Localization architecture completion; add languages only with corresponding STT/TTS/product acceptance.
- Store-ready privacy links, version metadata, and release configuration.

### Implementation steps

1. Define the minimal first-run onboarding; ask permissions just in time, not all at once.
2. Add feature-discovery copy for manual voice activation while wake/background remains gated.
3. Implement safe restoration of committed conversation state after process death; never resume a side effect by guessing.
4. Reconcile microphone, socket, active response, and playback after background/foreground and network changes.
5. Add compatibility handling for required backend/client protocol versions.
6. Run accessibility, localization-readiness, visual regression, and device-matrix validation.
7. Run end-to-end resilience cases aligned with Phase 12 backend failures.
8. Produce a release checklist and evidence bundle with all secrets/private content removed.

### Test cases

| ID | Test | Expected result |
|---|---|---|
| UI-P12-01 | Clean install onboarding | Purpose is clear; permissions are requested only when needed. |
| UI-P12-02 | Network switches Wi-Fi ↔ mobile | UI reconnects or offers recovery without duplicating a turn. |
| UI-P12-03 | STT outage | Transcription unavailable state appears; no LLM request is shown. |
| UI-P12-04 | LLM outage | Final transcript remains and Retry is available. |
| UI-P12-05 | TTS outage | Text remains usable and completed action/result is preserved. |
| UI-P12-06 | Redis/API restart | UI reconnects safely and rejects stale session events. |
| UI-P12-07 | Reminder worker restart | Durable reminders still appear correctly after refresh. |
| UI-P12-08 | App background/foreground during each voice state | Actual native/server state is reconciled without duplicate work. |
| UI-P12-09 | Process death during tool action | UI reloads authoritative status; it does not replay the action automatically. |
| UI-P12-10 | Long silence and very long utterance | Limits and recovery states are clear; UI never remains permanently busy. |
| UI-P12-11 | Malformed/unauthorized events | Safe error/ignore behavior; no protected content renders. |
| UI-P12-12 | Maximum font scale + TalkBack | Every critical flow remains usable. |
| UI-P12-13 | Light/dark screenshot suite | High-value states meet contrast and layout requirements. |
| UI-P12-14 | Thirty-minute repeated-turn soak | No unbounded memory/listener growth or progressive UI slowdown. |
| UI-P12-15 | Signed release APK on representative devices | Login, voice, memory, tasks, TTS, interruption, and logout flows pass as enabled. |

### UI release gate

The signed production build passes the supported device matrix, accessibility checks, network/service failure matrix, lifecycle tests, repeated-turn soak, privacy review, and all enabled feature gates without stale output, duplicate side effects, cross-user state, or embedded secrets.

---

# 5. Screen acceptance specifications

## 5.1 Assistant screen

Required before Phase 5 UI acceptance:

- authenticated access only;
- truthful microphone/connection/voice state;
- manual start/stop;
- final user transcript;
- streamed assistant text;
- cancel and retry;
- safe error states;
- stale-event rejection;
- accessible labels and controlled announcements;
- no provider/model configuration controls;
- no unconfirmed tool success.

Added later:

- memory-use indicator in Phase 6;
- confirmed tool actions in Phase 7;
- audio playback in Phase 8;
- barge-in transition in Phase 9;
- privacy/retention controls in Phase 10.

## 5.2 Memories screens

Required in Phase 6:

- list/search/filter;
- detail and source summary where allowed;
- create/correct/delete;
- Forget and delete-all;
- memory enabled/disabled state;
- conflicts/supersession;
- empty/no-result/degraded states;
- ownership-safe cache clearing.

## 5.3 Tasks and reminders screens

Required in Phase 7:

- upcoming/all/completed views;
- create/edit/delete/complete;
- explicit date/time/timezone preview;
- confirmation and durable success;
- push permission state;
- recurrence hidden until supported;
- idempotent interaction behavior.

## 5.4 Settings screens

Build progressively:

| Setting group | First required phase |
|---|---|
| Theme and accessibility preferences | Phase 1 |
| Account/logout | Phase 2 |
| Connection and safe diagnostics link | Phase 3 |
| Voice activation explanation | Phase 4/5 |
| Memory controls | Phase 6 |
| Task notification/timezone preferences | Phase 7 |
| Voice/TTS selection and mute | Phase 8 |
| Privacy, retention, devices, data deletion | Phase 10 |
| Support diagnostics | Phase 11 |

---

# 6. API and event dependency matrix

| UI feature | Required contract | Phase |
|---|---|---|
| Login/session restore/logout | Auth APIs + secure storage | 2 |
| Profile/account summary | `GET /api/v1/me` | 2 |
| Voice connection/session state | `WS /v1/voice` + correlated control events | 3 |
| Transcript | Genuine partial/final/error STT events | 4 |
| Streaming assistant response | Canonical LLM request/text/completion/failure events | 5 |
| Response cancellation | Client cancel + stale-event contract | 3/5 |
| Conversation history | Implemented conversation/history API or current-session store | 5+ |
| Memory management | `/api/v1/memories` CRUD/search/delete-all + policy state | 6 |
| Task management | `/api/v1/tasks` CRUD/complete | 7 |
| Reminder management | `/api/v1/reminders` CRUD + push status | 7 |
| Tool confirmation/status | Server-owned registry/tool lifecycle events | 7 |
| TTS playback | Correlated binary audio + playback lifecycle | 8 |
| Barge-in | Native detection + cancel + new-turn correlation | 9 |
| Retention/deletion | Enforced privacy/account APIs | 10 |
| Diagnostics | Redacted readiness/metrics/correlation contract | 11 |

If a contract is unavailable, the corresponding production UI must be hidden, disabled with accurate explanation, or implemented using a declared local-session scope. It must never invent backend state.

---

# 7. Error-copy policy

The server returns stable error codes; the mobile UI maps them to safe, actionable language.

| Failure class | User-facing behavior |
|---|---|
| Microphone permission | Explain why access is needed and offer Android Settings when required. |
| Voice connection | Show reconnecting, then Retry if bounded reconnect fails. |
| STT unavailable | Preserve the captured-turn state where possible; do not send empty text to LLM. |
| LLM unavailable | Preserve final transcript and offer Retry. |
| Memory unavailable | For direct memory questions, say memory lookup is unavailable; do not show a guessed answer as memory. |
| Tool failure | Show the action failed; never show completed/success. |
| TTS unavailable | Keep the completed text response and show a non-blocking audio error. |
| Authentication expired | Stop protected voice resources and return to Login. |
| Rate limit | Explain temporary throttling and when Retry is allowed if known. |
| Protocol/malformed event | Fail the current operation safely; hide raw payload/error internals. |

Never show stack traces, bearer headers, API keys, refresh tokens, raw provider response bodies, hidden reasoning, or sensitive tool arguments.

---

# 8. Accessibility requirements

Required throughout all phases:

- Every interactive element has an accessible name, role, and state.
- Voice animation has a text equivalent such as “Listening”, “Thinking”, or “Speaking”.
- Do not announce every audio frame, VAD probability, token, or partial transcript delta.
- Announce important state transitions and final transcript/response at a controlled cadence.
- Minimum 48 dp touch target for primary controls.
- Support Android font scaling without clipped actions or inaccessible dialogs.
- Message list reading order matches visual order.
- Destructive confirmations clearly identify the action and consequence.
- Error, warning, success, listening, and speaking states do not rely on color alone.
- Respect reduced-motion preference for the voice orb and streaming indicators.
- Maintain accessible contrast in light and dark themes.

---

# 9. Performance and state limits

- Do not rerender the full conversation for every streamed token.
- Batch text deltas and update only the active assistant row.
- Virtualize long message, memory, task, and reminder lists.
- Bound in-memory current-session history and paginate durable history.
- Remove native, socket, timer, and AppState listeners on teardown.
- Maintain exactly one active voice socket and one active playback controller per signed-in app instance.
- Cancel obsolete searches and requests; reject their late results.
- Avoid storing raw audio in JavaScript state.
- Record performance timings without transcript/prompt contents.
- Define and measure P50/P95/P99 for first transcript render, first assistant text render, first audio state, and cancellation-to-idle/listening transition.

---

# 10. Current execution backlog — start during Phase 5

Complete this backlog in order before beginning the Phase 6 UI:

## Milestone A — Audit and contracts

- [ ] Inventory existing screens, navigation, stores, native events, REST clients, WebSocket events, and tests.
- [ ] Record which Phase 0–4 UI items already exist, partially exist, or are absent.
- [ ] Freeze canonical TypeScript types for auth, voice state, transcript, LLM events, and safe errors.
- [ ] Confirm the existing server event names and payloads; adapt this plan to real contracts without breaking accepted behavior.

## Milestone B — UI foundation

- [ ] Implement/standardize app providers and root navigation.
- [ ] Implement design tokens and core components.
- [ ] Implement global error boundary, offline banner, loading, empty, and retry components.
- [ ] Add test rendering utilities and fake native/REST/WebSocket dependencies.

## Milestone C — Authentication

- [ ] Complete Splash/session restoration.
- [ ] Complete Login and auth error states.
- [ ] Complete single-flight refresh and logout cleanup.
- [ ] Add account/settings entry point.

## Milestone D — Assistant conversation

- [ ] Build Assistant screen and message list.
- [ ] Build accessible manual voice control.
- [ ] Integrate connection/reconnect state.
- [ ] Integrate listening/transcribing/final transcript state.
- [ ] Integrate Phase 5 streamed text events.
- [ ] Implement cancellation, stale-event rejection, Retry, and safe errors.
- [ ] Keep tool success UI disabled until confirmed server tool lifecycle exists.

## Milestone E — Verification

- [ ] Pass TypeScript, lint, format, Jest, and component tests.
- [ ] Pass auth, connection, transcript, streaming, cancellation, retry, and stale-event integration tests.
- [ ] Pass TalkBack and large-font checks for Login and Assistant.
- [ ] Install a release-like build on RMX5070.
- [ ] Perform ten consecutive spoken English turns through remote STT and the selected Phase 5 LLM.
- [ ] Save sanitized timestamps/events for final transcript, first text, completion, cancellation, and errors.
- [ ] Confirm no secrets/private transcripts are present in general logs or test evidence.

### Current UI milestone gate

Do not begin Phase 6 production UI until Milestones A–E pass and the Assistant screen is a stable provider-neutral frontend for the accepted Phase 3 gateway, the revised Phase 4 STT contract, and the Phase 5 streaming/cancellation contract.

---

# 11. UI pull-request checklist

Every UI change must answer all applicable items:

- [ ] Which system phase and UI gate does this change serve?
- [ ] Is the UI using an implemented, typed contract rather than guessed payloads?
- [ ] Are loading, empty, error, cancellation, offline, and stale-result states covered?
- [ ] Are user ownership and logout/account-switch cache clearing preserved?
- [ ] Are destructive or side-effecting actions confirmed and server-verified?
- [ ] Are accessibility labels, roles, touch targets, font scaling, contrast, and reduced motion handled?
- [ ] Are tests added at the correct unit/component/integration/E2E level?
- [ ] Does the change avoid putting raw audio or high-frequency DSP work on the JS thread?
- [ ] Are secrets, raw provider errors, private transcripts, and tool arguments excluded from logs/fixtures/screenshots?
- [ ] Does cancellation immediately reject late response content?
- [ ] Does the screen remain correct after background/foreground, reconnect, and session expiry?
- [ ] Were existing native audio and accepted backend behaviors preserved?

---

# 12. Final UI release predicate

The UI is production-ready only when all enabled phase gates pass and the following statements are true:

```text
authenticated routing and secure session lifecycle PASS
manual and approved wake-based voice activation states truthful
voice WebSocket lifecycle and reconnect PASS
final-only versus partial STT behavior represented honestly
streamed LLM text ordered and provider-neutral
cancellation and stale text/audio/tool rejection PASS
unauthorized or malformed tool success presentations = 0
duplicate visible/side-effecting actions = 0
memory inspection/correction/deletion controls PASS when enabled
task/reminder timezone, confirmation, and restart behavior PASS when enabled
TTS fallback and local stop PASS when enabled
physical barge-in UX PASS when enabled
privacy statements match enforced retention/deletion behavior
cross-user cached-state leaks = 0
embedded provider/service secrets = 0
critical TalkBack and maximum-font-scale journeys PASS
supported-device signed-build tests PASS
repeated-turn soak shows no unbounded UI resource growth
```

The UI must always describe the capability the system actually has. A feature that is unavailable, degraded, final-only, awaiting confirmation, cancelled, or failed must never be presented as live, successful, or complete.

## Phase 6 UI status

**IMPLEMENTED — ACCEPTANCE PENDING (revalidated 2026-09-09).**

`MemoryScreen` and its typed API client provide authenticated memory list/search/detail,
content editing, single/delete-all confirmation, memory on/off settings, and exclusion of
the active voice session. Backend code defaults remain disabled, while the local test
environment explicitly enables injection and writes. The live embedding and reranker
contracts pass; physical large-font/TalkBack, reconnect/lifecycle, privacy, and
provider-enabled end-to-end acceptance still need to be observed and recorded. Pagination/date/type filters, server-confirmed
“used memory” response indicators, and production conflict-history presentation remain
acceptance gaps rather than being simulated locally.

## Current verified UI status (2026-09-09)

- Automated frontend gates: **PASS** — TypeScript, Prettier, UI secret scan, and Jest `47/47`; ESLint has `0` errors and `17` warnings.
- UI Phase 1: **ACCEPTANCE PENDING** — large-font and accessibility physical evidence is not recorded.
- UI Phase 2: **PASS**.
- UI Phase 3: **ACCEPTANCE PENDING** — physical reconnect/recovery evidence needs a refreshed disposable authentication run.
- UI Phase 4: **ACCEPTANCE PENDING** — the revised remote-STT ten-turn physical run is not recorded.
- UI Phase 5: **ACCEPTANCE PENDING** — real Android STT-final-to-LLM streaming, cancellation, retry, lifecycle, and tool-status evidence is not recorded.
- UI Phase 6: **IMPLEMENTED — ACCEPTANCE PENDING** — memory controls and automated component coverage are present; provider-enabled physical, accessibility, privacy, lifecycle, and performance evidence is not recorded.
