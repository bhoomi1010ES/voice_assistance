package com.voiceaipoc.vad.silero

/** Deterministic probability-to-speech state machine, independent of ONNX. */
class SileroVadStateMachine(
    private val config: SileroVadConfig,
    private val wallClockMs: () -> Long = System::currentTimeMillis,
) {
    enum class State {
        SILENCE,
        SPEECH_START_PENDING,
        SPEECH,
        SPEECH_STOP_PENDING,
    }

    data class Transition(
        val event: String,
        val timestampMs: Long,
        val probability: Float,
        val inferenceIndex: Long,
        val speechDurationMs: Long,
        val reason: String,
        val monotonicTimestampNs: Long = 0L,
        val captureStartNs: Long = 0L,
        val captureEndNs: Long = 0L,
    )

    data class Status(
        val state: State,
        val lastProbability: Float?,
        val consecutiveSpeechChunks: Int,
        val consecutiveSilenceChunks: Int,
        val speechStartCount: Long,
        val speechStopCount: Long,
        val decisionsProcessed: Long,
    )

    private var state = State.SILENCE
    private var lastProbability: Float? = null
    private var consecutiveSpeechChunks = 0
    private var consecutiveSilenceChunks = 0
    private var speechStartCount = 0L
    private var speechStopCount = 0L
    private var decisionsProcessed = 0L
    private var pendingSpeechStartIndex = 0L
    private var speechStartIndex = 0L
    private var pendingSpeechStartCaptureNs = NO_CAPTURE_TIMESTAMP
    private var speechStartCaptureNs = NO_CAPTURE_TIMESTAMP

    @Synchronized
    fun onProbability(
        probability: Float,
        inferenceIndex: Long,
        captureStartNs: Long = syntheticCaptureStartNs(inferenceIndex),
        captureEndNs: Long = syntheticCaptureEndNs(inferenceIndex),
    ): Transition? {
        require(probability.isFinite() && probability in 0f..1f) {
            "Silero speech probability must be finite and in [0, 1]"
        }
        require(inferenceIndex > 0L) { "inferenceIndex must be positive" }

        lastProbability = probability
        decisionsProcessed += 1L
        val speech = probability >= config.speechProbabilityThreshold

        return when (state) {
            State.SILENCE -> handleSilence(
                speech,
                probability,
                inferenceIndex,
                captureStartNs,
                captureEndNs,
            )
            State.SPEECH_START_PENDING -> handleSpeechStartPending(
                speech,
                probability,
                inferenceIndex,
                captureStartNs,
                captureEndNs,
            )
            State.SPEECH -> handleSpeech(speech)
            State.SPEECH_STOP_PENDING -> handleSpeechStopPending(
                speech,
                probability,
                inferenceIndex,
                captureStartNs,
                captureEndNs,
            )
        }
    }

    @Synchronized
    fun stop(
        inferenceIndex: Long,
        captureStartNs: Long = syntheticCaptureStartNs(inferenceIndex),
        captureEndNs: Long = syntheticCaptureEndNs(inferenceIndex),
    ): Transition? {
        val shouldEmitStop = state == State.SPEECH || state == State.SPEECH_STOP_PENDING
        val transition = if (shouldEmitStop) {
            speechStopCount += 1L
            Transition(
                event = SileroVadEngine.EVENT_SPEECH_STOPPED,
                timestampMs = wallClockMs(),
                probability = lastProbability ?: 0f,
                inferenceIndex = inferenceIndex,
                speechDurationMs = speechDurationMs(inferenceIndex, captureEndNs),
                reason = "SESSION_STOPPED",
                monotonicTimestampNs = captureEndNs,
                captureStartNs = captureStartNs,
                captureEndNs = captureEndNs,
            )
        } else {
            null
        }
        resetTransientState()
        return transition
    }

    @Synchronized
    fun reset() {
        resetTransientState()
        speechStartCount = 0L
        speechStopCount = 0L
        decisionsProcessed = 0L
        lastProbability = null
    }

    @Synchronized
    fun getStatus(): Status = Status(
        state = state,
        lastProbability = lastProbability,
        consecutiveSpeechChunks = consecutiveSpeechChunks,
        consecutiveSilenceChunks = consecutiveSilenceChunks,
        speechStartCount = speechStartCount,
        speechStopCount = speechStopCount,
        decisionsProcessed = decisionsProcessed,
    )

    /** Returns the duration of the current confirmed speech segment. */
    @Synchronized
    fun currentSpeechDurationMs(
        inferenceIndex: Long,
        captureEndNs: Long = syntheticCaptureEndNs(inferenceIndex),
    ): Long {
        return if (state == State.SPEECH || state == State.SPEECH_STOP_PENDING) {
            speechDurationMs(inferenceIndex, captureEndNs)
        } else {
            0L
        }
    }

    private fun handleSilence(
        speech: Boolean,
        probability: Float,
        inferenceIndex: Long,
        captureStartNs: Long,
        captureEndNs: Long,
    ): Transition? {
        if (!speech) {
            consecutiveSpeechChunks = 0
            return null
        }
        pendingSpeechStartIndex = inferenceIndex
        pendingSpeechStartCaptureNs = captureStartNs
        consecutiveSpeechChunks = 1
        return confirmSpeechIfReady(probability, inferenceIndex, captureStartNs, captureEndNs)
    }

    private fun handleSpeechStartPending(
        speech: Boolean,
        probability: Float,
        inferenceIndex: Long,
        captureStartNs: Long,
        captureEndNs: Long,
    ): Transition? {
        if (!speech) {
            state = State.SILENCE
            consecutiveSpeechChunks = 0
            pendingSpeechStartIndex = 0L
            pendingSpeechStartCaptureNs = NO_CAPTURE_TIMESTAMP
            return null
        }
        consecutiveSpeechChunks += 1
        return confirmSpeechIfReady(probability, inferenceIndex, captureStartNs, captureEndNs)
    }

    private fun confirmSpeechIfReady(
        probability: Float,
        inferenceIndex: Long,
        captureStartNs: Long,
        captureEndNs: Long,
    ): Transition? {
        if (consecutiveSpeechChunks < config.speechStartConfirmationChunks) {
            state = State.SPEECH_START_PENDING
            return null
        }
        state = State.SPEECH
        speechStartIndex = pendingSpeechStartIndex
        speechStartCaptureNs = pendingSpeechStartCaptureNs
        speechStartCount += 1L
        consecutiveSpeechChunks = 0
        consecutiveSilenceChunks = 0
        return Transition(
            event = SileroVadEngine.EVENT_SPEECH_STARTED,
            timestampMs = wallClockMs(),
            probability = probability,
            inferenceIndex = inferenceIndex,
            speechDurationMs = 0L,
            reason = "SPEECH_CONFIRMED",
            monotonicTimestampNs = captureEndNs,
            captureStartNs = captureStartNs,
            captureEndNs = captureEndNs,
        )
    }

    private fun handleSpeech(speech: Boolean): Transition? {
        if (speech) {
            consecutiveSilenceChunks = 0
            return null
        }
        state = State.SPEECH_STOP_PENDING
        consecutiveSilenceChunks = 1
        return null
    }

    private fun handleSpeechStopPending(
        speech: Boolean,
        probability: Float,
        inferenceIndex: Long,
        captureStartNs: Long,
        captureEndNs: Long,
    ): Transition? {
        if (speech) {
            state = State.SPEECH
            consecutiveSilenceChunks = 0
            return null
        }
        consecutiveSilenceChunks += 1
        if (consecutiveSilenceChunks < config.speechStopConfirmationChunks) {
            return null
        }

        speechStopCount += 1L
        val durationMs = speechDurationMs(inferenceIndex, captureEndNs)
        state = State.SILENCE
        consecutiveSpeechChunks = 0
        consecutiveSilenceChunks = 0
        speechStartIndex = 0L
        pendingSpeechStartIndex = 0L
        return Transition(
            event = SileroVadEngine.EVENT_SPEECH_STOPPED,
            timestampMs = wallClockMs(),
            probability = probability,
            inferenceIndex = inferenceIndex,
            speechDurationMs = durationMs,
            reason = "SILENCE_CONFIRMED",
            monotonicTimestampNs = captureEndNs,
            captureStartNs = captureStartNs,
            captureEndNs = captureEndNs,
        )
    }

    private fun speechDurationMs(inferenceIndex: Long, captureEndNs: Long): Long {
        if (speechStartIndex <= 0L || inferenceIndex < speechStartIndex) {
            return 0L
        }
        if (speechStartCaptureNs != NO_CAPTURE_TIMESTAMP && captureEndNs >= speechStartCaptureNs) {
            return (captureEndNs - speechStartCaptureNs) / NANOS_PER_MILLISECOND
        }
        return (inferenceIndex - speechStartIndex + 1L) * config.inferenceChunkDurationMs
    }

    private fun resetTransientState() {
        state = State.SILENCE
        consecutiveSpeechChunks = 0
        consecutiveSilenceChunks = 0
        pendingSpeechStartIndex = 0L
        speechStartIndex = 0L
        pendingSpeechStartCaptureNs = NO_CAPTURE_TIMESTAMP
        speechStartCaptureNs = NO_CAPTURE_TIMESTAMP
    }

    private fun syntheticCaptureStartNs(inferenceIndex: Long): Long =
        (inferenceIndex - 1L).coerceAtLeast(0L) *
            config.inferenceChunkDurationMs.toLong() * NANOS_PER_MILLISECOND

    private fun syntheticCaptureEndNs(inferenceIndex: Long): Long =
        inferenceIndex.coerceAtLeast(0L) *
            config.inferenceChunkDurationMs.toLong() * NANOS_PER_MILLISECOND

    private companion object {
        const val NANOS_PER_MILLISECOND = 1_000_000L
        const val NO_CAPTURE_TIMESTAMP = Long.MIN_VALUE
    }
}
