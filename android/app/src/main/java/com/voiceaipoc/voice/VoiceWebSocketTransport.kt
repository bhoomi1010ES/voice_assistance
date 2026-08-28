package com.voiceaipoc.voice

import android.os.SystemClock
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

/**
 * Native Phase 3 voice gateway transport.
 *
 * Network work runs outside AudioRecord and the PCM callback only performs a
 * bounded copy. The listener exposes status and protocol metadata only.
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

    interface Listener {
        fun onStatus(status: Status)

        fun onServerEvent(
            eventType: String,
            sessionId: String?,
            turnId: String?,
            responseId: String?,
        )
    }

    private val stateLock = Any()
    private val drainScheduled = AtomicBoolean(false)
    private var webSocket: WebSocket? = null
    private var heartbeatTask: ScheduledFuture<*>? = null
    private var status = Status()
    private var turnFrameCount = 0L
    private var turnByteCount = 0L

    fun connect(url: String): Result {
        val token = tokenStorage.read()?.accessToken
            ?: return fail("E_VOICE_AUTH", "No access token is stored securely on this device.")
        if (!url.startsWith("ws://") && !url.startsWith("wss://")) {
            return fail("E_VOICE_URL", "Voice gateway URL must use ws:// or wss://.")
        }

        synchronized(stateLock) {
            if (status.state !in setOf(State.DISCONNECTED, State.ERROR)) {
                return Result(false, "E_VOICE_ALREADY_CONNECTED", "Voice gateway is already connected.")
            }
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
        notifyStatus()

        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .build()
        webSocket = client.newWebSocket(request, socketListener)
        return Result(true)
    }

    fun disconnect() {
        stopHeartbeat()
        synchronized(stateLock) {
            if (status.state == State.DISCONNECTED) return
            status = status.copy(state = State.CLOSING, connected = false, turnActive = false)
        }
        sendQueue.clear()
        notifyStatus()
        webSocket?.close(1000, "client_disconnect")
    }

    fun startSession(resumeSessionId: String? = null): Result {
        synchronized(stateLock) {
            if (status.state != State.CONNECTED) {
                return Result(false, "E_VOICE_STATE", "Connect to the voice gateway first.")
            }
            status = status.copy(state = State.SESSION_STARTING)
        }
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
                JSONObject().put("platform", "android").put("client_version", "phase3-native"),
            )
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
        }
        return Result(true)
    }

    fun cancelResponse(reason: String = "client_requested"): Result {
        val responseId = synchronized(stateLock) { status.responseId }
            ?: return Result(false, "E_VOICE_STATE", "No active response.")
        synchronized(stateLock) {
            status = status.copy(turnActive = false)
        }
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
            synchronized(stateLock) {
                status = status.copy(state = State.CONNECTED, connected = true, lastError = null)
            }
            scheduleHeartbeat(DEFAULT_HEARTBEAT_INTERVAL_SECONDS)
            notifyStatus()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            handleServerEvent(text)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            recordError("E_VOICE_PROTOCOL", "Unexpected binary server message.")
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            stopHeartbeat()
            synchronized(stateLock) {
                status = status.copy(state = State.CLOSING, connected = false, turnActive = false)
            }
            notifyStatus()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            stopHeartbeat()
            synchronized(stateLock) {
                status = status.copy(state = State.DISCONNECTED, connected = false, turnActive = false)
            }
            sendQueue.clear()
            notifyStatus()
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            stopHeartbeat()
            recordError("E_VOICE_WEBSOCKET", "Voice gateway connection failed.")
        }
    }

    private fun postControl(message: JSONObject) {
        networkExecutor.execute { sendControlNow(message) }
    }

    private fun sendControlNow(message: JSONObject) {
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
        }
    }

    private fun handleServerEvent(raw: String) {
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
        if (eventType == "server.session.ready") {
            scheduleHeartbeat(
                json.optLong("heartbeat_interval_seconds", DEFAULT_HEARTBEAT_INTERVAL_SECONDS),
            )
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
                connected = eventType != "server.session.ended" && status.connected,
                sessionStarted = eventType != "server.session.ended" &&
                    (status.sessionStarted || eventType == "server.session.ready"),
                turnActive = when (eventType) {
                    "server.turn.completed", "response.cancelled", "server.error" -> false
                    else -> status.turnActive
                },
                sessionId = sessionId ?: status.sessionId,
                turnId = turnId ?: status.turnId,
                responseId = responseId ?: status.responseId,
                lastServerEvent = eventType,
                lastServerEventTimestampMs = System.currentTimeMillis(),
            )
        }
        notifyStatus()
        listener.onServerEvent(eventType, sessionId, turnId, responseId)
    }

    private fun fail(code: String, message: String): Result {
        recordError(code, message)
        return Result(false, code, message)
    }

    private fun recordError(code: String, message: String) {
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
        const val DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15L
        const val MAX_HEARTBEAT_INTERVAL_SECONDS = 60L
    }
}
