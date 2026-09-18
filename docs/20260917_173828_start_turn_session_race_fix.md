# Start-turn session startup race fix

Date: 2026-09-17

## Problem

The app could receive a Start turn action while the native voice session was still starting. Automatic session startup left the JavaScript snapshot at `session: idle`, so the UI issued another session-start request instead of starting the turn.

## Changes

- Mark the client session as `starting` whenever automatic session startup is requested.
- Make `startTurn()` wait for the normal `server.session.ready` transition when the user taps during that startup window.
- Return a bounded timeout/error if the session does not become ready.
- Added a phase 3 regression test covering Start turn pressed during session startup.

## Verification

- `npm.cmd test -- --runInBand --silent __tests__/phase3.test.ts` — 11 tests passed.
- `npm.cmd test -- --runInBand --silent __tests__/phase9-barge-in.test.ts` — 14 tests passed.
- `npm.cmd run typecheck` — passed.
