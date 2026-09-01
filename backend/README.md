# Backend

This directory contains the Phase 1 infrastructure foundation, the Phase 2
authentication/user/device foundation with ownership-scoped memory/task CRUD,
the bounded Phase 3 WebSocket voice gateway, and the Phase 4 isolated local
CPU STT service. The Phase 4 implementation uses the approved
CTranslate2-compatible Whisper Large-v3-Turbo model at
`models/whisper-large-v3-turbo-ct2/`, `faster-whisper` 1.2.1, and CTranslate2
4.8.1 with CPU `int8`. The LM Studio GGUF artifact is not loaded by the
backend or silently substituted. Phase 4 implementation is complete, but the
acceptance decision is `IMPLEMENTED — ACCEPTANCE PENDING`. A physical RMX5070
device was enumerated and the debug APK was built, installed, and launched;
two committed microphone turns were observed, but the required ten
consecutive English turns and physical partial-event evidence were not proven.
Legitimate English ground-truth data is also unavailable, so WER remains
blocked. Actual-model synthetic 1/2/4-stream and process-memory measurements
are recorded separately and are not real-speech acceptance evidence. The Phase
4 acceptance language is English only; multilingual evaluation is outside this
gate.

Qwen/LLM reasoning, RAG, tools, reminders, Kokoro TTS, playback, barge-in,
and other Phase 5+ voice application features remain deferred.

## Local setup

From the repository root on Windows PowerShell:

```powershell
python3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Review `.env` and configure `DATABASE_URL` and `REDIS_URL` for the native
PostgreSQL and Redis services. Configure a unique `JWT_SECRET_KEY` for local
authentication. Docker Compose is optional; it supplies its own internal URLs
when that path is selected.

## Commands

From `backend/` with the virtual environment active:

```powershell
alembic upgrade head
uvicorn app.main:app --reload
pytest
ruff check .
ruff format --check .
```

The integration tests require running PostgreSQL with pgvector and Redis and
`RUN_INTEGRATION_TESTS=1`. Unit tests use dependency stubs where appropriate;
Phase 2 security integration tests use unique identities and clean up their
database rows.
When either native dependency is unavailable, the integration tests skip with
an explicit reason rather than reporting a false pass.

## Endpoints

- `GET /health` is a process/liveness check and returns HTTP 200.
- `GET /ready` checks PostgreSQL and Redis and returns HTTP 200 with
  `{"status":"ready"}` only when both dependencies respond successfully;
  otherwise it returns HTTP 503 with structured dependency status.
- `POST /auth/register`, `/auth/login`, `/auth/refresh`, and `/auth/logout`
  provide account and token lifecycle operations.
- `/devices` provides ownership-scoped device registration, listing, and
  revocation.
- `/auth/sessions` provides ownership-scoped session listing and revocation.
- `WS /ws` requires a bearer access token and binds the connection to that
  authenticated user. Client messages cannot override the connection identity.
- `WS /v1/voice` requires the same bearer access token and accepts the existing
  16 kHz mono PCM16 stream as exact 640-byte binary frames. Use explicit
  `client.session.start`, `client.turn.start`, `client.audio.commit`,
  `client.response.cancel`, `client.ping`, and `client.session.end` controls.
  The fixed binary frame header is 24 bytes: `VAI1`, version 1, client PCM
  type 1, zero flags, a per-turn sequence number, a client timestamp, and
  payload length.

The voice gateway preserves the Phase 3 transport behavior and can enable
local STT with `stt: {"enabled": true, "language": "..."}` in
`client.session.start`. It emits `transcript.partial` and
`transcript.final` events and persists final transcript metadata in the turn.
PostgreSQL stores durable session/turn boundaries and Redis stores TTL-bound
active connection/turn/response/cancellation ownership. One bounded ingress
queue is used per connection; gaps, duplicates, oversized frames, invalid
state transitions, and cross-owner resume attempts are rejected. No LLM,
tools, TTS, or playback is implemented.

For a local synthetic protocol test, set `RUN_INTEGRATION_TESTS=1` and run
`pytest tests/test_voice_gateway_integration.py`. The test sends one exact
640-byte PCM frame and does not retain audio. On Android, the transport is
explicitly controlled from the diagnostic screen; it never starts or stops
the microphone and does not send PCM through React Native. The default
diagnostic URL is `ws://127.0.0.1:8000/v1/voice`, suitable for a USB device
after `adb reverse tcp:8000 tcp:8000`.

Refresh tokens are stored as SHA-256 hashes, access tokens are short-lived
signed JWTs, and every protected resource query is scoped by the authenticated
user ID. Security-sensitive actions create structured audit records without
passwords, tokens, or authorization headers.

Application logs are JSON-compatible and do not include credentials, tokens,
or raw audio.
