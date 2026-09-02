# Voice Assistance

This repository contains the React Native Android proof of concept and the
Phase 1/2 backend foundation with the Phase 3 bounded WebSocket voice gateway.
Phase 4 now includes an isolated Windows Speech Recognition STT worker
integrated with the gateway. Its implementation is complete, but acceptance
remains pending Windows integration and physical evaluation evidence.

Current gates:

```text
PHASE 0: FAIL (unchanged; acoustic acceptance is not complete)
PHASE 1: PASS
PHASE 2: PASS
PHASE 3: PASS
PHASE 4: IMPLEMENTED — ACCEPTANCE PENDING
```

The Phase 2 cross-user backend isolation gate passed, Android secure-storage
instrumentation passed, and Android JVM validation completed successfully. The
Phase 3 backend and physical-device gateway validation passed, including the
10-turn microphone run, cancellation, heartbeat, reconnect, and credential
checks. Phase 4 backend/STT tests pass, and actual-model synthetic concurrency
and process-memory measurements were completed. The historical physical run
confirmed the previous CPU/int8 Whisper path produced committed microphone
turns, but it did not complete the required ten consecutive English turns;
physical partial-event evidence and legitimate English WER data remain
unverified. Phase 4 therefore remains implemented with acceptance pending.

Phase 1 through Phase 3 do not change the Android microphone, AEC/NS, VAD,
wake-word model, threshold, cooldown, or native audio pipeline. Phase 3 adds
an authenticated `/v1/voice` gateway, exact binary PCM framing, bounded
transport queues, explicit session/turn controls, PostgreSQL metadata, and
Redis active-session state. Phase 4 adds opt-in Windows Speech Recognition
transcription over that existing 16 kHz mono PCM16 stream, with partial/final
transcript events, cancellation, and per-turn metrics. Python uses a typed
engine abstraction and a reusable local C# `System.Speech.Recognition` worker
over newline-framed stdin/stdout IPC. `STT_ENGINE=windows` is the active
default; the previous Whisper runtime is an optional legacy adapter only.
Qwen/LLM reasoning, RAG, tools, reminders, Kokoro TTS, audio playback, and
barge-in remain deferred.

The original local Whisper candidate and its approximately 63–66 second
physical final-latency result are historical evidence only. The candidate at
`C:\\Users\\lenovo\\.lmstudio\\models\\xkeyC\\whisper-large-v3-turbo-gguf\\model_q4_k.gguf`
is an LM Studio GGUF artifact and is not loaded directly by
faster-whisper/CTranslate2. The approved CTranslate2 conversion is now
available locally at
`models/whisper-large-v3-turbo-ct2/` and loaded successfully on CPU `int8`.
The model directory is local and Git-ignored. The historical implementation
reused one model instance and ran inference in bounded worker threads. The
active Windows path does not load this model, does not require CUDA, LM
Studio, cloud speech, or Kokoro, and requires an installed English Windows
recognizer plus the built worker executable.

## Repository structure

```text
android/                 Existing React Native Android project
backend/app/             FastAPI application, auth, ownership, and infrastructure clients
backend/app/stt/         STT abstraction, Windows adapter, legacy Whisper adapter, and WER utility
backend/windows_stt/     Isolated System.Speech.Recognition C# worker
backend/migrations/      Async Alembic environment and schema migrations
backend/tests/           Backend unit, integration, and isolation tests
docker/                  Backend image and PostgreSQL initialization
docker-compose.yml       PostgreSQL, Redis, and backend services
scripts/bootstrap.ps1    Windows developer bootstrap
src/                     Existing React Native TypeScript code
docs/                    Project records and phase reports
```

## Prerequisites

For the Phase 1/2 backend:

- Python 3.12
- PostgreSQL with the `vector` extension available
- Redis
- Node.js required by the existing React Native project (currently Node >= 22.11.0)
- npm

Docker Desktop is optional for local development. No GPU, Android device, STT
server, TTS server, LLM server, or model server is required for Phase 1.

## Quick start on Windows

From the repository root in PowerShell:

```powershell
.\scripts\bootstrap.ps1
```

The script validates Python 3.12, creates `.env` only when it does not exist,
creates or reuses `.venv`, installs `backend[dev]`, checks the PostgreSQL and
Redis URLs from `.env`, and runs Alembic only when PostgreSQL is reachable. It
does not require Docker, start containers, remove volumes, or overwrite `.env`.
It reports a clear error when native PostgreSQL or Redis is unavailable.

Review `.env` after it is copied from [.env.example](.env.example). The
example contains safe local-development placeholders only. Never commit real
credentials.

`DATABASE_URL` and `REDIS_URL` are the connection settings for the local
backend. Replace `YOUR_PASSWORD` in `.env` with the local PostgreSQL password;
never commit that file. Set `JWT_SECRET_KEY` to a unique random value of at
least 32 characters; never use the example value outside a disposable local
test. `/ready` requires both native services to be reachable.

## Manual backend setup

```powershell
python3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# Edit .env with the local PostgreSQL password, service URLs, and a unique JWT secret.
Set-Location backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir . --reload
```

In a separate PowerShell window, verify the endpoints:

```powershell
Invoke-WebRequest http://localhost:8000/health
Invoke-WebRequest http://localhost:8000/ready
```

If `.env` already exists, do not overwrite it. The backend loads `.env`, while
direct environment variables take precedence over values from that file.

## Optional Docker Compose

Docker remains available as an optional non-GPU stack. It contains:

- `postgres`: `pgvector/pgvector:pg16`, persistent named volume, healthcheck,
  and automatic `vector` extension initialization;
- `redis`: Redis 7.4, persistent named volume, and healthcheck;
- `backend`: Python 3.12 FastAPI image with a healthcheck.

Start or rebuild it with:

```powershell
docker compose up -d --build
```

Stop containers while preserving data:

```powershell
docker compose stop
docker compose down
```

Do not use `docker compose down -v` unless you intentionally want to destroy
the local database and Redis volumes.

## API and migrations

The service exposes:

````text
GET /health  -> 200 {"status":"ok"}
GET /ready   -> 200 {"status":"ready"} when both dependencies respond; otherwise 503

Phase 2 protected endpoints include:

```text
POST /auth/register
POST /auth/login
POST /auth/refresh
POST /auth/logout
GET  /auth/me
GET  /auth/sessions
POST /auth/sessions/{session_id}/revoke
POST /devices/register
GET  /devices
POST /devices/{device_id}/revoke
WS   /ws
````

Protected HTTP requests use `Authorization: Bearer <access-token>`. The WebSocket
uses the same header during the handshake. WebSocket identity is derived only
from the authenticated token; a client-supplied `user_id` cannot change scope.
Refresh tokens are rotated and stored only as SHA-256 hashes. Access tokens are
short-lived and validated against the active user, session, and device.

````

Example checks:

```powershell
Invoke-WebRequest http://localhost:8000/health
Invoke-WebRequest http://localhost:8000/ready
````

Run migrations from the backend directory:

```powershell
Set-Location backend
alembic upgrade head
alembic downgrade -1
```

Migration `0001_enable_pgvector` enables
`CREATE EXTENSION IF NOT EXISTS vector`. Migration `0002_auth_foundation`
creates users, devices, authentication sessions, and audit logs. Migrations
`0003_voice_gateway` and `0004_phase2_user_resources` add voice session/turn
metadata plus ownership-scoped memory and task CRUD foundations. Embedding
search/RAG, reminders, and the remaining voice application features remain
deferred.

## Tests, linting, and formatting

Backend commands:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest
$env:RUN_INTEGRATION_TESTS = "1"
..\.venv\Scripts\python.exe -m pytest -m integration
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m ruff format --check .
```

Integration tests require native PostgreSQL with pgvector and Redis, plus
`$env:RUN_INTEGRATION_TESTS = "1"`. If the services are unavailable, the
integration tests skip without reporting a false success.

Existing React Native commands:

```powershell
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run format:check
npm.cmd test -- --runInBand
npm.cmd run check
```

`npm.cmd run check` does not build Android and does not start microphone
capture. The existing Android development flow still uses Metro. Android unit
tests, including secure token-storage tests, run with:

```powershell
Set-Location android
.\gradlew.bat testDebugUnitTest --no-daemon
```

The existing Android development flow still uses Metro:

```powershell
npm.cmd start
npm.cmd run android
```

For a USB-connected debug device, configure the existing React Native bundle
connection as needed:

```powershell
adb reverse tcp:8081 tcp:8081
```

## CI

GitHub Actions runs backend unit/integration checks with PostgreSQL/Redis
service containers, plus frontend TypeScript, ESLint, Prettier, and Jest
checks. It does not require GPU hardware or an Android device.

## Troubleshooting

### `/ready` returns 503

Check the native service ports and the URLs in `.env`:

```powershell
Test-NetConnection localhost -Port 5432
Test-NetConnection localhost -Port 6379
Get-Content .env
```

The local backend uses `DATABASE_URL` and `REDIS_URL` exactly as configured.
Docker Compose, if used, supplies its own internal service URLs separately.

### pgvector is unavailable

Run the migration and inspect the extension directly:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m alembic upgrade head
psql -U postgres -d voice_assistance -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### React Native says “Unable to load script”

That is a debug-bundle/Metro connection issue, separate from the Phase 1
backend. Start Metro and use the existing USB reverse command above. Do not
change the Android audio pipeline to resolve it.

## Phase 0 preservation

The approved `hey_mycroft` Android model and existing audio implementation
remain frozen. Phase 1 infrastructure work does not authorize retuning or
replacing them, and Phase 0 must not be marked passed until its acoustic and
other mandatory acceptance gates are objectively satisfied.
