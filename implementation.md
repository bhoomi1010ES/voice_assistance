# Voice AI Assistant — Final Architecture & End-to-End Implementation Plan

**Document status:** Senior-approval baseline  
**Reviewed against current documentation:** 2026-08-21  
**Primary client:** React Native Android application  
**Primary backend:** FastAPI + PostgreSQL + Redis  
**Primary AI stack:** openWakeWord + Silero VAD + Whisper Large-v3-Turbo + BGE-M3 + BGE Reranker + Qwen + Kokoro

---

## 1. Executive Decision

The proposed architecture is fundamentally sound and is a good baseline for a self-hosted, voice-native personal assistant.

However, it should be approved with several important implementation corrections:

1. **React Native remains the UI/application framework**, but wake-word, VAD, low-latency microphone capture, audio processing, and interruption handling must be treated as an **on-device/native inference layer**, not as ordinary JavaScript-only features.
2. Add **AEC (Acoustic Echo Cancellation)** and **Noise Suppression** to the mobile audio pipeline. Without this, barge-in while the assistant is speaking will be unreliable because the microphone can hear the assistant's own TTS audio.
3. Add a **Voice Session Orchestrator** between the WebSocket gateway and AI services. Every response must have a `session_id`, `turn_id`, and `response_id` so old STT/LLM/TTS work can be cancelled immediately.
4. Keep **Whisper Large-v3-Turbo**, but serve it with an STT-oriented runtime such as **faster-whisper/CTranslate2** rather than trying to run it through vLLM.
5. Keep **Qwen3.5-9B** as the latency-first baseline. Qwen3.6-27B is a valid upgrade path, and newer Qwen families can be evaluated later without changing the architecture.
6. Replace the vague `BGE Reranker Base` decision with **`BAAI/bge-reranker-v2-m3`** for the initial multilingual RAG implementation.
7. Use **LM Studio only where it fits** during development—primarily LLM and embedding experimentation. Run Whisper and Kokoro as separate local services.
8. Treat PostgreSQL as the **durable source of truth** for reminders/tasks. Redis is for sessions, cancellation state, caching, rate limits, and event delivery—not the only copy of a user's reminders.
9. For memory questions such as “When did I last visit Mumbai?”, query **structured temporal memory first**, then use semantic/keyword RAG as fallback or supporting context.
10. Cloudflare can proxy WebSockets, but WAF/rate-limit inspection mainly applies to the **initial WebSocket HTTP upgrade request**. The application must enforce authentication, authorization, frame/message limits, and session rules after the connection is open.

### Approval recommendation

> **APPROVE as the baseline architecture, subject to an early Android wake-word/VAD/audio proof-of-concept and end-to-end latency benchmark before the rest of the product is built.**

---

# 2. Final Corrected Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         REACT NATIVE ANDROID APP                            │
│                                                                             │
│  UI / Auth / Settings / Conversation UI / Task UI                          │
│                                                                             │
│  Native Audio Engine                                                       │
│  ├── Android AudioRecord                                                   │
│  ├── AEC / Noise Suppression                                               │
│  ├── Resampling / PCM framing                                              │
│  ├── Ring buffer / pre-roll                                                │
│  ├── openWakeWord inference                                                │
│  ├── Silero VAD inference                                                  │
│  └── Barge-in / playback cancellation                                      │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
                     WSS + HTTPS / TLS
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLOUDFLARE                                     │
│                                                                             │
│ DNS │ TLS │ DDoS │ WAF │ Connection Rate Limiting                          │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FASTAPI API / GATEWAY                               │
│                                                                             │
│ Auth │ REST APIs │ WebSocket Gateway │ Validation │ Rate Controls          │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       VOICE SESSION ORCHESTRATOR                            │
│                                                                             │
│ session_id │ turn_id │ response_id │ cancellation │ state machine          │
│ partial transcript routing │ tool lifecycle │ TTS lifecycle                │
└───────────────┬──────────────────────┬──────────────────────┬───────────────┘
                │                      │                      │
                ▼                      ▼                      ▼
       ┌────────────────┐     ┌──────────────────┐   ┌──────────────────────┐
       │  STT SERVICE   │     │ MEMORY / RETRIEVAL│   │    TOOL ENGINE       │
       │                │     │                  │   │                      │
       │ Whisper        │     │ Structured memory│   │ Reminders            │
       │ large-v3-turbo │     │ BGE-M3           │   │ Task execution       │
       │ faster-whisper │     │ pgvector         │   │ External actions     │
       │ / CT2          │     │ PostgreSQL FTS   │   │ Idempotency          │
       └───────┬────────┘     │ BGE reranker     │   └──────────┬───────────┘
               │              └─────────┬────────┘              │
               └────────────────────────┼───────────────────────┘
                                        │
                                        ▼
                           ┌────────────────────────┐
                           │    REASONING / AGENT   │
                           │                        │
                           │    Qwen3.5-9B          │
                           │       via vLLM         │
                           │                        │
                           │ reasoning / routing    │
                           │ structured outputs     │
                           │ tool calling           │
                           └───────────┬────────────┘
                                       │
                              streamed text
                                       │
                                       ▼
                           ┌────────────────────────┐
                           │      TTS SERVICE       │
                           │                        │
                           │       Kokoro-82M       │
                           │ phrase/sentence chunks │
                           └───────────┬────────────┘
                                       │
                            streamed audio chunks
                                       │
                                       ▼
                           ┌────────────────────────┐
                           │      ANDROID APP       │
                           │                        │
                           │ jitter buffer          │
                           │ speaker playback       │
                           │ barge-in cancellation  │
                           └────────────────────────┘


Durable state:
    PostgreSQL + pgvector

Ephemeral state:
    Redis

Background processing:
    Reminder Worker / Memory Extraction Worker / Embedding Worker

Observability:
    OpenTelemetry + metrics + structured logs + error tracking
```

---

# 3. Final Technology Decisions

| Layer | Final baseline | Notes |
|---|---|---|
| Mobile UI | React Native + TypeScript | Main UI/application layer |
| Android audio | Native Android `AudioRecord` through RN native/Turbo module | Prefer native control for continuous low-latency audio |
| Echo cancellation | Android AEC where available | Required for reliable barge-in |
| Noise suppression | Android NoiseSuppressor where available | Improve wake/VAD/STT input |
| Wake word | openWakeWord | Use ONNX/TFLite model assets; validate Android inference early |
| VAD | Silero VAD | ONNX on-device | 
| On-device inference | ONNX Runtime React Native or Android native ONNX Runtime | Benchmark both if needed |
| Client audio format v1 | PCM16 LE, mono, 16 kHz | Simpler first implementation |
| Later audio transport | Opus | Optional bandwidth optimization |
| Realtime transport | Secure WebSocket (`wss://`) | Binary audio + JSON control events |
| Edge | Cloudflare | DNS/TLS/DDoS/WAF/connection controls |
| API | FastAPI | REST + WebSocket gateway |
| Session orchestration | FastAPI/Python async module | Explicit response cancellation |
| STT model | `openai/whisper-large-v3-turbo` | 809M-parameter pruned large-v3 variant |
| STT runtime | faster-whisper/CTranslate2 | Better fit for server ASR |
| Embedding | `BAAI/bge-m3` | 1024-dimensional multilingual embeddings |
| Vector store | pgvector | HNSW initially |
| Keyword search | PostgreSQL FTS | Hybrid retrieval |
| Reranker | `BAAI/bge-reranker-v2-m3` | Multilingual, lightweight relative to larger rerankers |
| Reasoning model | `Qwen/Qwen3.5-9B` | Latency-first baseline |
| Upgrade candidate | Qwen3.6-27B / later Qwen versions | Upgrade only after benchmark |
| LLM runtime | vLLM | OpenAI-compatible serving, structured output/tool calling |
| TTS | `hexgrad/Kokoro-82M` | Fast, small open-weight TTS |
| Durable DB | PostgreSQL | System of record |
| Cache/session/event layer | Redis | Not source of truth for durable user data |
| DB migrations | Alembic | Version-controlled schema |
| Python data layer | SQLAlchemy 2.x + asyncpg | Recommended backend baseline |
| Local LLM experimentation | LM Studio | LLM/embedding development only |
| Containerization | Docker / Docker Compose | Local and production packaging |
| Metrics/tracing | OpenTelemetry + Prometheus/Grafana-compatible backend | Vendor-neutral |
| Error tracking | Sentry or equivalent | Optional but recommended |

---

# 4. Key Architecture Rules

## 4.1 Do not let the React Native JavaScript thread own the realtime audio loop

The React Native UI can control voice state, display transcripts, and react to events, but continuous microphone acquisition and latency-sensitive inference should not depend on the JS event loop.

Recommended split:

```text
React Native TypeScript
        │
        ▼
Voice Native Module
        │
        ├── AudioRecord
        ├── AEC / NS
        ├── ring buffer
        ├── openWakeWord
        ├── Silero VAD
        └── PCM frame callback / WebSocket queue
```

The native layer should expose higher-level events:

```text
wakeword.detected
speech.started
speech.ended
audio.frame
barge_in.detected
audio.error
```

rather than pushing every low-level DSP operation into JavaScript.

---

## 4.2 Wake-word and VAD have different jobs

### Idle state

```text
Microphone
   ↓
AEC/NS
   ↓
Wake-word detector
   ↓
No wake word → remain idle
```

### After wake word

```text
Wake word detected
   ↓
Create voice session/turn
   ↓
Enable/send speech frames
   ↓
Silero VAD
   ↓
Speech end detected
   ↓
Finish STT turn
```

### While assistant is talking

```text
TTS is playing
   +
microphone remains active
   ↓
AEC tries to remove speaker playback
   ↓
Silero VAD detects real user speech
   ↓
barge_in
   ↓
stop local playback immediately
   ↓
cancel old server response_id
   ↓
capture/process new utterance
```

Wake-word need not be required for every follow-up turn while the assistant is already in an active conversation. Make this a product setting/state-machine decision.

---

# 5. Mobile Audio Specification

## 5.1 First implementation format

Use:

```text
Sample rate:     16,000 Hz
Channels:        1 / mono
Sample format:   signed PCM16 little-endian
Frame duration:  20 ms
Samples/frame:   320
Bytes/frame:     640
```

This keeps the first transport implementation easy to inspect and debug.

At 50 frames/second:

```text
640 bytes × 50 ≈ 32 KB/s raw audio
```

This is reasonable for the first version.

Later, if bandwidth becomes material, add Opus while keeping the internal inference pipeline normalized to the required sample rate.

## 5.2 Ring buffer / pre-roll

Maintain roughly a short rolling audio buffer before wake-word activation.

Purpose:

```text
wake word detected slightly late
       ↓
pre-roll retrieves preceding frames
       ↓
first spoken word is not clipped
```

Do not immediately hard-code one value across every Android device. Make pre-roll configurable and tune during device testing.

## 5.3 Audio preprocessing

Recommended order:

```text
AudioRecord
   ↓
AEC
   ↓
Noise Suppression
   ↓
optional AGC
   ↓
resample / normalize
   ↓
wake-word + VAD + stream
```

Always feature-detect AEC/NS because Android device support varies.

---

# 6. Voice Session State Machine

A realtime assistant becomes much easier to reason about when every connection follows an explicit state machine.

```text
DISCONNECTED
    ↓
CONNECTING
    ↓
IDLE_LISTENING
    ↓ wake word
CAPTURING_USER
    ↓ VAD end
TRANSCRIBING
    ↓
THINKING
    ↓
TOOL_EXECUTION (optional)
    ↓
SPEAKING
    ↓
IDLE_LISTENING / FOLLOW_UP_WINDOW
```

At almost any active state:

```text
user interruption
    ↓
CANCELLING
    ↓
CAPTURING_USER
```

## 6.1 Required identifiers

Every event should carry enough identity to reject stale work:

```text
user_id
device_id
session_id
turn_id
response_id
sequence_no
client_timestamp_ms
```

### Example

```json
{
  "type": "client.turn.start",
  "session_id": "vs_...",
  "turn_id": "turn_...",
  "sequence_no": 1,
  "client_timestamp_ms": 1787290000000
}
```

When a response is interrupted:

```json
{
  "type": "client.response.cancel",
  "session_id": "vs_...",
  "turn_id": "turn_...",
  "response_id": "resp_..."
}
```

The server must propagate cancellation to:

```text
LLM stream
TTS generation
queued audio
non-committed tool execution where cancellation is safe
```

A destructive or externally committed tool must **not** be rolled back merely because speech playback was interrupted.

---

# 7. WebSocket Protocol

Use a single authenticated WebSocket session for one active voice conversation.

Suggested endpoint:

```text
wss://voice.example.com/v1/voice
```

## 7.1 Control messages — JSON

Client → server:

```text
client.session.start
client.turn.start
client.audio.commit
client.response.cancel
client.ping
client.session.end
```

Server → client:

```text
server.session.ready
stt.partial
stt.final
assistant.thinking
assistant.text.delta
tool.started
tool.completed
tool.failed
tts.started
tts.completed
response.cancelled
server.error
server.pong
```

## 7.2 Audio frames — binary

Do not Base64-encode realtime audio into JSON unless there is a strong reason.

Use WebSocket binary frames for:

```text
client microphone audio → server
server TTS audio → client
```

The connection/session state tells the receiver what direction/type is currently expected.

If one socket becomes too difficult to multiplex, use a tiny binary header with:

```text
version
frame_type
sequence_no
timestamp
payload_length
```

Do not invent a complex protocol until measurements justify it.

## 7.3 Backpressure

The mobile app and server must both have bounded queues.

Example policy:

```text
audio queue grows above safe threshold
    ↓
report degraded connection
    ↓
drop old partial/nonessential events first
    ↓
never allow unlimited memory growth
```

For TTS, local playback should maintain only a small jitter buffer so cancellation feels immediate.

---

# 8. Cloudflare Design

Recommended DNS separation:

```text
api.example.com      → normal REST traffic
voice.example.com    → WebSocket voice traffic
```

Cloudflare currently supports proxied WebSocket connections.

Important limitation:

> WAF and rate limiting can inspect the initial WebSocket HTTP upgrade request, but after the socket is established the application still needs its own message/session-level controls.

Therefore the FastAPI gateway must enforce:

```text
JWT/user authentication
device authorization
maximum concurrent voice sessions/user
maximum frame size
maximum audio duration/turn
idle timeout
heartbeat timeout
sequence validation
message frequency limits
daily/minute usage quotas
```

Use `wss://`, not plain `ws://`, in production.

Do not rely on Cloudflare as the only authorization boundary.

---

# 9. STT Architecture

## 9.1 Model

Baseline:

```text
openai/whisper-large-v3-turbo
```

It is a pruned/faster variant of large-v3 and is a sensible accuracy/latency baseline.

## 9.2 Runtime

Recommended server implementation:

```text
Whisper Large-v3-Turbo
        ↓
CTranslate2 conversion/runtime
        ↓
faster-whisper based STT service
```

Whisper is not inherently a native token-by-token realtime streaming ASR model in the same way some dedicated realtime ASR models are.

For voice interaction, implement **incremental/pseudo-streaming**:

```text
incoming PCM frames
       ↓
speech buffer
       ↓
incremental decode windows
       ↓
partial transcript
       ↓
VAD detects end
       ↓
final decode
       ↓
stt.final
```

Never let partial transcripts create irreversible tools.

Only `stt.final` or a clearly committed user turn should trigger an external action.

## 9.3 STT service API

Internal endpoint example:

```text
POST /internal/stt/transcribe
```

For streaming, prefer an internal async interface or persistent service connection instead of repeatedly creating HTTP requests per 20 ms audio frame.

Logical interface:

```python
start_stream(session_id, turn_id, language_hint)
push_audio(pcm_bytes)
get_partial()
finish_stream()
cancel_stream()
```

---

# 10. Reasoning LLM

## 10.1 Baseline

```text
Qwen/Qwen3.5-9B
```

Why it remains a good starting point:

```text
smaller model
   ↓
lower VRAM pressure
   ↓
higher concurrency
   ↓
lower first-token latency
   ↓
better voice UX
```

Qwen3.5-9B is currently documented as compatible with vLLM.

For tool use, Qwen's current documentation includes vLLM serving with reasoning and tool-call parsers.

## 10.2 Upgrade policy

Do not encode this as:

```text
if quality bad → automatically deploy 27B
```

Instead build an evaluation suite.

Evaluate:

```text
Qwen3.5-9B
Qwen3.6-27B
newer compatible Qwen releases
```

against the same dataset:

```text
memory questions
date/time reasoning
reminder creation
tool selection
multi-turn correction
ambiguous requests
personalization
hallucination resistance
latency
tokens/sec
VRAM
concurrency
```

Only change the default model when the quality gain justifies the latency/cost increase.

## 10.3 Production vLLM configuration principle

Pin:

```text
exact model revision
exact vLLM version
CUDA version
driver version
PyTorch version
quantization
max model length
tool parser
reasoning parser
```

Do not use floating `latest` versions in production.

---

# 11. Tool Calling Rules

The LLM never directly writes arbitrary SQL or directly performs external actions.

Correct flow:

```text
Qwen
 ↓
validated structured tool request
 ↓
Tool Registry
 ↓
Pydantic schema validation
 ↓
authorization / policy
 ↓
idempotency check
 ↓
tool implementation
 ↓
durable result
 ↓
tool result returned to Qwen
 ↓
final user response
```

Example tool:

```python
create_reminder(
    title: str,
    trigger_at: datetime,
    timezone: str,
    notes: str | None
)
```

## 11.1 Tool classes

Suggested v1 tools:

```text
memory_search
memory_save
memory_forget
create_reminder
update_reminder
delete_reminder
list_reminders
create_task
update_task
complete_task
list_tasks
```

Later:

```text
calendar.*
email.*
contacts.*
weather.*
maps.*
smart_home.*
```

Keep external integrations behind explicit permission scopes.

## 11.2 Idempotency

Every mutating tool call should contain or derive an idempotency key.

Example:

```text
(user_id, turn_id, tool_name, tool_call_id)
```

If a mobile reconnect causes the same request to be replayed, the tool layer returns the prior result instead of creating duplicate reminders.

---

# 12. TTS Architecture

Baseline:

```text
hexgrad/Kokoro-82M
```

Kokoro is small enough to be attractive for low-latency TTS.

Do not wait for the complete assistant answer before synthesizing a long response.

Use:

```text
LLM token stream
     ↓
safe phrase/sentence segmenter
     ↓
TTS chunk
     ↓
send audio
     ↓
continue next phrase
```

Be careful with tool calls:

```text
"I'll schedule that..."
```

must not be spoken as a confirmed action until the tool actually succeeds.

Correct:

```text
tool succeeds
   ↓
"Done. I've scheduled it for..."
```

## 12.1 TTS cancellation

Each TTS chunk belongs to a `response_id`.

When interrupted:

```text
client stops playback immediately
server marks response cancelled
queued TTS chunks are discarded
Kokoro generation is cancelled when possible
any later chunk with stale response_id is ignored by client
```

Client-side rejection of stale `response_id` is essential even if server cancellation is perfect.

---

# 13. Memory Architecture

Deep personalization needs more than a vector database.

Use three layers:

```text
1. Structured memory
2. Semantic/keyword memory
3. Recent conversation context
```

## 13.1 Structured memory

Use for facts with explicit meaning:

```text
name
relationship
preference
location visit
birthday
task
appointment
habit
important date
owned item
project
```

Example:

```json
{
  "memory_type": "event",
  "subject": "user",
  "predicate": "visited",
  "object": {
    "place": "Mumbai"
  },
  "occurred_start_at": "2026-03-12T00:00:00+05:30",
  "occurred_end_at": "2026-03-16T00:00:00+05:30",
  "confidence": 0.98
}
```

Question:

```text
"What was the last time I visited Mumbai?"
```

should first become a deterministic query:

```text
event_type = visit
place = Mumbai
ORDER BY occurred_start_at DESC
LIMIT 1
```

Only use vector RAG if structured memory does not answer confidently or if supporting context is useful.

## 13.2 Semantic memory

Use BGE-M3 to embed:

```text
conversation summaries
user-provided facts
events
preferences
notes
project context
important observations
```

Do not blindly embed every 20 ms transcript fragment.

## 13.3 Keyword memory

PostgreSQL FTS helps with:

```text
names
rare terms
codes
product names
addresses
exact phrases
```

## 13.4 Hybrid retrieval

Recommended pipeline:

```text
query
  │
  ├── structured query candidates
  │
  ├── dense vector candidates
  │
  └── PostgreSQL FTS candidates
          ↓
deduplicate
          ↓
Reciprocal Rank Fusion / controlled hybrid merge
          ↓
top candidate set
          ↓
BGE reranker-v2-m3
          ↓
top context
          ↓
Qwen
```

Do not add raw cosine similarity and raw PostgreSQL text rank directly without normalization; their scales are unrelated.

---

# 14. Memory Write Pipeline

Not every sentence deserves permanent memory.

After a completed turn or session:

```text
conversation
    ↓
memory extraction policy
    ↓
candidate memories
    ↓
classify:
  - fact
  - preference
  - event
  - relationship
  - routine
  - project
  - summary
    ↓
confidence / salience
    ↓
deduplication
    ↓
conflict detection
    ↓
write structured memory
    ↓
chunk if needed
    ↓
BGE-M3 embedding
```

## 14.1 Conflict handling

Example:

```text
old: user prefers tea
new: user says "I don't drink tea anymore; I prefer coffee"
```

Do not simply store both as equally valid facts.

Use:

```text
valid_from
valid_to
supersedes_id
status
confidence
```

so current preference is clear while history can remain available.

## 14.2 User controls

Provide APIs/UI for:

```text
view memories
delete a memory
correct a memory
disable long-term memory
delete all personal memory
exclude a conversation from memory
```

This is both a product-quality and privacy requirement.

---

# 15. Recommended Repository Structure

A monorepo is appropriate, but do not turn every logical box into a separately operated microservice immediately.

```text
voice-ai/
│
├── README.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Makefile
├── pnpm-workspace.yaml
│
├── apps/
│   └── mobile/
│       ├── android/
│       ├── ios/                       # optional later
│       ├── src/
│       │   ├── app/
│       │   ├── screens/
│       │   ├── components/
│       │   ├── navigation/
│       │   ├── hooks/
│       │   ├── stores/
│       │   ├── api/
│       │   ├── voice/
│       │   │   ├── VoiceManager.ts
│       │   │   ├── VoiceSocket.ts
│       │   │   ├── VoiceStateMachine.ts
│       │   │   ├── AudioPlayback.ts
│       │   │   └── types.ts
│       │   └── utils/
│       │
│       └── native/
│           └── voice-audio/
│               ├── android/
│               │   └── src/main/java/.../
│               │       ├── VoiceAudioModule.kt
│               │       ├── AudioCaptureEngine.kt
│               │       ├── AudioEffects.kt
│               │       ├── WakeWordEngine.kt
│               │       ├── VadEngine.kt
│               │       ├── RingBuffer.kt
│               │       └── AudioResampler.kt
│               └── src/
│                   └── NativeVoiceAudio.ts
│
├── services/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── alembic.ini
│   │   ├── alembic/
│   │   └── app/
│   │       ├── main.py
│   │       ├── config.py
│   │       ├── dependencies.py
│   │       │
│   │       ├── api/
│   │       │   ├── auth.py
│   │       │   ├── memories.py
│   │       │   ├── tasks.py
│   │       │   ├── reminders.py
│   │       │   └── health.py
│   │       │
│   │       ├── websocket/
│   │       │   ├── endpoint.py
│   │       │   ├── protocol.py
│   │       │   ├── connection_manager.py
│   │       │   └── limits.py
│   │       │
│   │       ├── orchestrator/
│   │       │   ├── session.py
│   │       │   ├── turn.py
│   │       │   ├── cancellation.py
│   │       │   ├── pipeline.py
│   │       │   └── state_machine.py
│   │       │
│   │       ├── agent/
│   │       │   ├── llm_client.py
│   │       │   ├── prompts.py
│   │       │   ├── router.py
│   │       │   ├── schemas.py
│   │       │   └── response_policy.py
│   │       │
│   │       ├── memory/
│   │       │   ├── service.py
│   │       │   ├── structured.py
│   │       │   ├── hybrid_search.py
│   │       │   ├── rrf.py
│   │       │   ├── rerank.py
│   │       │   ├── extraction.py
│   │       │   └── dedup.py
│   │       │
│   │       ├── tools/
│   │       │   ├── registry.py
│   │       │   ├── executor.py
│   │       │   ├── permissions.py
│   │       │   ├── idempotency.py
│   │       │   ├── reminder_tools.py
│   │       │   ├── task_tools.py
│   │       │   └── memory_tools.py
│   │       │
│   │       ├── db/
│   │       │   ├── session.py
│   │       │   ├── models/
│   │       │   └── repositories/
│   │       │
│   │       ├── redis/
│   │       │   ├── client.py
│   │       │   ├── sessions.py
│   │       │   └── events.py
│   │       │
│   │       ├── security/
│   │       │   ├── auth.py
│   │       │   ├── tokens.py
│   │       │   └── rate_limit.py
│   │       │
│   │       └── observability/
│   │           ├── logging.py
│   │           ├── metrics.py
│   │           └── tracing.py
│   │
│   ├── stt/
│   │   ├── pyproject.toml
│   │   ├── app.py
│   │   ├── model.py
│   │   ├── streaming.py
│   │   └── Dockerfile
│   │
│   ├── embeddings/
│   │   ├── pyproject.toml
│   │   ├── app.py
│   │   ├── bge_m3.py
│   │   └── Dockerfile
│   │
│   ├── reranker/
│   │   ├── pyproject.toml
│   │   ├── app.py
│   │   ├── bge_reranker.py
│   │   └── Dockerfile
│   │
│   └── tts/
│       ├── pyproject.toml
│       ├── app.py
│       ├── kokoro.py
│       ├── chunker.py
│       └── Dockerfile
│
├── workers/
│   ├── reminder-worker/
│   │   ├── worker.py
│   │   └── scheduler.py
│   ├── memory-worker/
│   │   ├── worker.py
│   │   ├── extractor.py
│   │   └── embedder.py
│   └── cleanup-worker/
│       └── worker.py
│
├── packages/
│   ├── contracts/
│   │   ├── websocket-events.json
│   │   └── tool-schemas.json
│   └── test-fixtures/
│
├── infra/
│   ├── docker/
│   ├── cloudflare/
│   ├── nginx/                         # only if needed behind/beside CF
│   ├── postgres/
│   ├── redis/
│   ├── monitoring/
│   └── deployment/
│
├── scripts/
│   ├── dev-up.sh
│   ├── migrate.sh
│   ├── seed.sh
│   ├── benchmark-stt.py
│   ├── benchmark-llm.py
│   └── benchmark-e2e.py
│
└── tests/
    ├── e2e/
    ├── load/
    ├── audio/
    └── evaluation/
```

---

# 16. Deployment Boundaries

The code can be modular without requiring 10 servers on day one.

## Initial production topology

```text
Server A — API / Data
    FastAPI
    reminder worker
    memory worker
    PostgreSQL
    Redis

Server B — GPU
    STT service
    Qwen/vLLM
    embeddings
    reranker
    Kokoro
```

If one GPU cannot sustain all models concurrently, split by workload:

```text
GPU 1 → Qwen
GPU 2 → Whisper + embeddings + reranker + TTS
```

or use model loading/offloading based on measured traffic.

## Later scaling

```text
Cloudflare
   ↓
API load balancer
   ↓
FastAPI replicas
   ↓
Redis session/event coordination
   ↓
GPU service pool
   ├── STT replicas
   ├── LLM replicas
   └── TTS replicas
```

PostgreSQL remains the durable state layer.

---

# 17. PostgreSQL Database Design

Enable:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
```

The following schema is a strong v1 starting point. Use Alembic migrations in real code rather than manually applying production SQL.

---

## 17.1 Users

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email CITEXT UNIQUE,
    display_name TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    locale TEXT NOT NULL DEFAULT 'en',
    memory_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
```

If password authentication is used, keep authentication credentials in a dedicated table/service and store only modern password hashes. Do not store plaintext passwords.

---

## 17.2 Devices

```sql
CREATE TABLE devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    device_name TEXT,
    app_version TEXT,
    os_version TEXT,
    push_token TEXT,
    wakeword_model_version TEXT,
    vad_model_version TEXT,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);

CREATE INDEX idx_devices_user ON devices(user_id);
```

---

## 17.3 Auth sessions

```sql
CREATE TABLE auth_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id UUID REFERENCES devices(id) ON DELETE SET NULL,
    refresh_token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);

CREATE INDEX idx_auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX idx_auth_sessions_expiry ON auth_sessions(expires_at);
```

---

## 17.4 Voice sessions

```sql
CREATE TABLE voice_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id UUID REFERENCES devices(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    wakeword TEXT,
    client_version TEXT,
    stt_model TEXT,
    llm_model TEXT,
    tts_model TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_voice_sessions_user_started
ON voice_sessions(user_id, started_at DESC);
```

---

## 17.5 Conversation turns

```sql
CREATE TABLE conversation_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES voice_sessions(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    turn_no INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    interrupted BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(session_id, turn_no)
);

CREATE INDEX idx_turns_session ON conversation_turns(session_id, turn_no);
CREATE INDEX idx_turns_user_time ON conversation_turns(user_id, started_at DESC);
```

---

## 17.6 Messages

```sql
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turn_id UUID NOT NULL REFERENCES conversation_turns(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool', 'system')),
    content TEXT,
    content_json JSONB,
    is_final BOOLEAN NOT NULL DEFAULT TRUE,
    model TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_turn ON messages(turn_id, created_at);
CREATE INDEX idx_messages_user_time ON messages(user_id, created_at DESC);
```

Partial STT text should normally stay ephemeral. Persist the final transcript unless product requirements explicitly require partial history.

---

# 18. Memory Tables

## 18.1 Memory items

```sql
CREATE TABLE memory_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    memory_type TEXT NOT NULL,
    subject TEXT,
    predicate TEXT,
    object_json JSONB,

    content TEXT NOT NULL,

    occurred_start_at TIMESTAMPTZ,
    occurred_end_at TIMESTAMPTZ,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,

    confidence REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0 AND confidence <= 1),

    salience REAL NOT NULL DEFAULT 0.5
        CHECK (salience >= 0 AND salience <= 1),

    source_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    supersedes_id UUID REFERENCES memory_items(id) ON DELETE SET NULL,

    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'deleted')),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    search_tsv TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(content, ''))
    ) STORED,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_memory_user_type
ON memory_items(user_id, memory_type);

CREATE INDEX idx_memory_user_occurred
ON memory_items(user_id, occurred_start_at DESC);

CREATE INDEX idx_memory_tsv
ON memory_items USING GIN(search_tsv);
```

---

## 18.2 Memory chunks + embeddings

BGE-M3 dense embeddings are 1024-dimensional.

```sql
CREATE TABLE memory_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id UUID NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_no INTEGER NOT NULL,
    content TEXT NOT NULL,

    embedding VECTOR(1024),

    search_tsv TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(content, ''))
    ) STORED,

    token_count INTEGER,
    embedding_model TEXT NOT NULL DEFAULT 'BAAI/bge-m3',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(memory_id, chunk_no)
);

CREATE INDEX idx_memory_chunks_user
ON memory_chunks(user_id);

CREATE INDEX idx_memory_chunks_tsv
ON memory_chunks USING GIN(search_tsv);

CREATE INDEX idx_memory_chunks_embedding_hnsw
ON memory_chunks USING hnsw (embedding vector_cosine_ops);
```

Every vector query **must include `user_id` ownership filtering**.

---

## 18.3 Entities

```sql
CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, entity_type, normalized_name)
);

CREATE INDEX idx_entities_user_name
ON entities(user_id, normalized_name);
```

---

## 18.4 Memory ↔ entity links

```sql
CREATE TABLE memory_entities (
    memory_id UUID NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    entity_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation TEXT,
    PRIMARY KEY(memory_id, entity_id)
);
```

This is enough for many personalization use cases; a graph database is not required initially.

---

# 19. Tasks and Reminders

## 19.1 Tasks

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    title TEXT NOT NULL,
    description TEXT,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'completed', 'cancelled')),

    priority TEXT NOT NULL DEFAULT 'normal',
    due_at TIMESTAMPTZ,
    timezone TEXT,

    source_turn_id UUID REFERENCES conversation_turns(id) ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_tasks_user_status
ON tasks(user_id, status);

CREATE INDEX idx_tasks_due
ON tasks(due_at)
WHERE status IN ('pending', 'in_progress');
```

## 19.2 Reminders

```sql
CREATE TABLE reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,

    title TEXT NOT NULL,
    body TEXT,

    trigger_at TIMESTAMPTZ NOT NULL,
    timezone TEXT NOT NULL,

    recurrence_rule TEXT,

    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'processing', 'sent', 'cancelled', 'failed')),

    delivery_channel TEXT NOT NULL DEFAULT 'push',

    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    sent_at TIMESTAMPTZ,
    failure_reason TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_reminders_due
ON reminders(trigger_at)
WHERE status = 'scheduled';

CREATE INDEX idx_reminders_user
ON reminders(user_id, trigger_at DESC);
```

### Reminder worker rule

PostgreSQL is the durable source of truth.

Workers can claim due reminders using a transactional pattern based on:

```sql
SELECT ...
FOR UPDATE SKIP LOCKED
```

This supports multiple workers without double-processing the same row.

The delivery operation must still be idempotent.

---

# 20. Tool Execution Audit

```sql
CREATE TABLE tool_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    turn_id UUID REFERENCES conversation_turns(id) ON DELETE SET NULL,

    tool_name TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,

    arguments JSONB NOT NULL,
    result JSONB,

    status TEXT NOT NULL
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),

    error_code TEXT,
    error_message TEXT,

    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(user_id, idempotency_key)
);

CREATE INDEX idx_tool_exec_turn ON tool_executions(turn_id);
CREATE INDEX idx_tool_exec_user_time ON tool_executions(user_id, created_at DESC);
```

Never place secrets/API keys inside `arguments` or `result`.

---

# 21. Model/Evaluation Audit

```sql
CREATE TABLE ai_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    session_id UUID REFERENCES voice_sessions(id) ON DELETE SET NULL,
    turn_id UUID REFERENCES conversation_turns(id) ON DELETE SET NULL,

    component TEXT NOT NULL,
    model TEXT NOT NULL,
    model_revision TEXT,

    latency_ms INTEGER,
    input_units INTEGER,
    output_units INTEGER,

    success BOOLEAN NOT NULL,
    error_code TEXT,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ai_requests_component_time
ON ai_requests(component, created_at DESC);
```

Do not put raw sensitive prompts/transcripts in general telemetry by default.

---

# 22. Redis Design

Redis should hold ephemeral data.

Suggested keys:

```text
voice:session:{session_id}
voice:session:{session_id}:active_response
voice:session:{session_id}:cancelled:{response_id}
voice:user:{user_id}:active_sessions
rate:user:{user_id}:{window}
cache:memory:{user_id}:{query_hash}
presence:device:{device_id}
```

Suggested uses:

```text
WebSocket session registry
active response/cancellation
short-lived conversation state
rate counters
distributed locks
cache
cross-instance events
```

For fire-and-forget realtime signaling, Redis Pub/Sub is appropriate.

For events that require persistence/replay/acknowledgment, use Redis Streams or a durable database/job queue.

Do **not** keep the only copy of a reminder in Redis Pub/Sub.

---

# 23. API Structure

Recommended REST prefix:

```text
/api/v1
```

## Auth

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/me
```

## Memory

```text
GET    /api/v1/memories
GET    /api/v1/memories/{id}
POST   /api/v1/memories
PATCH  /api/v1/memories/{id}
DELETE /api/v1/memories/{id}
POST   /api/v1/memories/search
DELETE /api/v1/memories
```

## Tasks

```text
GET    /api/v1/tasks
POST   /api/v1/tasks
PATCH  /api/v1/tasks/{id}
DELETE /api/v1/tasks/{id}
POST   /api/v1/tasks/{id}/complete
```

## Reminders

```text
GET    /api/v1/reminders
POST   /api/v1/reminders
PATCH  /api/v1/reminders/{id}
DELETE /api/v1/reminders/{id}
```

## Voice

```text
WS /v1/voice
```

## Health

```text
GET /health/live
GET /health/ready
```

Readiness should verify required dependencies rather than only returning a hard-coded `200`.

---

# 24. Example Full Flow — “What was the last time I visited Mumbai?”

```text
1. Microphone
      ↓
2. AEC / noise suppression
      ↓
3. openWakeWord
      ↓
4. Silero VAD
      ↓
5. PCM frames → WSS
      ↓
6. Voice Session Orchestrator
      ↓
7. Whisper Large-v3-Turbo
      ↓
"What was the last time I visited Mumbai?"
      ↓
8. Query/intent router
      ↓
9. Detect temporal structured-memory query
      ↓
10. Search:
    memory_type=event
    predicate=visited
    entity/place=Mumbai
    status=active
    ORDER BY occurred_start_at DESC
      ↓
11. If confidence sufficient:
       structured result
    Else/also:
       BGE-M3 + pgvector + FTS
       → RRF
       → BGE reranker-v2-m3
      ↓
12. Context → Qwen3.5-9B
      ↓
13. Response text
      ↓
14. phrase chunker
      ↓
15. Kokoro
      ↓
16. audio chunks → WSS
      ↓
17. Android jitter buffer/speaker
```

Example answer:

```text
"You last visited Mumbai from March 12 to March 16, 2026."
```

The LLM should phrase the answer, but the date should come from retrieved data rather than being invented by the model.

---

# 25. Example Tool Flow — “Remind me tomorrow to call Rahul”

```text
1. STT final transcript
      ↓
2. Qwen interprets intent
      ↓
3. Resolve "tomorrow" using user's timezone
      ↓
4. If no time was specified:
      follow product policy
      ├── ask for time
      └── or use an explicitly configured default
      ↓
5. LLM emits structured create_reminder tool call
      ↓
6. Pydantic validation
      ↓
7. authorization + idempotency
      ↓
8. INSERT reminder in PostgreSQL
      ↓
9. tool result returned to Qwen
      ↓
10. Qwen produces confirmation
      ↓
11. Kokoro speaks confirmation
```

Never let the LLM silently invent a clock time unless product requirements explicitly define a default behavior.

---

# 26. Development Environment

## 26.1 Required tools

Developer machine:

```text
Git
Node.js LTS
pnpm
React Native toolchain
Android Studio
JDK required by selected React Native version
Python 3.11/3.12 compatible with chosen AI packages
uv or Poetry
Docker Desktop / Docker Engine
PostgreSQL client
Redis client
CUDA toolkit/driver if local NVIDIA GPU is used
```

## 26.2 Local services

Use Docker Compose for:

```text
PostgreSQL + pgvector
Redis
FastAPI
workers
```

AI runtimes can be either Dockerized or run directly against the developer GPU.

## 26.3 LM Studio

Recommended local use:

```text
Qwen experimentation
embedding experimentation where the selected model/runtime is supported
OpenAI-compatible local API testing
tool-calling prototype work
```

Do not assume LM Studio is the single runtime for:

```text
openWakeWord
Silero VAD
Whisper Large-v3-Turbo
Kokoro
```

Run those through their appropriate mobile/server runtimes.

---

# 27. Environment Variables

Example `.env.example`:

```dotenv
APP_ENV=development
LOG_LEVEL=INFO

DATABASE_URL=postgresql+asyncpg://voice:voice@localhost:5432/voice_ai
REDIS_URL=redis://localhost:6379/0

JWT_ISSUER=voice-ai
JWT_AUDIENCE=voice-mobile
JWT_PRIVATE_KEY_PATH=./secrets/jwt-private.pem
JWT_PUBLIC_KEY_PATH=./secrets/jwt-public.pem
ACCESS_TOKEN_TTL_SECONDS=900
REFRESH_TOKEN_TTL_SECONDS=2592000

STT_BASE_URL=http://stt:8101
EMBEDDING_BASE_URL=http://embeddings:8102
RERANKER_BASE_URL=http://reranker:8103
LLM_BASE_URL=http://llm:8104/v1
TTS_BASE_URL=http://tts:8105

STT_MODEL=openai/whisper-large-v3-turbo
EMBEDDING_MODEL=BAAI/bge-m3
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
LLM_MODEL=Qwen/Qwen3.5-9B
TTS_MODEL=hexgrad/Kokoro-82M

VOICE_MAX_SESSION_SECONDS=1800
VOICE_MAX_TURN_SECONDS=120
VOICE_MAX_FRAME_BYTES=4096
VOICE_IDLE_TIMEOUT_SECONDS=90

MEMORY_ENABLED=true
MEMORY_RETRIEVAL_TOP_K=30
MEMORY_RERANK_TOP_K=8
```

Never commit real secrets to Git.

---

# 28. Development Plan — Phases and Approval Gates

No later phase should hide a failed early voice-latency assumption.

---

## Phase 0 — Technical proof of concept

### Goal

Prove the hardest uncertainty first:

```text
React Native Android
   +
continuous microphone
   +
openWakeWord
   +
Silero VAD
   +
AEC/NS
``` 

### Steps

- [ ] Create bare React Native Android project.
- [ ] Implement native microphone capture.
- [ ] Normalize audio to the model-required format.
- [ ] Add AEC/NS feature detection.
- [ ] Load openWakeWord-compatible ONNX/TFLite model assets.
- [ ] Run wake-word inference on a background/native worker thread.
- [ ] Load Silero VAD with ONNX Runtime.
- [ ] Add a ring buffer.
- [ ] Emit wake/VAD events to React Native.
- [ ] Test screen-off/background behavior required by the product.
- [ ] Measure battery/CPU.
- [ ] Test quiet room, TV/music, traffic, multiple voices, speaker playback.
- [ ] Measure false accepts and false rejects.
- [ ] Verify no audio gaps at wake transition.

### Gate

Do not proceed to a full product build until wake word + VAD + capture works reliably on representative Android hardware.

---


## Phase 1 — Repository and infrastructure foundation

### Steps

- [ ] Create monorepo structure.
- [ ] Configure TypeScript/linting/formatting.
- [ ] Create Python service environment.
- [ ] Add Docker Compose.
- [ ] Start PostgreSQL + pgvector.
- [ ] Start Redis.
- [ ] Add Alembic.
- [ ] Add base FastAPI project.
- [ ] Add health/readiness endpoints.
- [ ] Add structured logging.
- [ ] Add CI for lint/unit tests.
- [ ] Create `.env.example`.
- [ ] Create developer bootstrap script.

### Gate

A fresh developer machine can clone the repo, configure environment variables, run migrations, and start all non-GPU services predictably.

---

## Phase 2 — Authentication and user/device foundation

### Steps

- [ ] Create users/devices/auth_sessions migrations.
- [ ] Implement login/refresh/logout.
- [ ] Store mobile tokens securely using Android secure storage.
- [ ] Register device.
- [ ] Revoke device/session support.
- [ ] Add per-user and per-device authorization checks.
- [ ] Add audit logging for security-relevant events.

### Gate

One user's token cannot access another user's memory, tasks, sessions, or WebSocket.

---

## Phase 3 — WebSocket voice gateway

### Steps

- [ ] Implement `/v1/voice`.
- [ ] Authenticate during connection establishment.
- [ ] Implement heartbeat.
- [ ] Implement frame-size limits.
- [ ] Implement max session/turn duration.
- [ ] Implement sequence numbers.
- [ ] Implement binary PCM frame handling.
- [ ] Implement session/turn/response IDs.
- [ ] Persist session + final turn metadata.
- [ ] Add Redis active-session registry.
- [ ] Handle reconnect and disconnect cleanly.
- [ ] Add cancellation events.

### Gate

A mobile test client can stream PCM to the backend for repeated turns without memory growth, stuck sessions, or stale response delivery.

---

## Phase 4 — STT

### Steps

- [ ] Create isolated STT service.
- [ ] Load Whisper Large-v3-Turbo.
- [ ] Integrate faster-whisper/CTranslate2.
- [ ] Accept streaming audio buffers.
- [ ] Emit partial text.
- [ ] Emit final text on VAD end.
- [ ] Add language handling.
- [ ] Add cancellation.
- [ ] Add per-turn latency metrics.
- [ ] Build a noisy-audio evaluation set.

### Gate

Measure:

```text
speech-end → final transcript latency
word error rate on target languages/accents
GPU VRAM
concurrent streams
failure recovery
```

Do not approve based only on one clean English microphone test.

---

## Phase 5 — LLM orchestration

### Steps

- [ ] Deploy Qwen3.5-9B with vLLM.
- [ ] Pin model revision/runtime versions.
- [ ] Configure reasoning parser.
- [ ] Configure tool-call parser.
- [ ] Define system instructions.
- [ ] Define Pydantic structured outputs.
- [ ] Implement cancellation.
- [ ] Stream text deltas.
- [ ] Add prompt/context token budgets.
- [ ] Add model timeout/retry policy.
- [ ] Add response safety/policy layer appropriate to the product.
- [ ] Build LLM evaluation fixtures.

### Gate

Qwen passes the defined tool-routing and structured-output tests without malformed or unauthorized actions.

---

## Phase 6 — Long-term memory + hybrid RAG

### Steps

- [ ] Create memory tables.
- [ ] Create BGE-M3 embedding service.
- [ ] Add HNSW pgvector index.
- [ ] Add PostgreSQL FTS.
- [ ] Implement structured memory queries.
- [ ] Implement semantic retrieval.
- [ ] Implement keyword retrieval.
- [ ] Implement RRF/hybrid merge.
- [ ] Deploy `bge-reranker-v2-m3`.
- [ ] Add reranking.
- [ ] Implement memory extraction.
- [ ] Implement memory deduplication.
- [ ] Implement superseding/conflict logic.
- [ ] Add memory view/edit/delete APIs.
- [ ] Add “forget” tool.
- [ ] Add memory-disabled mode.

### Gate

Test at minimum:

```text
latest event
oldest event
preference change
person relationship
exact-name lookup
semantic paraphrase
conflicting memory
deleted memory
cross-user isolation
no-result query
```

---

## Phase 7 — Tool engine + reminders/tasks

### Steps

- [ ] Build Tool Registry.
- [ ] Define strict Pydantic schemas.
- [ ] Implement permission checks.
- [ ] Implement idempotency.
- [ ] Add tool execution audit.
- [ ] Create tasks/reminders tables.
- [ ] Implement reminder tools.
- [ ] Implement task tools.
- [ ] Build durable reminder worker.
- [ ] Add timezone handling.
- [ ] Add recurrence handling only after one-shot reminders are correct.
- [ ] Add mobile push delivery.
- [ ] Add retry/dead-letter/failure strategy.

### Gate

Repeated/replayed tool calls cannot create duplicate reminders, and a service restart does not lose scheduled reminders.

---

## Phase 8 — TTS

### Steps

- [ ] Create Kokoro service.
- [ ] Select/test target voices.
- [ ] Implement phrase/sentence segmentation.
- [ ] Generate first playable audio chunk quickly.
- [ ] Stream binary audio.
- [ ] Implement client jitter buffer.
- [ ] Implement response-id filtering.
- [ ] Implement TTS cancellation.
- [ ] Prevent unconfirmed tool statements from being spoken.
- [ ] Measure real-time factor and first-audio latency.

### Gate

The assistant can begin speaking promptly, continue smoothly, and stop essentially immediately when the user interrupts.

---

## Phase 9 — Full barge-in

### Steps

- [ ] Keep microphone active during TTS.
- [ ] Enable AEC where available.
- [ ] Enable NS where available.
- [ ] Tune Silero VAD for playback conditions.
- [ ] Detect sustained user speech.
- [ ] Stop local TTS playback first.
- [ ] Send `client.response.cancel`.
- [ ] Cancel server response.
- [ ] Start new turn.
- [ ] Ignore stale chunks.
- [ ] Test with loudspeaker volume at several levels.

### Gate

Assistant playback does not repeatedly trigger false barge-in, and real user interruptions are detected reliably.

---

## Phase 10 — Security and privacy hardening

### Steps

- [ ] TLS everywhere externally.
- [ ] Private/internal networking for AI services.
- [ ] Strong JWT/refresh token handling.
- [ ] Secret manager for production.
- [ ] Per-user DB authorization rules in repository layer.
- [ ] Rate limits.
- [ ] WebSocket message limits.
- [ ] SQL parameterization.
- [ ] Tool allow-list.
- [ ] SSRF controls for future URL tools.
- [ ] Data deletion workflow.
- [ ] Retention policy.
- [ ] Log redaction.
- [ ] Audio retention disabled by default unless required.
- [ ] Backup/restore testing.
- [ ] Dependency/container scanning.

### Gate

Security review passes and deletion/retention behavior is demonstrably implemented.

---

## Phase 11 — Observability and performance

Instrument each turn:

```text
wakeword_detected_at
speech_started_at
speech_ended_at
stt_first_partial_at
stt_final_at
retrieval_started_at
retrieval_completed_at
llm_request_at
llm_first_token_at
tool_started_at
tool_completed_at
tts_request_at
tts_first_audio_at
playback_started_at
turn_completed_at
```

Compute:

```text
speech-end → STT final
STT final → LLM first token
tool latency
LLM first token → TTS first audio
speech-end → speaker first audio
total turn latency
cancellation latency
```

Add:

- [ ] OpenTelemetry traces.
- [ ] Prometheus-style metrics.
- [ ] GPU metrics.
- [ ] PostgreSQL query metrics.
- [ ] Redis metrics.
- [ ] structured error logs.
- [ ] alerting for service failure.
- [ ] capacity dashboard.

### Gate

Every slow response can be attributed to a specific stage rather than guessed from user complaints.

---

## Phase 12 — Load, resilience, and production release

### Test

- [ ] multiple concurrent WebSockets
- [ ] repeated connect/disconnect
- [ ] mobile network change
- [ ] packet delay
- [ ] temporary STT outage
- [ ] temporary LLM outage
- [ ] TTS outage
- [ ] Redis restart
- [ ] API restart
- [ ] reminder worker restart
- [ ] PostgreSQL fail/recovery procedure
- [ ] GPU OOM behavior
- [ ] client app background/foreground
- [ ] stale response after reconnect
- [ ] duplicate tool request replay
- [ ] long silence
- [ ] very long utterance
- [ ] malformed WebSocket event
- [ ] unauthorized user/device

### Release gate

Production is ready only when the product has explicit targets and passing measurements for:

```text
wake-word quality
STT accuracy
end-to-end latency
barge-in latency
tool correctness
memory retrieval correctness
crash-free sessions
GPU concurrency
database recovery
security/privacy
```

---

# 29. Development Order — The Short Version

Build in this order:

```text
1. React Native + native microphone
2. AEC/NS
3. openWakeWord
4. Silero VAD
5. WebSocket
6. FastAPI session orchestration
7. Whisper STT
8. Qwen basic response
9. Kokoro TTS
10. interruption/cancellation
11. PostgreSQL conversation storage
12. BGE-M3 + pgvector + FTS
13. reranker
14. structured memory
15. task/reminder tools
16. background workers
17. security/privacy hardening
18. observability
19. load/latency optimization
20. production rollout
```

This order gets a real voice loop working before investing heavily in memory or integrations.

---

# 30. Local Development Topology

Corrected local setup:

```text
React Native Android
      │
      ├── openWakeWord (on device)
      ├── Silero VAD (on device)
      └── WebSocket
             │
             ▼
        FastAPI local
             │
   ┌─────────┼──────────────┐
   ▼         ▼              ▼
Whisper    Qwen          Kokoro
service   LM Studio       service
or CT2    OR local vLLM
             │
             ▼
       embedding service
       (BGE-M3)
             │
             ▼
 PostgreSQL + pgvector
             │
           Redis
```

LM Studio can be swapped with local vLLM for closer production parity.

For the final stages, prefer running the same runtime family as production so latency and tool behavior are representative.

---

# 31. Production Topology

```text
                             Internet
                                │
                                ▼
                         Cloudflare Edge
                                │
                      ┌─────────┴─────────┐
                      ▼                   ▼
              api.example.com     voice.example.com
                      │                   │
                      └─────────┬─────────┘
                                ▼
                       FastAPI instances
                                │
                  ┌─────────────┼──────────────┐
                  ▼             ▼              ▼
             PostgreSQL       Redis        GPU services
                                                │
                      ┌──────────┬──────────────┼───────────┐
                      ▼          ▼              ▼           ▼
                    STT       Qwen/vLLM      BGE/Rerank   Kokoro
```

Keep GPU service ports private.

---

# 32. Cloudflare / Origin Security

Recommended:

```text
Internet
  ↓
Cloudflare
  ↓
origin firewall / tunnel / allowlisted access
  ↓
FastAPI
```

Do not expose model-service ports publicly.

Suggested model services:

```text
8101 STT
8102 embeddings
8103 reranker
8104 LLM
8105 TTS
```

These should be reachable only from trusted application infrastructure.

---

# 33. Database Query Example — Latest Mumbai Visit

Illustrative query:

```sql
SELECT
    id,
    content,
    occurred_start_at,
    occurred_end_at,
    confidence,
    object_json
FROM memory_items
WHERE user_id = :user_id
  AND status = 'active'
  AND memory_type = 'event'
  AND predicate = 'visited'
  AND lower(object_json->>'place') = lower(:place)
ORDER BY occurred_start_at DESC NULLS LAST
LIMIT 1;
```

If entity normalization is used:

```text
"Mumbai"
"Bombay"
"Mumbai, Maharashtra"
```

can resolve to the same canonical location entity before querying.

---

# 34. Vector Retrieval Example

Conceptually:

```sql
SELECT
    id,
    memory_id,
    content,
    1 - (embedding <=> :query_embedding) AS cosine_similarity
FROM memory_chunks
WHERE user_id = :user_id
ORDER BY embedding <=> :query_embedding
LIMIT 30;
```

Always validate the pgvector operator/index combination against the installed pgvector version and chosen distance metric.

---

# 35. FTS Retrieval Example

```sql
SELECT
    id,
    memory_id,
    content,
    ts_rank_cd(search_tsv, websearch_to_tsquery('simple', :query)) AS rank
FROM memory_chunks
WHERE user_id = :user_id
  AND search_tsv @@ websearch_to_tsquery('simple', :query)
ORDER BY rank DESC
LIMIT 30;
```

Then merge FTS and vector candidates by rank rather than directly adding incompatible score scales.

---

# 36. Prompt Context Budget

Do not send the user's entire history on every turn.

Construct context:

```text
system instructions
+
current user profile essentials
+
recent conversation window
+
structured memory results
+
top reranked semantic memories
+
active task/tool state
+
current user turn
```

Use hard budgets.

Example policy:

```text
recent turns: bounded
memory results: top N
long memory content: summarized/chunked
tool results: only relevant fields
```

This improves both latency and reliability.

---

# 37. Memory Quality Rules

Store a candidate long-term memory only if it is likely useful later.

Good candidates:

```text
stable preference
important relationship
explicitly stated fact
important event
project context
recurring routine
user instruction intended for future conversations
```

Poor candidates:

```text
filler words
temporary ASR errors
assistant speculation
low-confidence inference
every sentence in casual conversation
```

The memory extractor should distinguish:

```text
user said X
```

from:

```text
assistant guessed X
```

Only trusted sources should become factual personal memory.

---

# 38. Reminder Reliability Rules

A voice confirmation must reflect actual state.

Wrong:

```text
LLM decides to create reminder
↓
assistant immediately says "Done"
↓
database insert fails
```

Correct:

```text
LLM requests tool
↓
tool validates
↓
database commits
↓
tool returns success
↓
assistant says "Done"
```

Use a unique delivery/event ID so push retries do not create duplicate user-visible reminders where the notification provider supports deduplication.

---

# 39. Background Jobs

Initial workers:

## Reminder worker

```text
poll/claim due reminders
deliver
record success/failure
schedule next recurrence if applicable
```

## Memory worker

```text
receive completed conversation
extract candidates
deduplicate
store
embed
```

## Cleanup worker

```text
expire temporary session data
delete temporary audio if any
clean revoked sessions
apply retention policy
```

If workloads grow, move job dispatch to Redis Streams or a dedicated queue while keeping durable user records in PostgreSQL.

---

# 40. Error Handling

Every internal dependency needs a clear failure mode.

## STT down

```text
do not send empty text to LLM
tell client voice transcription is unavailable
allow retry
```

## LLM down

```text
do not lose final transcript
return temporary service error
```

## Memory service down

For non-memory-critical conversation:

```text
continue with recent context where safe
mark memory retrieval degraded
```

For a direct memory question:

```text
state that memory lookup is currently unavailable
do not invent an answer
```

## Tool fails

```text
return tool error to LLM
LLM communicates failure honestly
```

## TTS fails

```text
send/display text response
do not fail the completed reasoning/tool action
```

---

# 41. Security Boundaries

## Mobile

```text
no server API secrets in app
short-lived access token
refresh token in secure storage
certificate/TLS validation
signed app release
```

## Edge

```text
Cloudflare TLS
DDoS
WAF
connection-level rate rules
bot/abuse controls where appropriate
```

## API

```text
auth
authorization
input validation
message limits
tool policy
rate limiting
idempotency
audit
```

## Internal AI network

```text
not public
service authentication if crossing hosts
network ACL/firewall
```

## Database

```text
least-privilege DB users
encrypted backups
regular restore tests
user ownership filters
no raw secret storage
```

---

# 42. Privacy / Personal Memory

Because this architecture intentionally stores personal memory, define this before release:

```text
what gets remembered
what never gets remembered automatically
audio retention
transcript retention
memory retention
how users inspect memory
how users correct memory
how users delete memory
account deletion
backup deletion policy
analytics redaction
```

Recommended baseline:

```text
raw microphone audio: not permanently stored by default
partial transcript: ephemeral
final transcript: stored only according to product policy
long-term memory: explicit, inspectable, deletable
```

---

# 43. Performance Targets to Define

Do not use “low latency” as the only requirement.

Create measured targets for:

```text
wake-word detection latency
VAD speech-start latency
VAD speech-end latency
network uplink delay
STT finalization latency
retrieval latency
LLM time-to-first-token
tool latency
TTS time-to-first-audio
client playback buffer delay
barge-in stop latency
total speech-end → first assistant audio
```

Also track percentiles:

```text
P50
P95
P99
```

Averages alone hide bad realtime UX.

---

# 44. Model Benchmark Matrix

Before senior production sign-off, benchmark on the actual target GPU.

| Component | Candidate | Quality metric | Latency metric | Resource metric |
|---|---|---|---|---|
| STT | Whisper large-v3-turbo | WER / target-language accuracy | speech-end → final | VRAM/concurrency |
| LLM | Qwen3.5-9B | eval pass rate | TTFT + tokens/s | VRAM/concurrency |
| LLM upgrade | Qwen3.6-27B | same eval suite | TTFT + tokens/s | VRAM/concurrency |
| Embedding | BGE-M3 | retrieval recall@K | embed latency | GPU/CPU |
| Reranker | bge-reranker-v2-m3 | nDCG/MRR or task accuracy | rerank latency | GPU/CPU |
| TTS | Kokoro-82M | human MOS-style test | first-audio + RTF | GPU/CPU |
| Wake word | openWakeWord | FAR/FRR | detection latency | battery/CPU |
| VAD | Silero VAD | miss/false trigger | endpoint latency | battery/CPU |

---

# 45. Required Test Sets

Create and version a real project evaluation dataset.

## Wake-word set

```text
target phrase
near-sounding phrases
background television
music
car
street
different distances
male/female voices
different accents
speaker output active
```

## STT set

```text
short commands
names
cities
dates
times
numbers
noisy environment
accented speech
mixed-language speech if required
```

## Memory set

```text
latest visit
oldest fact
preference update
relationship
contradiction
deleted memory
entity alias
exact keyword
semantic paraphrase
```

## Tool set

```text
create reminder
change reminder
cancel reminder
ambiguous time
relative date
duplicate replay
unauthorized tool
tool failure
```

## Conversation set

```text
normal question
follow-up
interruption
correction
topic switch
very short utterance
very long utterance
silence
```

---

# 46. Barge-In Acceptance Test

Test:

```text
Assistant speaks:
"Your next appointment is tomorrow at..."

User interrupts:
"No, what about Friday?"
```

Expected:

```text
1. microphone sees user speech
2. AEC suppresses assistant speaker signal as much as device supports
3. VAD detects user
4. app stops playback immediately
5. app sends cancel(response_id=A)
6. server cancels remaining LLM/TTS for A
7. new turn B is created
8. STT processes "No, what about Friday?"
9. assistant answers turn B
10. any late audio for A is discarded by client
```

This must be tested with packet delay and a deliberately slow TTS service to prove stale response protection.

---

# 47. Scaling Strategy

## Stage A

```text
one API instance
one PostgreSQL
one Redis
one GPU server
```

## Stage B

```text
multiple FastAPI instances
Redis coordinates ephemeral session state
shared PostgreSQL
GPU service replicas
```

## Stage C

Scale each bottleneck independently:

```text
more STT replicas if speech transcription saturates
more LLM replicas if reasoning queue grows
more TTS replicas if audio synthesis queues
read replicas only if DB reads justify them
connection-aware load balancing
```

Do not prematurely introduce Kafka/Kubernetes solely because the architecture is “AI”.

Measure first.

---

# 48. CI/CD

Recommended pipelines:

## Pull request

```text
TypeScript lint
React Native unit tests
Python lint/type checks
Python unit tests
DB migration validation
contract tests
security/dependency scan
```

## Main branch

```text
build service images
tag with Git SHA
integration tests
GPU smoke tests where infrastructure exists
publish immutable images
```

## Deployment

```text
backup/check DB
apply forward-compatible migration
deploy workers/services
health/readiness check
canary/small traffic
observe metrics
complete rollout
```

Model artifacts should also be versioned/pinned.

---

# 49. Database Migration Rules

Use Alembic.

Rules:

```text
never edit a migration already applied to production
use additive/backward-compatible changes where possible
deploy code that understands old+new schema during transition
create large indexes carefully
backfill in batches
take backups
test downgrade/forward recovery where appropriate
```

For vector index changes, benchmark build time and query behavior on production-sized data before rollout.

---

# 50. Observability Event Example

Structured log:

```json
{
  "event": "voice.turn.completed",
  "session_id": "vs_...",
  "turn_id": "turn_...",
  "response_id": "resp_...",
  "stt_ms": 410,
  "retrieval_ms": 74,
  "llm_ttft_ms": 210,
  "tts_first_audio_ms": 145,
  "speech_end_to_audio_ms": 839,
  "interrupted": false,
  "stt_model": "openai/whisper-large-v3-turbo",
  "llm_model": "Qwen/Qwen3.5-9B"
}
```

Avoid putting full private conversation text into general logs.

---

# 51. Important Architecture Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| openWakeWord mobile integration | Project is not simply a React Native wake-word npm package | Prove ONNX/TFLite/native integration in Phase 0 |
| Speaker echo triggers VAD | Breaks barge-in | AEC + NS + device testing + thresholds |
| Whisper streaming latency | Whisper is not natively designed as a realtime streaming protocol | faster-whisper + incremental buffering + benchmark |
| LLM tool hallucination | Could create wrong actions | strict schemas + tool registry + authorization |
| Redis reminder loss | Pub/Sub/cache is not durable task storage | PostgreSQL source of truth |
| Vector-only memory | Poor for precise temporal facts | structured memory first |
| Cross-user RAG leakage | Serious privacy problem | mandatory user_id filtering + tests |
| Stale TTS after interrupt | Voice UX becomes confusing | response_id + cancellation + client stale-frame rejection |
| Cloudflare WAF assumption | WebSocket messages are not continuously WAF-inspected | backend frame/session controls |
| LM Studio dependency | Dev convenience can diverge from production | use production-like runtimes before release |
| GPU contention | STT/LLM/TTS compete for VRAM | profiling + service isolation/replicas |
| Model-version drift | Behavior changes unexpectedly | pin model/runtime revisions |

---

# 52. Changes From the Original Proposal

## Keep exactly

```text
React Native
openWakeWord
Silero VAD
WebSocket
Cloudflare
FastAPI
Whisper Large-v3-Turbo
BGE-M3
pgvector
PostgreSQL FTS
Qwen3.5-9B baseline
Kokoro
PostgreSQL
Redis
vLLM for Qwen
```

## Modify

Original:

```text
BGE Reranker Base
```

Recommended:

```text
BAAI/bge-reranker-v2-m3
```

Original:

```text
LM Studio
 ├ Whisper
 ├ BGE
 ├ Qwen
 └ Kokoro
```

Recommended:

```text
LM Studio → Qwen/embedding experimentation where supported

Separate runtime:
Whisper → faster-whisper/CTranslate2
Kokoro → dedicated Python TTS service
mobile wake/VAD → ONNX/TFLite/native runtime
```

## Add

```text
AEC / Noise Suppression
Ring buffer
Voice Session Orchestrator
session_id / turn_id / response_id
Cancellation propagation
Structured temporal memory
Memory extraction pipeline
Tool idempotency
Durable reminder worker
Observability
Privacy controls
```

---

# 53. Final Architecture in One Line

> **React Native Android → native AudioRecord + AEC/NS + openWakeWord + Silero VAD → WSS → Cloudflare → FastAPI Voice Gateway → Session Orchestrator → Whisper Large-v3-Turbo/faster-whisper → structured memory + BGE-M3 + pgvector + PostgreSQL FTS + BGE reranker-v2-m3 → Qwen3.5-9B/vLLM → validated Tool Engine → Kokoro streaming chunks → WSS → React Native speaker, with PostgreSQL as durable state and Redis as ephemeral coordination.**

---

# 54. Senior Approval Checklist

Before approving implementation:

- [ ] React Native is confirmed as UI/client framework.
- [ ] Native Android audio layer is accepted.
- [ ] openWakeWord Android inference proof-of-concept passes.
- [ ] Silero VAD Android inference proof-of-concept passes.
- [ ] AEC/NS requirement is accepted.
- [ ] WebSocket protocol and cancellation identifiers are accepted.
- [ ] Whisper runtime is separated from vLLM.
- [ ] Qwen3.5-9B is accepted as the initial latency baseline.
- [ ] Model upgrades are benchmark-driven.
- [ ] BGE-M3 + pgvector + FTS hybrid retrieval is accepted.
- [ ] `bge-reranker-v2-m3` is accepted as the v1 reranker.
- [ ] Structured memory is accepted for dates/events/facts.
- [ ] PostgreSQL is the durable source of truth.
- [ ] Redis is not used as the only durable reminder store.
- [ ] Tool calls require schemas, authorization, and idempotency.
- [ ] Users can inspect/delete long-term memory.
- [ ] Audio retention policy is defined.
- [ ] End-to-end latency targets are defined.
- [ ] Barge-in test is a release requirement.
- [ ] Observability is implemented before production load testing.
- [ ] Model/runtime versions are pinned for production.

---

# 55. Official / Primary References

Checked while reviewing this architecture on **2026-08-21**.

## React Native / on-device inference

- React Native Native Platform / native modules:  
  https://reactnative.dev/docs/native-platform

- ONNX Runtime for React Native:  
  https://onnxruntime.ai/docs/get-started/with-javascript/react-native.html

- ONNX Runtime Mobile:  
  https://onnxruntime.ai/docs/get-started/with-mobile.html

## Android audio

- Android AcousticEchoCanceler:  
  https://developer.android.com/reference/android/media/audiofx/AcousticEchoCanceler

- Android NoiseSuppressor:  
  https://developer.android.com/reference/kotlin/android/media/audiofx/NoiseSuppressor

- Android MediaRecorder AudioSource:  
  https://developer.android.com/reference/android/media/MediaRecorder.AudioSource.html

## Wake word / VAD

- openWakeWord:  
  https://github.com/dscripka/openWakeWord

- Silero VAD:  
  https://github.com/snakers4/silero-vad

## STT

- OpenAI Whisper Large-v3-Turbo model card:  
  https://huggingface.co/openai/whisper-large-v3-turbo

- faster-whisper:  
  https://github.com/SYSTRAN/faster-whisper

## Embeddings / reranking

- BGE-M3:  
  https://huggingface.co/BAAI/bge-m3

- BGE reranker v2 m3:  
  https://huggingface.co/BAAI/bge-reranker-v2-m3

## Vector search / database

- pgvector:  
  https://github.com/pgvector/pgvector

- PostgreSQL Full Text Search:  
  https://www.postgresql.org/docs/current/textsearch.html

## Reasoning / inference

- Qwen3.5-9B:  
  https://huggingface.co/Qwen/Qwen3.5-9B

- Qwen3.6-27B:  
  https://huggingface.co/Qwen/Qwen3.6-27B

- vLLM tool calling:  
  https://docs.vllm.ai/en/stable/features/tool_calling/

- vLLM structured outputs:  
  https://docs.vllm.ai/en/stable/features/structured_outputs/

## TTS

- Kokoro repository:  
  https://github.com/hexgrad/kokoro

- Kokoro-82M model:  
  https://huggingface.co/hexgrad/Kokoro-82M

## Backend / realtime transport

- FastAPI WebSockets:  
  https://fastapi.tiangolo.com/advanced/websockets/

- Cloudflare WebSockets:  
  https://developers.cloudflare.com/network/websockets/

## Redis

- Redis docs:  
  https://redis.io/docs/latest/

- Redis Pub/Sub:  
  https://redis.io/docs/latest/develop/use-cases/pub-sub/

- Redis Streams:  
  https://redis.io/docs/latest/develop/data-types/streams/

## Development

- LM Studio developer documentation:  
  https://lmstudio.ai/docs/developer

- LM Studio embeddings:  
  https://lmstudio.ai/docs/developer/openai-compat/embeddings

---

# 56. Final Recommendation

The architecture should be implemented as a **voice system first, AI assistant second**.

The most dangerous mistake would be to spend months building memory, agents, and tool integrations before proving:

```text
wake
→ listen
→ accurately detect end of speech
→ transcribe
→ reason
→ synthesize
→ play
→ interrupt cleanly
```

Therefore the implementation priority is:

```text
VOICE LOOP RELIABILITY
        ↓
LATENCY + BARGE-IN
        ↓
MEMORY
        ↓
TOOLS
        ↓
SCALING
```

If the Phase 0 mobile audio proof-of-concept and the first end-to-end voice loop pass, the rest of this architecture can be expanded without redesigning the React Native application.

---

**End of document**
