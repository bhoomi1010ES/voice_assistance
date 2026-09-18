package com.voiceaipoc.voice

import android.os.SystemClock
import android.util.Log
import com.voiceaipoc.auth.AuthTokenStorage
import com.voiceaipoc.audio.PlaybackEchoReference
import com.voiceaipoc.diagnostics.DiagnosticSessionContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
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
    private val playbackEchoReference: PlaybackEchoReference = PlaybackEchoReference(),
    private val client: OkHttpClient = OkHttpClient(),
    private val networkExecutor: ExecutorService = Executors.newSingleThreadExecutor {
        Thread(it, "VoiceAI-VoiceGateway")
    },
    private val sendQueue: PcmSendQueue = PcmSendQueue(),
    private val heartbeatScheduler: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor {
        Thread(it, "VoiceAI-VoiceHeartbeat")
    },
    private val ttsFrameExecutor: ExecutorService = Executors.newSingleThreadExecutor {
        Thread(it, "VoiceAI-TtsFrameIngress")
    },
    private val diagnosticSession: DiagnosticSessionContext = DiagnosticSessionContext(),
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
        val diagnosticSessionId: String = DiagnosticSessionContext.NONE,
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
        val status: String? = null,
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
    private var firstAssistantTextResponseId: String? = null
    private var webSocket: WebSocket? = null
    private var heartbeatTask: ScheduledFuture<*>? = null
    private var status = Status()
    private var turnFrameCount = 0L
    private var turnByteCount = 0L
    private var turnGeneration = 0L
    private var awaitingTurnReadyGeneration: Long? = null
    private var pendingCommitDurationMs: Int? = null
    private var cancelledBargeInResponseId: String? = null
    @Volatile
    private var bargeInTurn = false
    @Volatile
    private var bargeInLivePcmLogged = false
    private var bargeInState = BargeInState.IDLE
    private val ttsSequenceTracker = TtsFrameSequenceTracker()
    private var localCloseRequested = false
    private var localCloseReason: String? = null
    private var activeTtsResponseId: String? = null
    private var activeTtsFirstAudioElapsedMs: Long? = null
    private var activeTtsFirstAudioElapsedNs: Long? = null
    private var activeTtsPlaybackStartedNs: Long? = null
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

    private enum class BargeInState {
        IDLE,
        NEW_TURN_STARTING,
        NEW_TURN_READY,
        NEW_TURN_RECORDING,
        NEW_TURN_COMMITTING,
    }
    private val ttsAudioPlayer = TtsAudioPlayer(
        diagnosticSession = diagnosticSession,
        listener = object : TtsAudioPlayer.Listener {
            override fun onPlaybackStarted(responseId: java.util.UUID) {
                playbackEchoReference.onPlaybackStarted(responseId.toString())
                ttsLogInfo(
                    "TTS_AUDIO_RENDER_STARTED response_id=$responseId " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                notifyTtsPlayback("tts.playback.started", responseId)
            }

            override fun onPlaybackCompleted(responseId: java.util.UUID) {
                playbackEchoReference.onPlaybackEnded(responseId.toString())
                ttsLogInfo(
                    "TTS_AUDIO_RENDER_ENDED response_id=$responseId reason=completed " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                notifyTtsPlayback("tts.playback.completed", responseId)
            }

            override fun onPlaybackStopped(responseId: java.util.UUID) {
                playbackEchoReference.onPlaybackEnded(responseId.toString())
                ttsLogInfo(
                    "TTS_AUDIO_RENDER_ENDED response_id=$responseId reason=stopped " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                notifyTtsPlayback("tts.playback.stopped", responseId)
            }

            override fun onPlaybackError(responseId: java.util.UUID, errorCode: String) {
                playbackEchoReference.onPlaybackEnded(responseId.toString())
                ttsLogInfo(
                    "TTS_AUDIO_RENDER_ENDED response_id=$responseId reason=error:$errorCode " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                ttsLogError("TTS_PLAYBACK_ERROR response_id=$responseId code=$errorCode")
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
                    ttsLogInfo(
                        "TTS_FIRST_PCM_ENQUEUED response_id=$responseId seq=$sequence " +
                            "bytes=$bytes elapsedMs=${SystemClock.elapsedRealtime()}",
                    )
                }
                ttsLogInfo(
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

            override fun onPcmRendered(
                responseId: java.util.UUID,
                sequence: Long,
                payload: ByteArray,
                offsetBytes: Int,
                writtenBytes: Int,
            ) {
                playbackEchoReference.onPcmRendered(
                    responseId = responseId.toString(),
                    payload = payload,
                    offsetBytes = offsetBytes,
                    byteCount = writtenBytes,
                    sampleRateHz = TtsAudioFrame.TTS_SAMPLE_RATE_HZ,
                )
            }

            override fun onPlaybackSummary(
                responseId: java.util.UUID,
                summary: TtsAudioPlayer.PlaybackSummary,
            ) {
                ttsLogInfo(
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
        sendQueue.clearPreRoll()
        bargeInTurn = false
        awaitingTurnReadyGeneration = null
        pendingCommitDurationMs = null
        cancelledBargeInResponseId = null
        bargeInState = BargeInState.IDLE
        ttsAudioPlayer.cancel()
        notifyStatus()
        socketToClose?.close(1000, "client_disconnect")
    }

    fun startSession(resumeSessionId: String? = null): Result {
        diagnosticSession.ensureActive()
        synchronized(stateLock) {
            if (status.state != State.CONNECTED) {
                return Result(false, "E_VOICE_STATE", "Connect to the voice gateway first.")
            }
            status = status.copy(state = State.SESSION_STARTING)
        }
        sendQueue.clearPreRoll()
        bargeInTurn = false
        awaitingTurnReadyGeneration = null
        pendingCommitDurationMs = null
        cancelledBargeInResponseId = null
        bargeInState = BargeInState.IDLE
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

    fun startTurn(
        clientTurnId: String? = null,
        includePreRoll: Boolean = false,
    ): Result {
        val preRoll = if (includePreRoll) sendQueue.takePreRoll() else emptyList()
        val nextGeneration: Long
        synchronized(stateLock) {
            val canStartQueuedTurn = status.state == State.STREAMING_AUDIO && !status.turnActive
            if (status.state != State.SESSION_READY && !canStartQueuedTurn) {
                return Result(false, "E_VOICE_STATE", "Start a voice session first.")
            }
            nextGeneration = turnGeneration + 1L
            turnGeneration = nextGeneration
            turnFrameCount = 0
            turnByteCount = 0
            awaitingTurnReadyGeneration = nextGeneration
            pendingCommitDurationMs = null
            bargeInTurn = includePreRoll
            bargeInLivePcmLogged = false
            bargeInState = if (includePreRoll) {
                BargeInState.NEW_TURN_STARTING
            } else {
                BargeInState.IDLE
            }
            status = status.copy(
                state = State.TURN_STARTING,
                turnActive = true,
                turnId = null,
                responseId = null,
            )
        }
        sendQueue.prepareTurn(nextGeneration, preRoll)
        Log.i(
            TAG,
            "VOICE turn start requested clientTurnId=${clientTurnId ?: "NONE"} " +
                "includePreRoll=$includePreRoll " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        if (includePreRoll) {
            if (preRoll.isNotEmpty()) {
                val bytes = preRoll.sumOf { it.payload.size }
                bargeInLog(
                    "BARGE_IN_PREROLL_ATTACHED bytes=$bytes " +
                        "duration_ms=${preRoll.size * 20} " +
                        "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
                )
            }
            bargeInLog(
                "BARGE_IN_NEW_TURN_REQUESTED generation=$nextGeneration " +
                    "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
            )
        }
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
        val timestampMs = SystemClock.elapsedRealtime()
        val turn = synchronized(stateLock) {
            if (!status.turnActive || status.state !in setOf(State.TURN_STARTING, State.STREAMING_AUDIO)) {
                null
            } else {
                Triple(status.turnId, turnGeneration, bargeInTurn)
            }
        }
        sendQueue.rememberForPreRoll(buffer, samplesRead, timestampMs)
        if (turn == null) {
            return false
        }
        val (turnId, generation, _) = turn
        val accepted = if (turnId == null) {
            sendQueue.offerPending(buffer, samplesRead, timestampMs, generation)
        } else {
            sendQueue.offerForTurn(buffer, samplesRead, timestampMs, generation)
        }
        if (accepted && turnId != null) {
            synchronized(stateLock) {
                if (bargeInTurn && bargeInState == BargeInState.NEW_TURN_READY) {
                    transitionBargeInStateLocked(BargeInState.NEW_TURN_RECORDING)
                }
            }
            scheduleDrain()
        } else if (accepted && turnId == null) {
            val pending = sendQueue.pendingSnapshot()
            if (pending.depth == 1 || pending.depth % 50 == 0) {
                bargeInLog(
                    "BARGE_IN_PCM_BUFFERING frames=${pending.depth} " +
                        "bytes=${pending.depth * PcmSendQueue.FRAME_SAMPLES * PcmSendQueue.BYTES_PER_SAMPLE} " +
                        "generation=$generation wallMs=${System.currentTimeMillis()} " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
            }
        } else if (!accepted && turnId == null) {
            val pending = sendQueue.pendingSnapshot()
            if (pending.overflowed) {
                Log.e(
                    TAG,
                    "ERROR_PENDING_TURN_BUFFER_OVERFLOW generation=$generation " +
                        "frames=${pending.depth} dropped=${pending.droppedFrames} " +
                        "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
                )
            }
        }
        notifyStatus()
        return accepted
    }

    fun commitAudio(durationMs: Int): Result {
        val normalizedDuration = durationMs.coerceAtLeast(0)
        val waitForTurnReady: Boolean
        synchronized(stateLock) {
            if (!status.turnActive) {
                return Result(false, "E_VOICE_STATE", "No active voice turn.")
            }
            status = status.copy(turnActive = false)
            waitForTurnReady = status.turnId == null && awaitingTurnReadyGeneration != null
            if (waitForTurnReady) {
                pendingCommitDurationMs = normalizedDuration
                if (bargeInTurn) {
                    transitionBargeInStateLocked(BargeInState.NEW_TURN_COMMITTING)
                }
            }
        }
        Log.i(
            TAG,
            "VOICE audio commit requested durationMs=$durationMs " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        notifyStatus()
        if (waitForTurnReady) {
            bargeInLog(
                "BARGE_IN_COMMIT_WAITING_FOR_TURN_READY wallMs=${System.currentTimeMillis()} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            return Result(true)
        }
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
                    .put("duration_ms", normalizedDuration),
            )
            val current = synchronized(stateLock) { status }
            logLatency(
                sessionId = current.sessionId,
                turnId = current.turnId,
                responseId = current.responseId,
                component = "android",
                event = "native_turn_commit_sent",
                metadata = mapOf(
                    "frame_count" to committed.first,
                    "byte_count" to committed.second,
                ),
            )
            Log.i(
                TAG,
                "VOICE audio commit sent frames=${committed.first} bytes=${committed.second} " +
                    "lastSequence=${committed.third} wallMs=${System.currentTimeMillis()} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            synchronized(stateLock) {
                if (bargeInTurn) {
                    bargeInLog(
                        "BARGE_IN_NEW_TURN_COMMIT_SENT frames=${committed.first} bytes=${committed.second} " +
                            "lastSequence=${committed.third} wallMs=${System.currentTimeMillis()} " +
                            "elapsedMs=${SystemClock.elapsedRealtime()}",
                    )
                    transitionBargeInStateLocked(BargeInState.IDLE)
                    bargeInTurn = false
                    bargeInLivePcmLogged = false
                }
            }
        }
        return Result(true)
    }

    fun cancelResponse(reason: String = "client_requested"): Result {
        val responseId = synchronized(stateLock) { status.responseId }
            ?: return Result(false, "E_VOICE_STATE", "No active response.")
        synchronized(stateLock) {
            status = status.copy(
                state = State.SESSION_READY,
                turnActive = false,
                turnId = null,
                responseId = null,
            )
            if (reason == "barge_in") {
                cancelledBargeInResponseId = responseId
            }
        }
        Log.i(
            TAG,
            "VOICE response cancel requested responseId=$responseId reason=$reason " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        sendQueue.clear()
        ttsAudioPlayer.cancel(runCatching { java.util.UUID.fromString(responseId) }.getOrNull())
        notifyStatus()
        postControl(
            JSONObject()
                .put("type", "client.response.cancel")
                .put("response_id", responseId)
                .put("reason", reason.take(128)),
        )
        return Result(true)
    }

    fun abortAllResponses(reason: String = "abort_all"): Result {
        val current = synchronized(stateLock) { status }
        if (!current.connected || !current.sessionStarted) {
            return Result(false, "E_VOICE_STATE", "The voice session is not ready.")
        }
        synchronized(stateLock) {
            status = status.copy(
                state = State.SESSION_READY,
                turnActive = false,
                turnId = null,
                responseId = null,
            )
        }
        sendQueue.clear()
        sendQueue.clearPreRoll()
        bargeInTurn = false
        awaitingTurnReadyGeneration = null
        pendingCommitDurationMs = null
        cancelledBargeInResponseId = null
        bargeInState = BargeInState.IDLE
        ttsAudioPlayer.cancel()
        notifyStatus()
        postControl(
            JSONObject()
                .put("type", "client.response.abort_all")
                .put("reason", reason.take(128)),
        )
        return Result(true)
    }

    fun resetConversation(): Result {
        val current = synchronized(stateLock) { status }
        if (!current.connected || !current.sessionStarted) {
            return Result(false, "E_VOICE_STATE", "The voice session is not ready.")
        }
        synchronized(stateLock) {
            status = status.copy(
                state = State.SESSION_READY,
                turnActive = false,
                turnId = null,
                responseId = null,
            )
        }
        sendQueue.clear()
        sendQueue.clearPreRoll()
        bargeInTurn = false
        awaitingTurnReadyGeneration = null
        pendingCommitDurationMs = null
        cancelledBargeInResponseId = null
        bargeInState = BargeInState.IDLE
        ttsAudioPlayer.cancel()
        notifyStatus()
        postControl(JSONObject().put("type", "client.conversation.reset"))
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
        sendQueue.clearPreRoll()
        bargeInTurn = false
        notifyStatus()
        postControl(JSONObject().put("type", "client.session.end").put("reason", reason.take(128)))
        return Result(true)
    }

    fun getStatus(): Status = synchronized(stateLock) {
        status.copyFromQueue(sendQueue.snapshot(), sendQueue.pendingSnapshot()).copy(
            diagnosticSessionId = diagnosticSession.currentId() ?: DiagnosticSessionContext.NONE,
        )
    }

    fun stopTtsPlayback() {
        ttsAudioPlayer.cancel()
    }

    fun shutdown() {
        stopHeartbeat()
        disconnect()
        networkExecutor.shutdownNow()
        heartbeatScheduler.shutdownNow()
        ttsFrameExecutor.shutdownNow()
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
            playbackEchoReference.reset()
            sendQueue.clear()
            sendQueue.clearPreRoll()
            bargeInTurn = false
            awaitingTurnReadyGeneration = null
            pendingCommitDurationMs = null
            bargeInState = BargeInState.IDLE
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
            sendQueue.clearPreRoll()
            bargeInTurn = false
            awaitingTurnReadyGeneration = null
            pendingCommitDurationMs = null
            cancelledBargeInResponseId = null
            bargeInState = BargeInState.IDLE
            ttsAudioPlayer.cancel()
            playbackEchoReference.reset()
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
            playbackEchoReference.reset()
            sendQueue.clear()
            sendQueue.clearPreRoll()
            bargeInTurn = false
            awaitingTurnReadyGeneration = null
            pendingCommitDurationMs = null
            cancelledBargeInResponseId = null
            bargeInState = BargeInState.IDLE
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
        val messageType = message.optString("type", "unknown")
        Log.i(
            TAG,
            "VOICE control sent type=$messageType " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        val sent = webSocket?.send(message.toString()) == true
        if (!sent) {
            recordError("E_VOICE_SEND", "Voice gateway message could not be sent.")
        }
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
            val frame = sendQueue.poll() ?: break
            if (frame.ownerTurnId.isNullOrBlank()) {
                Log.e(
                    TAG,
                    "ERROR_PCM_WITHOUT_TURN generation=${frame.generation} " +
                        "sequence=${frame.sequenceNo} wallMs=${System.currentTimeMillis()} " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                continue
            }
            val currentTurnId = synchronized(stateLock) { status.turnId }
            if (currentTurnId != frame.ownerTurnId) {
                Log.e(
                    TAG,
                    "ERROR_PCM_TO_OLD_TURN frameTurnId=${frame.ownerTurnId} " +
                        "currentTurnId=${currentTurnId ?: "NONE"} generation=${frame.generation} " +
                        "sequence=${frame.sequenceNo} wallMs=${System.currentTimeMillis()} " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                continue
            }
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
                if (bargeInTurn) {
                    if (!bargeInLivePcmLogged) {
                        bargeInLog(
                            "BARGE_IN_LIVE_PCM_STARTED turnId=${frame.ownerTurnId} " +
                                "generation=${frame.generation} wallMs=${System.currentTimeMillis()} " +
                                "elapsedMs=${SystemClock.elapsedRealtime()}",
                        )
                        bargeInLivePcmLogged = true
                    }
                    bargeInLog(
                        "BARGE_IN_PCM_FORWARDING turnId=${frame.ownerTurnId} " +
                            "seq=${frame.sequenceNo} bytes=${frame.payload.size} " +
                            "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
                    )
                }
            }
        }
        maybeSendPendingCommit()
    }

    private fun maybeSendPendingCommit() {
        val durationMs = synchronized(stateLock) {
            if (pendingCommitDurationMs != null && status.turnId != null && !status.turnActive) {
                val value = pendingCommitDurationMs
                pendingCommitDurationMs = null
                value
            } else {
                null
            }
        } ?: return
        val committed = synchronized(stateLock) {
            Triple(turnFrameCount, turnByteCount, maxOf(0L, turnFrameCount - 1))
        }
        sendControlNow(
            JSONObject()
                .put("type", "client.audio.commit")
                .put("last_sequence_no", committed.third)
                .put("frame_count", committed.first)
                .put("byte_count", committed.second)
                .put("duration_ms", durationMs),
        )
        bargeInLog(
            "BARGE_IN_NEW_TURN_COMMIT_SENT frames=${committed.first} bytes=${committed.second} " +
                "lastSequence=${committed.third} wallMs=${System.currentTimeMillis()} " +
                "elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        synchronized(stateLock) {
            if (bargeInTurn) {
                transitionBargeInStateLocked(BargeInState.IDLE)
                bargeInTurn = false
                bargeInLivePcmLogged = false
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
        if (eventType == "transcript.final" || eventType == "voice.transcript.final.delivered") {
            logLatency(
                sessionId = sessionId ?: synchronized(stateLock) { status.sessionId },
                turnId = turnId ?: synchronized(stateLock) { status.turnId },
                responseId = responseId ?: synchronized(stateLock) { status.responseId },
                component = "android",
                event = "client_stt_final_received",
                metadata = mapOf("transcript_sequence" to payload?.transcriptSequence),
            )
        }
        val traceResponseId = responseId ?: synchronized(stateLock) { status.responseId }
        val firstAssistantText = eventType == "assistant.text.delta" &&
            !payload?.delta.isNullOrBlank() && traceResponseId != null &&
            synchronized(stateLock) {
                if (firstAssistantTextResponseId == traceResponseId) {
                    false
                } else {
                    firstAssistantTextResponseId = traceResponseId
                    true
                }
            }
        if (firstAssistantText) {
            logLatency(
                sessionId = sessionId ?: synchronized(stateLock) { status.sessionId },
                turnId = turnId ?: synchronized(stateLock) { status.turnId },
                responseId = traceResponseId,
                component = "android",
                event = "first_assistant_token_received",
                metadata = mapOf("delta_characters" to payload?.delta?.length),
            )
        }
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
        val readyGeneration = if (eventType == "server.turn.ready" && !turnId.isNullOrBlank()) {
            synchronized(stateLock) { awaitingTurnReadyGeneration }
        } else {
            null
        }
        val binding = if (readyGeneration != null && turnId != null) {
            Log.i(
                TAG,
                "SERVER_TURN_READY turnId=$turnId responseId=${responseId ?: "NONE"} " +
                    "generation=$readyGeneration wallMs=${System.currentTimeMillis()} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            sendQueue.bindTurn(turnId, readyGeneration)
        } else {
            null
        }
        val wasBargeInTurn = synchronized(stateLock) { bargeInTurn }
        val delayedOldBargeInCancellation = eventType == "response.cancelled" &&
            responseId != null &&
            responseId == cancelledBargeInResponseId &&
            synchronized(stateLock) {
                awaitingTurnReadyGeneration != null || status.turnId != null
            }
        synchronized(stateLock) {
            status = if (delayedOldBargeInCancellation) {
                status.copy(
                    lastServerEvent = eventType,
                    lastServerEventTimestampMs = System.currentTimeMillis(),
                )
            } else {
                status.copy(
                    state = when (eventType) {
                        "server.session.ready" -> State.SESSION_READY
                        "server.turn.ready" -> State.STREAMING_AUDIO
                        "server.turn.completed" -> State.SESSION_READY
                        "response.cancelled" -> State.SESSION_READY
                        "server.session.ended" -> State.DISCONNECTED
                        "server.error" -> State.ERROR
                        else -> status.state
                    },
                    connected = eventType != "server.session.ended" &&
                        eventType != "server.error" && status.connected,
                    sessionStarted = !terminalSessionEvent &&
                        (status.sessionStarted || eventType == "server.session.ready"),
                    turnActive = when (eventType) {
                        "server.turn.completed", "server.error", "response.cancelled" -> false
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
        }
        if (binding != null) {
            if (wasBargeInTurn) {
                bargeInLog(
                    "BARGE_IN_BUFFER_FLUSH_STARTED frames=${binding.frames} bytes=${binding.bytes} " +
                        "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
                )
            }
            synchronized(stateLock) {
                awaitingTurnReadyGeneration = null
                if (bargeInTurn) {
                    transitionBargeInStateLocked(BargeInState.NEW_TURN_READY)
                }
            }
            bargeInLog(
                "BARGE_IN_NEW_TURN_BOUND turnId=$turnId frames=${binding.frames} " +
                    "bytes=${binding.bytes} wallMs=${System.currentTimeMillis()} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            bargeInLog(
                "BARGE_IN_BUFFER_FLUSH_COMPLETED frames=${binding.frames} bytes=${binding.bytes} " +
                    "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
            )
            scheduleDrain()
        } else if (eventType == "server.turn.ready" && readyGeneration != null) {
            Log.e(
                TAG,
                "ERROR_TURN_BINDING_FAILED turnId=${turnId ?: "NONE"} " +
                    "generation=$readyGeneration wallMs=${System.currentTimeMillis()} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
        }
        if (binding != null && wasBargeInTurn) {
            bargeInLog(
                "BARGE_IN_NEW_TURN_CREATED turnId=${turnId ?: "NONE"} " +
                    "responseId=${responseId ?: "NONE"} " +
                    "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
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
            activeTtsFirstAudioElapsedNs = null
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
            activeTtsFirstAudioElapsedNs = SystemClock.elapsedRealtimeNanos()
            Log.i(
                TAG,
                "TTS_FIRST_AUDIO_RECEIVED response_id=${frame.responseId} seq=${frame.sequence} " +
                    "bytes=${frame.payload.size} sample_rate=${frame.sampleRateHz} " +
                    "channels=1 encoding=PCM16 elapsedMs=${activeTtsFirstAudioElapsedMs}",
            )
        }
        if (frame.sequence == 0L) {
            logLatency(
                sessionId = synchronized(stateLock) { status.sessionId },
                turnId = synchronized(stateLock) { status.turnId },
                responseId = frame.responseId.toString(),
                component = "android",
                event = "tts_first_chunk_received",
                metadata = mapOf("bytes" to frame.payload.size),
            )
        }
        if (frame.endsResponse && activeTtsFirstAudioElapsedNs != null) {
            val streamDurationMs =
                (SystemClock.elapsedRealtimeNanos() - activeTtsFirstAudioElapsedNs!!) / 1_000_000.0
            logLatency(
                sessionId = synchronized(stateLock) { status.sessionId },
                turnId = synchronized(stateLock) { status.turnId },
                responseId = frame.responseId.toString(),
                component = "android",
                event = "tts_stream_completed",
                durationMs = streamDurationMs,
                metadata = mapOf("duration_basis" to "android_elapsed_realtime"),
            )
        }
        if (!isTtsOutputEnabled()) return
        if (frame.startsResponse) {
            if (!ttsAudioPlayer.start(frame.responseId, frame.sampleRateHz)) {
                return
            }
        }
        try {
            // TtsAudioPlayer may wait for its bounded PCM queue to drain. Keep
            // that wait off OkHttp's WebSocket callback thread so control
            // messages and application heartbeats remain responsive.
            ttsFrameExecutor.execute {
                if (!ttsAudioPlayer.write(frame.responseId, frame.sequence, frame.payload)) {
                    if (!ttsAudioPlayer.isActive(frame.responseId)) {
                        Log.w(
                            TAG,
                            "TTS_STALE_FRAME response_id=${frame.responseId} sequence=${frame.sequence} " +
                                "reason=playback_inactive",
                        )
                        return@execute
                    }
                    recordError("E_TTS_PLAYBACK", "TTS PCM could not be queued for playback.")
                    return@execute
                }
                if (frame.endsResponse) {
                    ttsAudioPlayer.finish(frame.responseId)
                }
            }
        } catch (_: RejectedExecutionException) {
            recordError("E_TTS_PLAYBACK", "TTS PCM worker is not available.")
        }
    }

    private fun notifyTtsPlayback(eventType: String, responseId: java.util.UUID) {
        val current = synchronized(stateLock) { status }
        val nowNs = SystemClock.elapsedRealtimeNanos()
        val durationMs = if (eventType == "tts.playback.completed") {
            activeTtsPlaybackStartedNs?.let { (nowNs - it) / 1_000_000.0 }
        } else {
            null
        }
        if (eventType == "tts.playback.started") {
            activeTtsPlaybackStartedNs = nowNs
        } else if (eventType == "tts.playback.completed" || eventType == "tts.playback.stopped") {
            activeTtsPlaybackStartedNs = null
        }
        logLatency(
            sessionId = current.sessionId,
            turnId = current.turnId,
            responseId = responseId.toString(),
            component = "android",
            event = eventType.replace('.', '_'),
            durationMs = durationMs,
            metadata = mapOf("duration_basis" to "android_elapsed_realtime"),
        )
        listener.onTtsPlayback(
            eventType,
            current.sessionId,
            current.turnId,
            responseId.toString(),
            System.currentTimeMillis(),
        )
    }

    private fun logLatency(
        sessionId: String?,
        turnId: String?,
        responseId: String?,
        component: String,
        event: String,
        durationMs: Double? = null,
        metadata: Map<String, Any?> = emptyMap(),
    ) {
        val wallTimeUtc = java.time.Instant.now().toString()
        val record = JSONObject()
            .put("timestamp", wallTimeUtc)
            .put("wall_time_utc", wallTimeUtc)
            .put("timestamp_ms", System.currentTimeMillis())
            .put("monotonic_ns", SystemClock.elapsedRealtimeNanos())
            .put("monotonic_ms", SystemClock.elapsedRealtime())
            .put("clock_domain", "android_elapsed_realtime")
            .put("process", "android:com.voiceaipoc")
            .put("session_id", sessionId ?: JSONObject.NULL)
            .put("turn_id", turnId ?: JSONObject.NULL)
            .put("response_id", responseId ?: JSONObject.NULL)
            .put("component", component)
            .put("event", event)
            .put("duration_ms", durationMs ?: JSONObject.NULL)
            .put("metadata", JSONObject())
        if (metadata.isNotEmpty()) {
            val safe = JSONObject()
            metadata.forEach { (key, value) -> safe.put(key, value ?: JSONObject.NULL) }
            record.put("metadata", safe)
        }
        Log.i(TAG, "LATENCY_TRACE $record")
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
            "assistant.thinking",
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
        val isTool = eventType == "tool.status" ||
            eventType == "confirmation.required" ||
            eventType == "confirmation.resolved"
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
            status = json.optStringOrNull("status")
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
        sendQueue.clearPreRoll()
        bargeInTurn = false
        awaitingTurnReadyGeneration = null
        pendingCommitDurationMs = null
        cancelledBargeInResponseId = null
        bargeInState = BargeInState.IDLE
        notifyStatus()
    }

    private fun notifyStatus() {
        listener.onStatus(getStatus())
    }

    private fun ttsLogInfo(message: String) {
        Log.i(TAG, diagnosticSession.tag(message))
    }

    private fun ttsLogError(message: String) {
        Log.e(TAG, diagnosticSession.tag(message))
    }

    private fun bargeInLog(message: String) {
        Log.i(TAG, diagnosticSession.tag(message))
    }

    private fun transitionBargeInStateLocked(next: BargeInState) {
        if (bargeInState == next) return
        bargeInLog(
            "BARGE_IN_STATE from=${bargeInState.name} to=${next.name} " +
                "wallMs=${System.currentTimeMillis()} elapsedMs=${SystemClock.elapsedRealtime()}",
        )
        bargeInState = next
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

    private fun Status.copyFromQueue(
        snapshot: PcmSendQueue.Snapshot,
        pending: PcmSendQueue.PendingSnapshot,
    ): Status = copy(
        framesQueued = snapshot.depth + pending.depth,
        queueHighWaterMark = maxOf(snapshot.highWaterMark, pending.highWaterMark),
        droppedFrames = snapshot.droppedFrames + pending.droppedFrames,
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
