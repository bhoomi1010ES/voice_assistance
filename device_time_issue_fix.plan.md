---
name: Device time issue fix
overview: Finish date and time voice turns from validated device-clock tool results, release forced tool choice after the first round, and cover common routing and answer-time accuracy.
todos:
  - id: reproduce-forced-tool-loop
    content: Reproduce a named get_current_time/get_current_date request and verify the first tool succeeds while the follow-up request retains the named tool choice.
    status: completed
  - id: answer-clock-result
    content: After a successful clock-only tool round, emit one deterministic text_delta and terminal response_completed from validated tool-result fields without another provider request; preserve tool status and cancellation behavior.
    status: completed
  - id: release-other-tool-choice
    content: Set tool_choice to auto for every non-clock continuation, including failed/malformed clock results, while keeping confirmation-required turns terminal.
    status: completed
  - id: route-clock-phrases
    content: Support ordinary time/date phrases and combined date-and-time requests, and evaluate task/reminder/meeting lookups before broad clock patterns.
    status: completed
  - id: ui-and-freshness
    content: Accept get_current_date as a read-only conversation tool, and verify that the turn-start device epoch remains acceptably current when the answer is spoken.
    status: completed
  - id: verify-and-record
    content: Run focused backend and frontend tests, verify a real voice turn when available, and write the required timestamped docs note after implementation.
    status: completed
isProject: false
---

# Device date and time issue fix

## Current status and cause

Android sends `device_epoch_ms`, IANA timezone, and UTC offset on session/turn start. The backend validates the context; `get_current_time` returns `local_time`, `local_date`, `timezone`, and `utc_offset`, while `get_current_date` returns the date and zone. The device epoch is a snapshot, not a continuously advancing value in `format_local_time()`.

`classify_voice_tool_choice()` selects a named clock tool for some time/date phrases. `LLMToolLoop.stream()` currently copies only `messages` into the next request, preserving that forced choice after tool execution. A provider continuation can therefore request another tool or fail instead of producing speech. Saved 2026-09-22 clock turns show successful tool activity followed by `llm_provider_error`; the code establishes the repeated-choice defect, while those logs alone do not prove every failed response has the same provider-side cause.

The existing `FakeLLMService` masks the defect because it emits text whenever a tool message is present, regardless of `tool_choice`. The existing tool-loop test starts with the default `auto`, so it does not reproduce a forced clock request.

## 1. Complete a successful clock-only round locally

In `backend/app/llm/tool_loop.py`, keep the provider's initial tool-call lifecycle and the server's `tool_execution_completed` status. After all calls in a round execute, use a deterministic answer only when every completed call is `get_current_time` or `get_current_date`, every execution succeeded, and each JSON result has `ok: true` plus the required nonempty fields. Parse the serialized tool result; never use model text, server wall time, or unchecked user content for the answer.

Emit one `text_delta` followed by one `response_completed` with the same session/turn/response IDs and increasing event sequence numbers. Set a clear terminal finish reason for a tool-derived answer and avoid claiming a second provider request. The existing gateway sends the delta to `assistant.text.delta` and TTS, then persists the terminal response. Keep cancellation checks and the successful tool-status event order intact.

Use the tool fields to say `The current time is {local_time}.` or `Today's date is {local_date}.` For an explicitly requested timezone, include the returned `timezone` in the spoken sentence. For a combined date-and-time request, use the time tool, which already returns both fields, and speak both once. If both clock tools are returned in a round, speak both once without duplicate sentences.

If a clock execution fails or its payload is malformed, do not synthesize a successful time. Continue through the normal tool-result path with `tool_choice="auto"`, allowing an honest provider response about the failure. Preserve the existing terminal `confirmation_required` path.

## 2. Release tool choice for all other continuations

Update the `model_copy` that creates a follow-up `LLMRequest` so `tool_choice` becomes `"auto"` after the first provider round. This covers `list_tasks`, other forced tools, and a clock-result fallback. Keep the assistant tool-call and tool-result messages, including provider continuation items, intact. Add a fake provider that asserts the first request is named and every resumed request is `auto`.

## 3. Correct intent routing and the conversation UI

In `backend/app/llm/context.py`, handle common forms including `what is the date`, `what's the date today`, `what is the current date`, `what's today's date`, `what time is it now`, and both orders of `current date and time`. Keep existing supported forms. Route a combined request to `get_current_time` because that tool already provides date and time.

Check task/reminder/meeting lookup intent before broader clock regexes and before the informational-prefix return. `What time is my meeting?` and `What date is my reminder?` must remain `list_tasks`. Do not route scheduled-item creation to a clock tool.

In `frontend/src/voice/conversation.ts`, add `get_current_date` to `READ_ONLY_TOOL_NAMES`. `SUPPORTED_TOOL_NAMES` is derived from that set, so no separate entry is needed. Extend the existing ordered tool-lifecycle test in `frontend/__tests__/phase5.test.ts`.

## 4. Check clock freshness

The current device epoch is captured at turn start, whereas the clock tool may run seconds later. Measure the age of that snapshot at tool execution and answer emission using a controlled delayed test. If it can cross a minute/date boundary and produce a visibly stale answer, advance the validated device epoch by monotonic elapsed time or refresh it through the existing turn context; preserve the device's timezone and explicit-zone behavior. Keep the server fallback explicit for invalid or absent device context. Do not let a raw client UTC offset replace IANA zone rules.

## 5. Verification and acceptance

In `backend/tests/test_llm_tool_loop.py`, use an initial request with actual named clock `tool_choice`. Assert exactly one provider request for a successful time/date tool round, ordered tool success then spoken delta then terminal completion, and no premature model text. Include a provider that fails on a second call, a forced non-clock tool that resumes with `auto`, and a malformed/failed clock result that does not speak an invented value.

Extend `backend/tests/test_device_aware_time.py` for the phrase matrix, meeting/reminder precedence, combined requests, explicit timezone, and the delayed snapshot case. Run those two backend files and `frontend/__tests__/phase5.test.ts`; add a focused gateway test only if the synthetic terminal event changes gateway behavior. No Android/web/API/admin build is required for this backend and TypeScript scope.

When a device-backed voice check is available, confirm that the tool status completes, the same turn displays a final answer and plays TTS, and a second model call is absent for successful clock-only requests. Check ordinary date, time, combined date/time, an explicit zone, and `What time is my meeting?`.

After code implementation, add `docs/<current date and time>_device_time_issue_fix.md` with the cause, changed files, test results, clock-freshness decision, and physical result if available. Update this plan's todo statuses only when their checks are complete.
