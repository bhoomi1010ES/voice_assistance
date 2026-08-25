package com.voiceaipoc.wakeword

/** Pure deterministic wake detection/cooldown state machine. */
internal class WakeWordStateMachine(
    private val config: WakeWordConfig,
    private val monotonicClockMs: () -> Long,
    private val wallClockMs: () -> Long,
) {
    enum class State {
        IDLE,
        LISTENING,
        WAKE_DETECTED,
        COOLDOWN,
        STOPPED,
        ERROR,
    }

    data class Detection(
        val timestampMs: Long,
        val confidence: Float,
        val detectionCount: Long,
        val modelName: String,
    )

    data class Status(
        val state: State,
        val detectionCount: Long,
        val duplicateSuppressionCount: Long,
        val lastDetectionTimestampMs: Long,
        val lastConfidence: Float?,
        val cooldownRemainingMs: Long,
    )

    private val lock = Any()
    private var state = State.IDLE
    private var detectionCount = 0L
    private var duplicateSuppressionCount = 0L
    private var lastDetectionTimestampMs = 0L
    private var lastConfidence: Float? = null
    private var cooldownUntilMs = 0L

    fun reset() = synchronized(lock) {
        state = State.IDLE
        detectionCount = 0L
        duplicateSuppressionCount = 0L
        lastDetectionTimestampMs = 0L
        lastConfidence = null
        cooldownUntilMs = 0L
    }

    fun startListening() = synchronized(lock) {
        state = State.LISTENING
        cooldownUntilMs = 0L
    }

    fun stop() = synchronized(lock) {
        state = State.STOPPED
        cooldownUntilMs = 0L
    }

    fun fail() = synchronized(lock) {
        state = State.ERROR
        cooldownUntilMs = 0L
    }

    fun onConfidence(confidence: Float): Detection? = synchronized(lock) {
        require(confidence.isFinite() && confidence in 0.0f..1.0f) {
            "confidence must be finite and in [0, 1]"
        }

        val now = monotonicClockMs()
        advanceBeforeInferenceLocked(now)
        lastConfidence = confidence

        if (state == State.COOLDOWN) {
            if (confidence >= config.detectionThreshold) {
                duplicateSuppressionCount += 1L
            }
            return null
        }
        if (state != State.LISTENING || confidence < config.detectionThreshold) {
            return null
        }

        state = State.WAKE_DETECTED
        detectionCount += 1L
        lastDetectionTimestampMs = wallClockMs()
        cooldownUntilMs = now + config.cooldownMs
        Detection(
            timestampMs = lastDetectionTimestampMs,
            confidence = confidence,
            detectionCount = detectionCount,
            modelName = config.modelName,
        )
    }

    fun getStatus(): Status = synchronized(lock) {
        val now = monotonicClockMs()
        Status(
            state = state,
            detectionCount = detectionCount,
            duplicateSuppressionCount = duplicateSuppressionCount,
            lastDetectionTimestampMs = lastDetectionTimestampMs,
            lastConfidence = lastConfidence,
            cooldownRemainingMs = if (state == State.COOLDOWN) {
                (cooldownUntilMs - now).coerceAtLeast(0L)
            } else {
                0L
            },
        )
    }

    private fun advanceBeforeInferenceLocked(now: Long) {
        if (state == State.WAKE_DETECTED) {
            state = State.COOLDOWN
        }
        if (state == State.COOLDOWN && now >= cooldownUntilMs) {
            state = State.LISTENING
            cooldownUntilMs = 0L
        }
    }
}
