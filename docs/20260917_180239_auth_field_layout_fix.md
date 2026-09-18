# Sign-in/sign-up field layout fix

Date: 2026-09-17

## Problem

The email field on the authentication screen expanded to most of the screen height on Android. The shared auth field icon wrapper used `height: '100%'` while its parent had only a minimum height inside a scroll view, allowing the percentage height to resolve incorrectly.

## Change

Updated `frontend/src/components/auth/AuthField.tsx` to use `alignSelf: 'stretch'` instead of percentage height for the icon wrapper. This keeps email, password, and registration fields at their intended compact height on both sign-in and sign-up screens.

## Verification

- `npm.cmd test -- --runInBand --silent __tests__/auth-registration.test.tsx __tests__/phase1.test.tsx` — 6 tests passed.
- `npm.cmd run typecheck` — passed.
- `git diff --check` — passed.
