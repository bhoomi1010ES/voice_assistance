# Phase 3 ADB host environment correction — 2026-09-28

This is an environment-only correction; it does not change Android app behavior,
backend product settings, or repository source code.

## Cause and resolution

The command shell supplied an empty `HOME` value. Windows ADB consequently tried
to create its key/config directory as `\.android` and failed with
`Cannot mkdir '\.android': Permission denied`. Per-session `ANDROID_USER_HOME`
and `ANDROID_SDK_HOME` overrides did not resolve this ADB build's directory
lookup. With narrow host permission for ADB to access its standard
`C:\Users\lenovo\.android` directory, ADB started normally and reported device
`9b0ea196` (`CPH2527`) as `device`.

## Verification

- `RECORD_AUDIO` is granted; Android reports `RECORD_AUDIO: allow` while the app
  is foregrounded.
- The voice app launches as `com.voiceaipoc/.MainActivity`.
- During the isolated shadow test, the phone's port 8000 was temporarily
  reversed to host port 8002. Afterward it was restored to host port 8000.
- Final USB reverse list: `tcp:8000 -> tcp:8000` and `tcp:8081 -> tcp:8081`.
- Metro reports `packager-status:running`; the normal backend returns healthy
  `/health` and ready `/ready` responses.
- The temporary port-8002 shadow listener was stopped.

No shell profile, `.env`, Android permission, application setting, or source file
was changed to work around this host issue.
