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

    @Synchronized
    fun onProbability(probability: Float, inferenceIndex: Long): Transition? {
        require(probability.isFinite() && probability in 0f..1f) {
            "Silero speech probability must be finite and in [0, 1]"
        }
        require(inferenceIndex > 0L) { "inferenceIndex must be positive" }

        lastProbability = probability
        decisionsProcessed += 1L
        val speech = probability >= config.speechProbabilityThreshold

        return when (state) {
            State.SILENCE -> handleSilence(speech, probability, inferenceIndex)
            State.SPEECH_START_PENDING -> handleSpeechStartPending(
                speech,
                probability,
                inferenceIndex,
            )
            State.SPEECH -> handleSpeech(speech)
            State.SPEECH_STOP_PENDING -> handleSpeechStopPending(
                speech,
                probability,
                inferenceIndex,
            )
        }
    }

    @Synchronized
    fun stop(inferenceIndex: Long): Transition? {
        val shouldEmitStop = state == State.SPEECH || state == State.SPEECH_STOP_PENDING
        val transition = if (shouldEmitStop) {
            speechStopCount += 1L
            Transition(
                event = SileroVadEngine.EVENT_SPEECH_STOPPED,
                timestampMs = wallClockMs(),
                probability = lastProbability ?: 0f,
                inferenceIndex = inferenceIndex,
                speechDurationMs = speechDurationMs(inferenceIndex),
                reason = "SESSION_STOPPED",
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

    private fun handleSilence(
        speech: Boolean,
        probability: Float,
        inferenceIndex: Long,
    ): Transition? {
        if (!speech) {
            consecutiveSpeechChunks = 0
            return null
        }
        pendingSpeechStartIndex = inferenceIndex
        consecutiveSpeechChunks = 1
        return confirmSpeechIfReady(probability, inferenceIndex)
    }

    private fun handleSpeechStartPending(
        speech: Boolean,
        probability: Float,
        inferenceIndex: Long,
    ): Transition? {
        if (!speech) {
            state = State.SILENCE
            consecutiveSpeechChunks = 0
            pendingSpeechStartIndex = 0L
            return null
        }
        consecutiveSpeechChunks += 1
        return confirmSpeechIfReady(probability, inferenceIndex)
    }

    private fun confirmSpeechIfReady(
        probability: Float,
        inferenceIndex: Long,
    ): Transition? {
        if (consecutiveSpeechChunks < config.speechStartConfirmationChunks) {
            state = State.SPEECH_START_PENDING
            return null
        }
        state = State.SPEECH
        speechStartIndex = pendingSpeechStartIndex
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
        val durationMs = speechDurationMs(inferenceIndex)
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
        )
    }

    private fun speechDurationMs(inferenceIndex: Long): Long {
        if (speechStartIndex <= 0L || inferenceIndex < speechStartIndex) {
            return 0L
        }
        return (inferenceIndex - speechStartIndex + 1L) * config.inferenceChunkDurationMs
    }

    private fun resetTransientState() {
        state = State.SILENCE
        consecutiveSpeechChunks = 0
        consecutiveSilenceChunks = 0
        pendingSpeechStartIndex = 0L
        speechStartIndex = 0L
    }
}
