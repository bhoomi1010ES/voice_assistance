# Device time issue fix

Work recorded: 2026-09-23 18:18:28 UTC

## Summary

- Successful `get_current_time` and `get_current_date` tool-only rounds now produce one deterministic spoken answer directly from parsed, successful tool-result envelopes. The tool success event is preserved, then one text delta and one terminal completion are emitted with the same turn identifiers and increasing sequence numbers.
- Continuations after any non-terminal tool round now use `tool_choice="auto"`. Failed or malformed clock results go through the provider continuation path without a synthesized time. The existing confirmation-required terminal path is unchanged.
- Clock routing now handles common date and time phrases, combined date/time requests, and scheduled-item lookup precedence. Scheduled-item creation remains routed to task creation.
- Validated device-clock snapshots advance by monotonic elapsed time. Explicit timezone views preserve the original snapshot capture point and continue to derive offsets from IANA timezone rules.
- The conversation UI accepts `get_current_date` as a read-only tool and applies the existing ordered lifecycle transitions.

## Files changed

- `backend/app/llm/tool_loop.py`
- `backend/app/llm/context.py`
- `backend/app/services/device_time.py`
- `backend/app/websocket/gateway.py`
- `backend/tests/test_llm_tool_loop.py`
- `backend/tests/test_device_aware_time.py`
- `frontend/src/voice/conversation.ts`
- `frontend/__tests__/phase5.test.ts`
- `.gitignore` (allowlist this task's required work record)
- `device_time_issue_fix.plan.md` (todo statuses)
- `docs/20260923_181828Z_device_time_issue_fix.md` (this record)

## Verification

- Backend focused suite: `pytest tests/test_llm_tool_loop.py tests/test_device_aware_time.py -q` — 53 passed.
- Frontend focused suite: `npm test -- --runInBand __tests__/phase5.test.ts` — 12 passed.
- Ruff lint passed with E501 ignored; the full targeted Ruff run only reports an existing overlong line at `backend/app/llm/context.py:237`.
- Ruff format check passed for `tool_loop.py`, `device_time.py`, and both changed backend test files.
- `git diff --check` passed.
- Frontend typecheck remains blocked by an existing TypeScript error at `frontend/__tests__/phase3.test.ts:762` (a call on a value typed `never`); that file is untouched.
- A device-backed voice check could not be run. `adb devices -l` could not initialize its `.android` directory due to a permission error, including when Android home variables pointed to existing workspace caches.

## Clock freshness decision

The server advances the validated turn-start epoch by the monotonic time elapsed since validation. This accounts for provider and tool delays, including a minute or date boundary, without trusting the client UTC offset or server wall time for device-local answers. A controlled delayed tool-execution test crosses midnight and verifies the refreshed date and time.
