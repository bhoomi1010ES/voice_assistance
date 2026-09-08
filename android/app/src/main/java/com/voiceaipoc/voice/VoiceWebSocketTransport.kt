package com.voiceaipoc.voice

import android.os.SystemClock
import android.util.Log
import com.voiceaipoc.auth.AuthTokenStorage
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.TimeZone

/**
 * Native Phase 3 voice gateway transport.
 *
 * Network work runs outside AudioRecord and the PCM callback only performs a
 * bounded copy. The listener exposes status and bounded protocol/transcript
 * metadata only.
 * Tokens are read from AuthTokenStorage and are never included in callbacks
 * or logs.
 */
class VoiceWebSocketTransport(
    private val tokenStorage: AuthTokenStorage,
    private val listener: Listener,
    private val client: OkHttpClient = OkHttpClient(),
    private val networkExecutor: ExecutorService = Executors.newSingleThreadExecutor {
        Thread(it, "VoiceAI-VoiceGateway")
    },
    private val sendQueue: PcmSendQueue = PcmSendQueue(),
    private val heartbeatScheduler: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor {
        Thread(it, "VoiceAI-VoiceHeartbeat")
    },
) {
    enum class State {
        DISCONNECTED,
        CONNECTING,
        CONNECTED,
        SESSION_STARTING,
        SESSION_READY,
        TURN_STARTING,
        STREAMING_AUDIO,
        CLOSING,
        ERROR,
    }

    data class Status(
        val state: State = State.DISCONNECTED,
        val connected: Boolean = false,
        val sessionStarted: Boolean = false,
        val turnActive: Boolean = false,
        val sessionId: String? = null,
        val turnId: String? = null,
        val responseId: String? = null,
        val framesQueued: Int = 0,
        val queueHighWaterMark: Int = 0,
        val droppedFrames: Long = 0,
        val invalidFrames: Long = 0,
        val framesSent: Long = 0,
        val bytesSent: Long = 0,
        val websocketErrorCount: Long = 0,
        val lastServerEvent: String? = null,
        val lastServerEventTimestampMs: Long = 0,
        val lastError: String? = null,
    )

    data class ServerEventPayload(
        val text: String? = null,
        val delta: String? = null,
        val isFinal: Boolean? = null,
        val sequence: Long? = null,
        val attempt: Long? = null,
        val transcriptSequence: Long? = null,
        val language: String? = null,
        val audioDurationMs: Long? = null,
        val metrics: Map<String, Double?> = emptyMap(),
        val usage: Map<String, Double?> = emptyMap(),
        val toolCallId: String? = null,
        val toolName: String? = null,
        val toolStatus: String? = null,
        val confirmationId: String? = null,
        val errorCode: String? = null,
        val retryable: Boolean? = null,
    )

    interface Listener {
        fun onStatus(status: Status)

        fun onServerEvent(
            eventType: String,
            sessionId: String?,
            turnId: String?,
            responseId: String?,
        )

        fun onServerEvent(
            eventType: String,
            sessionId: String?,
            turnId: String?,
            responseId: String?,
            eventId: String?,
            timestampMs: Long?,
        ) {
            onServerEvent(eventType, sessionId, turnId, responseId)
        }

        fun onServerEvent(
            eventType: String,
            sessionId: String?,
            turnId: String?,
            responseId: String?,
            eventId: String?,
            timestampMs: Long?,
            payload: ServerEventPayload?,
        ) {
            onServerEvent(eventType, sessionId, turnId, responseId, eventId, timestampMs)
        }
    }

    private val stateLock = Any()
    private val drainScheduled = AtomicBoolean(false)
    private var webSocket: WebSocket? = null
    private var heartbeatTask: ScheduledFuture<*>? = null
    private var status = Status()
    private var turnFrameCount = 0L
    private var turnByteCount = 0L

    fun connect(url: String): Result {
        if (!url.startsWith("ws://") && !url.startsWith("wss://")) {
            return fail("E_VOICE_URL", "Voice gateway URL must use ws:// or wss://.")
        }

        // Reject missing credentials before changing the transport into a
        // connecting state. This keeps the public connect result truthful and
        // guarantees that an unauthenticated attempt never reaches OkHttp.
        val accessToken = try {
            tokenStorage.read()?.accessToken
        } catch (_: RuntimeException) {
            null
        }
        if (accessToken.isNullOrBlank()) {
            return fail("E_VOICE_AUTH", "No access token is stored securely on this device.")
        }

        val socketToCancel: WebSocket?
        synchronized(stateLock) {
            if (status.state in setOf(
                    State.CONNECTING,
                    State.CONNECTED,
                    State.SESSION_STARTING,
                    State.SESSION_READY,
                    State.TURN_STARTING,
                    State.STREAMING_AUDIO,
                )
            ) {
                return Result(true)
            }
            socketToCancel = webSocket
            webSocket = null
            status = status.copy(
                state = State.CONNECTING,
                connected = false,
                sessionStarted = false,
                turnActive = false,
                sessionId = null,
                turnId = null,
                responseId = null,
                lastError = null,
            )
        }
        socketToCancel?.cancel()
        Log.i(
            TAG,
            "VOICE connect requested wallMs=${System.currentTimeMillis()} " +
                "elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        notifyStatus()

        networkExecutor.execute {
            val request = Request.Builder()
                .url(url)
                .header("Authorization", "Bearer $accessToken")
                .build()
            val candidate = client.newWebSocket(request, socketListener)
            val accepted = synchronized(stateLock) {
                if (status.state == State.CONNECTING && webSocket == null) {
                    webSocket = candidate
                    true
                } else {
                    false
                }
            }
            if (!accepted) {
                candidate.cancel()
            }
        }
        return Result(true)
    }

    fun disconnect() {
        stopHeartbeat()
        val socketToClose: WebSocket?
        synchronized(stateLock) {
            if (status.state == State.DISCONNECTED) return
            socketToClose = webSocket
            status = status.copy(state = State.CLOSING, connected = false, turnActive = false)
        }
        sendQueue.clear()
        notifyStatus()
        socketToClose?.close(1000, "client_disconnect")
    }

    fun startSession(resumeSessionId: String? = null): Result {
        synchronized(stateLock) {
            if (status.state != State.CONNECTED) {
                return Result(false, "E_VOICE_STATE", "Connect to the voice gateway first.")
            }
            status = status.copy(state = State.SESSION_STARTING)
        }
        Log.i(
            TAG,
            "VOICE session start requested resume=${resumeSessionId != null} " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        notifyStatus()
        val message = JSONObject()
            .put("type", "client.session.start")
            .put("protocol_version", 1)
            .put(
                "audio",
                JSONObject()
                    .put("sample_rate_hz", 16000)
                    .put("channels", 1)
                    .put("frame_samples", PcmSendQueue.FRAME_SAMPLES)
                    .put("frame_bytes", PcmSendQueue.FRAME_SAMPLES * PcmSendQueue.BYTES_PER_SAMPLE),
            )
            .put(
                "client_metadata",
                JSONObject()
                    .put("platform", "android")
                    .put("client_version", "phase3-native")
                    .put("timezone", TimeZone.getDefault().id),
            )
            .put("stt", JSONObject().put("enabled", true))
        if (resumeSessionId != null) message.put("resume_session_id", resumeSessionId)
        postControl(message)
        return Result(true)
    }

    fun startTurn(clientTurnId: String? = null): Result {
        synchronized(stateLock) {
            if (status.state != State.SESSION_READY) {
                return Result(false, "E_VOICE_STATE", "Start a voice session first.")
            }
            sendQueue.clear()
            sendQueue.resetSequence()
            turnFrameCount = 0
            turnByteCount = 0
            status = status.copy(state = State.TURN_STARTING, turnActive = true)
        }
        Log.i(
            TAG,
            "VOICE turn start requested clientTurnId=${clientTurnId ?: "NONE"} " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        notifyStatus()
        val message = JSONObject().put("type", "client.turn.start")
        if (clientTurnId != null) message.put("client_turn_id", clientTurnId)
        postControl(message)
        return Result(true)
    }

    /** Called from the native PCM consumer; never blocks on network I/O. */
    fun offerPcmFrame(buffer: ShortArray, samplesRead: Int): Boolean {
        synchronized(stateLock) {
            if (!status.turnActive || status.state !in setOf(State.TURN_STARTING, State.STREAMING_AUDIO)) {
                return false
            }
        }
        val accepted = sendQueue.offer(buffer, samplesRead, SystemClock.elapsedRealtime())
        if (accepted) scheduleDrain()
        notifyStatus()
        return accepted
    }

    fun commitAudio(durationMs: Int): Result {
        synchronized(stateLock) {
            if (!status.turnActive) {
                return Result(false, "E_VOICE_STATE", "No active voice turn.")
            }
            status = status.copy(turnActive = false)
        }
        Log.i(
            TAG,
            "VOICE audio commit requested durationMs=$durationMs " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        notifyStatus()
        networkExecutor.execute {
            drainQueue()
            val committed = synchronized(stateLock) {
                Triple(turnFrameCount, turnByteCount, maxOf(0L, turnFrameCount - 1))
            }
            sendControlNow(
                JSONObject()
                    .put("type", "client.audio.commit")
                    .put("last_sequence_no", committed.third)
                    .put("frame_count", committed.first)
                    .put("byte_count", committed.second)
                    .put("duration_ms", durationMs.coerceAtLeast(0)),
            )
            Log.i(
                TAG,
                "VOICE audio commit sent frames=${committed.first} bytes=${committed.second} " +
                    "lastSequence=${committed.third} wallMs=${System.currentTimeMillis()} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
        }
        return Result(true)
    }

    fun cancelResponse(reason: String = "client_requested"): Result {
        val responseId = synchronized(stateLock) { status.responseId }
            ?: return Result(false, "E_VOICE_STATE", "No active response.")
        synchronized(stateLock) {
            status = status.copy(turnActive = false)
        }
        Log.i(
            TAG,
            "VOICE response cancel requested responseId=$responseId reason=$reason " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        sendQueue.clear()
        notifyStatus()
        postControl(
            JSONObject()
                .put("type", "client.response.cancel")
                .put("response_id", responseId)
                .put("reason", reason.take(128)),
        )
        return Result(true)
    }

    fun retryResponse(
        turnId: String,
        originalResponseId: String,
        transcript: String,
    ): Result {
        if (turnId.isBlank() || originalResponseId.isBlank() || transcript.isBlank()) {
            return Result(false, "E_VOICE_RETRY", "A completed transcript is required to retry.")
        }
        if (transcript.toByteArray(Charsets.UTF_8).size > MAX_TRANSCRIPT_BYTES) {
            return Result(false, "E_VOICE_RETRY", "The transcript is too large to retry.")
        }
        synchronized(stateLock) {
            if (status.state != State.SESSION_READY || !status.sessionStarted) {
                return Result(false, "E_VOICE_STATE", "The voice session is not ready for retry.")
            }
            status = status.copy(responseId = null, turnId = turnId, turnActive = false)
        }
        postControl(
            JSONObject()
                .put("type", "client.response.retry")
                .put("turn_id", turnId)
                .put("original_response_id", originalResponseId)
                .put("transcript", transcript),
        )
        notifyStatus()
        return Result(true)
    }

    fun endSession(reason: String = "client_requested"): Result {
        synchronized(stateLock) {
            if (!status.sessionStarted) {
                return Result(false, "E_VOICE_STATE", "No active voice session.")
            }
            status = status.copy(turnActive = false)
        }
        sendQueue.clear()
        notifyStatus()
        postControl(JSONObject().put("type", "client.session.end").put("reason", reason.take(128)))
        return Result(true)
    }

    fun getStatus(): Status = synchronized(stateLock) {
        status.copyFromQueue(sendQueue.snapshot())
    }

    fun shutdown() {
        stopHeartbeat()
        disconnect()
        networkExecutor.shutdownNow()
        heartbeatScheduler.shutdownNow()
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
    }

    data class Result(
        val succeeded: Boolean,
        val errorCode: String? = null,
        val errorMessage: String? = null,
    )

    private val socketListener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!isCurrentSocket(webSocket)) {
                webSocket.cancel()
                return
            }
            Log.i(
                TAG,
                "VOICE websocket opened wallMs=${System.currentTimeMillis()} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            synchronized(stateLock) {
                status = status.copy(state = State.CONNECTED, connected = true, lastError = null)
            }
            scheduleHeartbeat(DEFAULT_HEARTBEAT_INTERVAL_SECONDS)
            notifyStatus()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!isCurrentSocket(webSocket)) return
            handleServerEvent(text)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (!isCurrentSocket(webSocket)) return
            recordError("E_VOICE_PROTOCOL", "Unexpected binary server message.")
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrentSocket(webSocket)) return
            Log.i(
                TAG,
                "VOICE websocket closing code=$code reason=$reason " +
                    "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            stopHeartbeat()
            synchronized(stateLock) {
                status = status.copy(state = State.CLOSING, connected = false, turnActive = false)
            }
            notifyStatus()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrentSocket(webSocket)) return
            Log.i(
                TAG,
                "VOICE websocket closed code=$code reason=$reason " +
                    "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            stopHeartbeat()
            synchronized(stateLock) {
                this@VoiceWebSocketTransport.webSocket = null
                status = status.copy(state = State.DISCONNECTED, connected = false, turnActive = false)
            }
            sendQueue.clear()
            notifyStatus()
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!isCurrentSocket(webSocket)) return
            Log.e(
                TAG,
                "VOICE websocket failure type=${t::class.java.simpleName} " +
                    "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            stopHeartbeat()
            synchronized(stateLock) {
                if (this@VoiceWebSocketTransport.webSocket === webSocket) {
                    this@VoiceWebSocketTransport.webSocket = null
                }
            }
            recordError("E_VOICE_WEBSOCKET", "Voice gateway connection failed.")
        }
    }

    private fun isCurrentSocket(candidate: WebSocket): Boolean = synchronized(stateLock) {
        webSocket === candidate
    }

    private fun postControl(message: JSONObject) {
        networkExecutor.execute { sendControlNow(message) }
    }

    private fun sendControlNow(message: JSONObject) {
        Log.i(
            TAG,
            "VOICE control sent type=${message.optString("type", "unknown")} " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        val sent = webSocket?.send(message.toString()) == true
        if (!sent) recordError("E_VOICE_SEND", "Voice gateway message could not be sent.")
    }

    private fun scheduleDrain() {
        if (!drainScheduled.compareAndSet(false, true)) return
        networkExecutor.execute {
            try {
                drainQueue()
            } finally {
                drainScheduled.set(false)
                if (sendQueue.snapshot().depth > 0) scheduleDrain()
            }
        }
    }

    private fun drainQueue() {
        while (true) {
            val frame = sendQueue.poll() ?: return
            val sent = webSocket?.send(ByteString.of(*VoiceBinaryFrame.encode(frame))) == true
            if (!sent) {
                recordError("E_VOICE_SEND", "Voice gateway audio frame could not be sent.")
                return
            }
            synchronized(stateLock) {
                status = status.copy(
                    framesSent = status.framesSent + 1,
                    bytesSent = status.bytesSent + frame.payload.size,
                    state = if (status.turnActive) State.STREAMING_AUDIO else status.state,
                )
                turnFrameCount += 1
                turnByteCount += frame.payload.size
            }
            if (frame.sequenceNo == 0L || frame.sequenceNo % 50L == 0L) {
                Log.i(
                    TAG,
                    "VOICE PCM frame sent sequence=${frame.sequenceNo} " +
                        "bytes=${frame.payload.size} clientTimestampMs=${frame.clientTimestampMs} " +
                        "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
                )
            }
        }
    }

    private fun handleServerEvent(raw: String) {
        if (raw.toByteArray(Charsets.UTF_8).size > MAX_SERVER_EVENT_BYTES) {
            recordError("E_VOICE_PROTOCOL", "Voice gateway event is too large.")
            return
        }
        val json = try {
            JSONObject(raw)
        } catch (_: Exception) {
            recordError("E_VOICE_PROTOCOL", "Voice gateway sent malformed metadata.")
            return
        }
        val eventType = json.optString("type", "unknown")
        val sessionId = json.optStringOrNull("session_id")
        val turnId = json.optStringOrNull("turn_id")
        val responseId = json.optStringOrNull("response_id")
        val eventId = json.optStringOrNull("event_id")
        val timestampMs = if (json.has("timestamp_ms") && !json.isNull("timestamp_ms")) {
            json.optLong("timestamp_ms")
        } else {
            null
        }
        val payload = extractServerEventPayload(eventType, json)
        val terminalSessionEvent = eventType == "server.session.ended" ||
            (eventType == "server.error" && payload?.errorCode == "session_not_available")
        Log.i(
            TAG,
            "VOICE server event type=$eventType sessionId=${sessionId ?: "NONE"} " +
                "turnId=${turnId ?: "NONE"} responseId=${responseId ?: "NONE"} " +
                "wallMs=${System.currentTimeMillis()} " +
                "elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        if (eventType == "server.session.ready") {
            scheduleHeartbeat(
                json.optLong("heartbeat_interval_seconds", DEFAULT_HEARTBEAT_INTERVAL_SECONDS),
            )
        }
        if (eventType == "server.error" || eventType == "server.session.ended") {
            stopHeartbeat()
        }
        synchronized(stateLock) {
            status = status.copy(
                state = when (eventType) {
                    "server.session.ready" -> State.SESSION_READY
                    "server.turn.ready" -> State.STREAMING_AUDIO
                    "server.turn.completed", "response.cancelled" -> State.SESSION_READY
                    "server.session.ended" -> State.DISCONNECTED
                    "server.error" -> State.ERROR
                    else -> status.state
                },
                connected = eventType != "server.session.ended" &&
                    eventType != "server.error" && status.connected,
                sessionStarted = !terminalSessionEvent &&
                    (status.sessionStarted || eventType == "server.session.ready"),
                turnActive = when (eventType) {
                    "server.turn.completed", "response.cancelled", "server.error" -> false
                    else -> status.turnActive
                },
                sessionId = if (terminalSessionEvent) null else sessionId ?: status.sessionId,
                turnId = if (terminalSessionEvent) null else turnId ?: status.turnId,
                responseId = if (terminalSessionEvent) null else responseId ?: status.responseId,
                lastServerEvent = eventType,
                lastServerEventTimestampMs = System.currentTimeMillis(),
            )
        }
        notifyStatus()
        listener.onServerEvent(
            eventType,
            sessionId,
            turnId,
            responseId,
            eventId,
            timestampMs,
            payload,
        )
    }

    private fun extractServerEventPayload(
        eventType: String,
        json: JSONObject,
    ): ServerEventPayload? {
        val isTranscript = eventType in setOf(
            "transcript.partial",
            "transcript.final",
            "voice.transcript.partial",
            "voice.transcript.final.delivered",
        )
        val isAssistant = eventType in setOf(
            "assistant.response.started",
            "assistant.request.started",
            "assistant.text.delta",
            "assistant.text.final",
            "llm.response.completed",
        )
        val isError = eventType == "server.error" || eventType == "server.turn.failed" ||
            eventType == "assistant.response.failed" || eventType == "llm.response.failed"
        val isTool = eventType == "tool.status" || eventType == "confirmation.required"
        if (!isTranscript && !isAssistant && !isError && !isTool) {
            return null
        }

        val text = json.optStringOrNull("text")?.takeIf { it.length <= MAX_TRANSCRIPT_BYTES }
        val delta = json.optStringOrNull("delta")?.takeIf { it.length <= MAX_TRANSCRIPT_BYTES }
        val isFinal = if (json.has("final") && !json.isNull("final")) {
            json.optBoolean("final")
        } else {
            null
        }
        val transcriptSequence = if (
            json.has("transcript_sequence") && !json.isNull("transcript_sequence")
        ) {
            json.optLong("transcript_sequence").takeIf { it >= 0L }
        } else {
            null
        }
        val audioDurationMs = if (
            json.has("audio_duration_ms") && !json.isNull("audio_duration_ms")
        ) {
            json.optLong("audio_duration_ms").takeIf { it >= 0L }
        } else {
            null
        }
        val sequence = if (json.has("sequence") && !json.isNull("sequence")) {
            json.optLong("sequence").takeIf { it >= 0L }
        } else {
            null
        }
        val attempt = if (json.has("attempt") && !json.isNull("attempt")) {
            json.optLong("attempt").takeIf { it >= 1L }
        } else {
            null
        }
        return ServerEventPayload(
            text = text,
            delta = delta,
            isFinal = isFinal,
            sequence = sequence,
            attempt = attempt,
            transcriptSequence = transcriptSequence,
            language = json.optStringOrNull("language")?.takeIf { it.length <= MAX_LANGUAGE_BYTES },
            audioDurationMs = audioDurationMs,
            metrics = boundedMetrics(json.optJSONObject("metrics")),
            usage = boundedMetrics(json.optJSONObject("usage")),
            toolCallId = json.optStringOrNull("tool_call_id")
                ?.takeIf { it.length <= MAX_ERROR_CODE_BYTES },
            toolName = json.optStringOrNull("tool_name")
                ?.takeIf { it.length <= MAX_ERROR_CODE_BYTES },
            toolStatus = (
                if (eventType == "confirmation.required") {
                    "confirmation_required"
                } else {
                    json.optStringOrNull("tool_status")
                }
                )?.takeIf { it.length <= MAX_ERROR_CODE_BYTES },
            confirmationId = json.optStringOrNull("confirmation_id")
                ?.takeIf { it.length <= MAX_ERROR_CODE_BYTES },
            errorCode = (json.optStringOrNull("code") ?: json.optStringOrNull("error_code"))
                ?.takeIf { it.length <= MAX_ERROR_CODE_BYTES },
            retryable = if (json.has("retryable") && !json.isNull("retryable")) {
                json.optBoolean("retryable")
            } else {
                null
            },
        )
    }

    private fun boundedMetrics(metricsJson: JSONObject?): Map<String, Double?> {
        if (metricsJson == null) {
            return emptyMap()
        }
        val metrics = linkedMapOf<String, Double?>()
        val keys = metricsJson.keys()
        while (keys.hasNext() && metrics.size < MAX_METRIC_COUNT) {
            val key = keys.next()
            if (key.length > MAX_METRIC_KEY_BYTES) {
                continue
            }
            val value = metricsJson.opt(key)
            when {
                value == null || value == JSONObject.NULL -> metrics[key] = null
                value is Number -> metrics[key] = value.toDouble()
            }
        }
        return metrics
    }

    private fun fail(code: String, message: String): Result {
        recordError(code, message)
        return Result(false, code, message)
    }

    private fun recordError(code: String, message: String) {
        Log.e(
            TAG,
            "VOICE error code=$code message=$message wallMs=${System.currentTimeMillis()} " +
                "elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        stopHeartbeat()
        synchronized(stateLock) {
            status = status.copy(
                state = State.ERROR,
                connected = false,
                turnActive = false,
                websocketErrorCount = status.websocketErrorCount + 1,
                lastError = "$code: $message",
            )
        }
        sendQueue.clear()
        notifyStatus()
    }

    private fun notifyStatus() {
        listener.onStatus(getStatus())
    }

    /**
     * Sends application-level heartbeats without touching AudioRecord or the
     * PCM queue. The session-ready event supplies the backend interval; the
     * connection-level default covers the handshake period.
     */
    private fun scheduleHeartbeat(intervalSeconds: Long) {
        val intervalMs = intervalSeconds.coerceIn(1L, MAX_HEARTBEAT_INTERVAL_SECONDS) * 1000L
        synchronized(stateLock) {
            if (!status.connected) return
            heartbeatTask?.cancel(false)
            heartbeatTask = heartbeatScheduler.scheduleAtFixedRate(
                { sendHeartbeat() },
                intervalMs,
                intervalMs,
                TimeUnit.MILLISECONDS,
            )
        }
    }

    private fun stopHeartbeat() {
        synchronized(stateLock) {
            heartbeatTask?.cancel(false)
            heartbeatTask = null
        }
    }

    private fun sendHeartbeat() {
        val socket = synchronized(stateLock) {
            webSocket.takeIf { status.connected && status.state != State.CLOSING }
        } ?: return
        val message = JSONObject()
            .put("type", "client.ping")
            .put("client_timestamp_ms", System.currentTimeMillis())
        Log.i(
            TAG,
            "VOICE heartbeat sent wallMs=${System.currentTimeMillis()} " +
                "elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        if (!socket.send(message.toString())) {
            recordError("E_VOICE_HEARTBEAT", "Voice gateway heartbeat could not be sent.")
        }
    }

    private fun Status.copyFromQueue(snapshot: PcmSendQueue.Snapshot): Status = copy(
        framesQueued = snapshot.depth,
        queueHighWaterMark = snapshot.highWaterMark,
        droppedFrames = snapshot.droppedFrames,
        invalidFrames = snapshot.invalidFrames,
    )

    private fun JSONObject.optStringOrNull(name: String): String? =
        if (!has(name) || isNull(name)) null else optString(name).takeIf { it.isNotBlank() }

    private companion object {
        const val TAG = "VoiceAI-VoiceGateway"
        const val DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15L
        const val MAX_HEARTBEAT_INTERVAL_SECONDS = 60L
        const val MAX_SERVER_EVENT_BYTES = 16 * 1024
        const val MAX_TRANSCRIPT_BYTES = 16 * 1024
        const val MAX_LANGUAGE_BYTES = 32
        const val MAX_ERROR_CODE_BYTES = 128
        const val MAX_METRIC_COUNT = 32
        const val MAX_METRIC_KEY_BYTES = 64
    }
}
