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
import java.util.Locale
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
    private val isTtsOutputEnabled: () -> Boolean = { true },
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
        val sampleRateHz: Long? = null,
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

        fun onTtsPlayback(
            eventType: String,
            sessionId: String?,
            turnId: String?,
            responseId: String?,
            timestampMs: Long,
        ) = Unit

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
    private val ttsSequenceTracker = TtsFrameSequenceTracker()
    private var localCloseRequested = false
    private var localCloseReason: String? = null
    private var activeTtsResponseId: String? = null
    private var activeTtsFirstAudioElapsedMs: Long? = null
    private var lastTtsFrameSequence: Long? = null
    private var lastTtsFrameBytes = 0
    private var lastTtsFrameReceivedElapsedMs: Long? = null
    private var lastTtsFrameEnqueuedSequence: Long? = null
    private var lastTtsFrameWrittenSequence: Long? = null
    private var firstTtsEnqueueResponseId: String? = null
    private var ttsFramesReceived = 0L
    private var ttsFramesEnqueued = 0L
    private var ttsPcmBytesReceived = 0L
    private var ttsPcmBytesEnqueued = 0L
    private var ttsSequenceGaps = 0L
    private var ttsDuplicateFrames = 0L
    private var ttsStaleFrames = 0L
    private val ttsAudioPlayer = TtsAudioPlayer(
        listener = object : TtsAudioPlayer.Listener {
            override fun onPlaybackStarted(responseId: java.util.UUID) =
                notifyTtsPlayback("tts.playback.started", responseId)

            override fun onPlaybackCompleted(responseId: java.util.UUID) =
                notifyTtsPlayback("tts.playback.completed", responseId)

            override fun onPlaybackStopped(responseId: java.util.UUID) =
                notifyTtsPlayback("tts.playback.stopped", responseId)

            override fun onPlaybackError(responseId: java.util.UUID, errorCode: String) {
                Log.e(TAG, "TTS_PLAYBACK_ERROR response_id=$responseId code=$errorCode")
                notifyTtsPlayback("tts.playback.stopped", responseId)
            }

            override fun onPcmEnqueued(
                responseId: java.util.UUID,
                sequence: Long,
                bytes: Int,
                queuedBytes: Int,
            ) {
                lastTtsFrameEnqueuedSequence = sequence
                ttsFramesEnqueued += 1
                ttsPcmBytesEnqueued += bytes
                if (firstTtsEnqueueResponseId != responseId.toString()) {
                    firstTtsEnqueueResponseId = responseId.toString()
                    Log.i(
                        TAG,
                        "TTS_FIRST_PCM_ENQUEUED response_id=$responseId seq=$sequence " +
                            "bytes=$bytes elapsedMs=${SystemClock.elapsedRealtime()}",
                    )
                }
                Log.i(
                    TAG,
                    "TTS_FRAME_ENQUEUED response_id=$responseId seq=$sequence " +
                        "bytes=$bytes queued_bytes=$queuedBytes " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
            }

            override fun onPcmWrite(
                responseId: java.util.UUID,
                sequence: Long,
                requestedBytes: Int,
                writtenBytes: Int,
                queueBytesRemaining: Int,
            ) {
                lastTtsFrameWrittenSequence = sequence
            }

            override fun onPlaybackSummary(
                responseId: java.util.UUID,
                summary: TtsAudioPlayer.PlaybackSummary,
            ) {
                Log.i(
                    TAG,
                    "TTS_RESPONSE_SUMMARY response_id=$responseId " +
                        "frames_received=$ttsFramesReceived frames_enqueued=$ttsFramesEnqueued " +
                        "frames_written=${summary.framesWritten} " +
                        "pcm_bytes_received=$ttsPcmBytesReceived " +
                        "pcm_bytes_enqueued=$ttsPcmBytesEnqueued " +
                        "pcm_bytes_written=${summary.pcmBytesWritten} " +
                        "sequence_gaps=$ttsSequenceGaps duplicates=$ttsDuplicateFrames " +
                        "stale_frames=$ttsStaleFrames partial_writes=${summary.partialWrites} " +
                        "write_errors=${summary.writeErrors} underrun_delta=${summary.underrunDelta} " +
                        "ws_connected_after_playback=${synchronized(stateLock) { status.connected }}",
                )
                if (activeTtsResponseId == responseId.toString()) {
                    activeTtsResponseId = null
                }
            }
        },
    )

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
            localCloseRequested = false
            localCloseReason = null
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
            localCloseRequested = true
            localCloseReason = "client_disconnect"
            status = status.copy(state = State.CLOSING, connected = false, turnActive = false)
        }
        sendQueue.clear()
        ttsAudioPlayer.cancel()
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
                    .put("timezone", TimeZone.getDefault().id)
                    .put("locale", Locale.getDefault().toLanguageTag()),
            )
            .put("device_time_context", deviceTimeContext())
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
        val message = JSONObject()
            .put("type", "client.turn.start")
            .put("device_time_context", deviceTimeContext())
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

    fun resolveConfirmation(
        confirmationId: String,
        toolCallId: String,
        decision: String,
    ): Result {
        if (confirmationId.isBlank() || toolCallId.isBlank()) {
            return Result(false, "E_VOICE_CONFIRMATION", "A confirmation identity is required.")
        }
        if (decision != "approve" && decision != "deny") {
            return Result(false, "E_VOICE_CONFIRMATION", "The confirmation decision is invalid.")
        }
        synchronized(stateLock) {
            if (!status.connected || !status.sessionStarted) {
                return Result(false, "E_VOICE_STATE", "The voice session is not ready for confirmation.")
            }
        }
        postControl(
            JSONObject()
                .put("type", "client.confirmation.resolve")
                .put("confirmation_id", confirmationId)
                .put("tool_call_id", toolCallId)
                .put("decision", decision),
        )
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

    fun stopTtsPlayback() {
        ttsAudioPlayer.cancel()
    }

    fun shutdown() {
        stopHeartbeat()
        disconnect()
        networkExecutor.shutdownNow()
        heartbeatScheduler.shutdownNow()
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
        ttsAudioPlayer.shutdown()
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
                "WS_CONNECTED http_code=${response.code} wallMs=${System.currentTimeMillis()} " +
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
            handleTtsAudio(bytes.toByteArray())
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrentSocket(webSocket)) return
            val current = synchronized(stateLock) { status }
            val initiator = if (localCloseRequested) "local" else "remote"
            Log.i(
                TAG,
                "WS_CLOSING code=$code reason=${reason.take(120)} initiator=$initiator " +
                    "session_id=${current.sessionId ?: "NONE"} turn_id=${current.turnId ?: "NONE"} " +
                    "response_id=${current.responseId ?: "NONE"} tts_active=${activeTtsResponseId != null} " +
                    "last_tts_seq=${lastTtsFrameSequence ?: -1} " +
                    "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            stopHeartbeat()
            ttsAudioPlayer.cancel()
            synchronized(stateLock) {
                status = status.copy(state = State.CLOSING, connected = false, turnActive = false)
            }
            notifyStatus()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrentSocket(webSocket)) return
            val current = synchronized(stateLock) { status }
            val initiator = if (localCloseRequested) "local" else "remote"
            Log.i(
                TAG,
                "WS_CLOSED code=$code reason=${reason.take(120)} initiator=$initiator " +
                    "session_id=${current.sessionId ?: "NONE"} turn_id=${current.turnId ?: "NONE"} " +
                    "response_id=${current.responseId ?: "NONE"} tts_active=${activeTtsResponseId != null} " +
                    "last_tts_seq=${lastTtsFrameSequence ?: -1} " +
                    "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            stopHeartbeat()
            synchronized(stateLock) {
                this@VoiceWebSocketTransport.webSocket = null
                status = status.copy(state = State.DISCONNECTED, connected = false, turnActive = false)
            }
            sendQueue.clear()
            ttsAudioPlayer.cancel()
            notifyStatus()
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!isCurrentSocket(webSocket)) return
            val current = synchronized(stateLock) { status }
            val initiator = if (localCloseRequested) "local" else "unknown"
            Log.e(
                TAG,
                "WS_FAILURE exception=${t::class.java.simpleName} " +
                    "message=${(t.message ?: "").take(240)} " +
                    "http_code=${response?.code ?: -1} " +
                    "session_id=${current.sessionId ?: "NONE"} turn_id=${current.turnId ?: "NONE"} " +
                    "response_id=${current.responseId ?: "NONE"} tts_active=${activeTtsResponseId != null} " +
                    "last_server_event=${current.lastServerEvent ?: "NONE"} " +
                    "last_tts_seq=${lastTtsFrameSequence ?: -1} " +
                    "last_tts_bytes=$lastTtsFrameBytes " +
                    "last_tts_written_seq=${lastTtsFrameWrittenSequence ?: -1} " +
                    "initiator=$initiator heartbeat_active=${heartbeatTask != null} " +
                    "cancel_requested=${localCloseReason != null} " +
                    "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            Log.e(TAG, "WS_FAILURE stacktrace", t)
            stopHeartbeat()
            ttsAudioPlayer.cancel()
            synchronized(stateLock) {
                if (this@VoiceWebSocketTransport.webSocket === webSocket) {
                    this@VoiceWebSocketTransport.webSocket = null
                }
            }
            if (!localCloseRequested) {
                recordError("E_VOICE_WEBSOCKET", "Voice gateway connection failed.")
            } else {
                synchronized(stateLock) {
                    status = status.copy(
                        state = State.DISCONNECTED,
                        connected = false,
                        turnActive = false,
                        lastError = null,
                    )
                }
                notifyStatus()
            }
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

    /**
     * Capture the current device wall clock and timezone rules at the protocol
     * boundary. The epoch is the absolute instant; the IANA ID is authoritative
     * for local calendar/DST calculations. The offset is diagnostic metadata.
     */
    private fun deviceTimeContext(): JSONObject {
        return buildDeviceTimeContextJson().toJson()
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
        if (eventType == "response.cancelled" || eventType == "tts.cancelled" ||
            eventType == "tts.failed") {
            ttsAudioPlayer.cancel(responseId?.let {
                runCatching { java.util.UUID.fromString(it) }.getOrNull()
            })
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
                lastError = if (eventType == "server.error") {
                    payload?.errorCode?.let { "E_VOICE_SERVER: $it" } ?: status.lastError
                } else {
                    status.lastError
                },
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

    private fun handleTtsAudio(bytes: ByteArray) {
        val frame = TtsAudioFrame.parse(bytes)
        if (frame == null) {
            recordError("E_VOICE_PROTOCOL", "Voice gateway sent malformed audio.")
            return
        }
        val currentResponseId = synchronized(stateLock) { status.responseId }
        if (currentResponseId != frame.responseId.toString()) {
            ttsStaleFrames += 1
            Log.w(TAG, "TTS_STALE_FRAME response_id=${frame.responseId} sequence=${frame.sequence}")
            return
        }
        if (frame.startsResponse) {
            activeTtsResponseId = frame.responseId.toString()
            activeTtsFirstAudioElapsedMs = null
            lastTtsFrameSequence = null
            lastTtsFrameBytes = 0
            lastTtsFrameReceivedElapsedMs = null
            lastTtsFrameEnqueuedSequence = null
            lastTtsFrameWrittenSequence = null
            firstTtsEnqueueResponseId = null
            ttsFramesReceived = 0
            ttsFramesEnqueued = 0
            ttsPcmBytesReceived = 0
            ttsPcmBytesEnqueued = 0
            ttsSequenceGaps = 0
            ttsDuplicateFrames = 0
            ttsStaleFrames = 0
            val current = synchronized(stateLock) { status }
            Log.i(
                TAG,
                "TTS_START session_id=${current.sessionId ?: "NONE"} " +
                    "turn_id=${current.turnId ?: "NONE"} response_id=${frame.responseId} " +
                    "sample_rate=${frame.sampleRateHz} channels=1 encoding=PCM16 " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
        }
        when (val sequenceResult = ttsSequenceTracker.observe(frame)) {
            TtsFrameSequenceTracker.Result.GAP -> {
                ttsSequenceGaps += 1
                Log.e(
                    TAG,
                    "TTS_FRAME_GAP response_id=${frame.responseId} received=${frame.sequence}",
                )
            }
            TtsFrameSequenceTracker.Result.DUPLICATE_OR_OUT_OF_ORDER -> {
                ttsDuplicateFrames += 1
                Log.e(
                    TAG,
                    "TTS_FRAME_DUPLICATE_OR_OUT_OF_ORDER response_id=${frame.responseId} " +
                        "received=${frame.sequence}",
                )
            }
            TtsFrameSequenceTracker.Result.STALE -> {
                ttsStaleFrames += 1
                Log.w(TAG, "TTS_STALE_FRAME response_id=${frame.responseId} sequence=${frame.sequence}")
                return
            }
            else -> Unit
        }
        Log.i(
            TAG,
            "TTS_FRAME_RECEIVED response_id=${frame.responseId} seq=${frame.sequence} " +
                "bytes=${frame.payload.size} starts=${frame.startsResponse} " +
                "ends=${frame.endsResponse} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        ttsFramesReceived += 1
        ttsPcmBytesReceived += frame.payload.size
        lastTtsFrameSequence = frame.sequence
        lastTtsFrameBytes = frame.payload.size
        lastTtsFrameReceivedElapsedMs = SystemClock.elapsedRealtime()
        if (frame.payload.isNotEmpty() && activeTtsFirstAudioElapsedMs == null) {
            activeTtsFirstAudioElapsedMs = lastTtsFrameReceivedElapsedMs
            Log.i(
                TAG,
                "TTS_FIRST_AUDIO_RECEIVED response_id=${frame.responseId} seq=${frame.sequence} " +
                    "bytes=${frame.payload.size} sample_rate=${frame.sampleRateHz} " +
                    "channels=1 encoding=PCM16 elapsedMs=${activeTtsFirstAudioElapsedMs}",
            )
        }
        if (!isTtsOutputEnabled()) return
        if (frame.startsResponse) {
            if (!ttsAudioPlayer.start(frame.responseId, frame.sampleRateHz)) {
                return
            }
        }
        if (!ttsAudioPlayer.write(frame.responseId, frame.sequence, frame.payload)) {
            recordError("E_TTS_PLAYBACK", "TTS PCM could not be queued for playback.")
            return
        }
        if (frame.endsResponse) {
            ttsAudioPlayer.finish(frame.responseId)
        }
    }

    private fun notifyTtsPlayback(eventType: String, responseId: java.util.UUID) {
        val current = synchronized(stateLock) { status }
        listener.onTtsPlayback(
            eventType,
            current.sessionId,
            current.turnId,
            responseId.toString(),
            System.currentTimeMillis(),
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
            "tts.started",
            "tts.completed",
            "tts.cancelled",
            "tts.failed",
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
            sampleRateHz = if (json.has("sample_rate_hz") && !json.isNull("sample_rate_hz")) {
                json.optLong("sample_rate_hz").takeIf { it in 8_000L..48_000L }
            } else {
                null
            },
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
