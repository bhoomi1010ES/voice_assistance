package com.voiceaipoc.vad

/**
 * Tunable policy for [PlaybackAwareBargeInDetector].  The policy is expressed
 * in terms of native capture/presentation time and energy metadata; it never
 * depends on a React Native timer or on raw PCM crossing the bridge.
 */
data class BargeInConfig(
    val playbackGuardMs: Long = 1_200L,
    val earlyBargeInEnabled: Boolean = false,
    val confirmationMs: Long = 480L,
    val degradedConfirmationMs: Long = 640L,
    val normalSpeechProbabilityThreshold: Float = 0.50f,
    val playbackSpeechProbabilityThreshold: Float = 0.75f,
    val degradedSpeechProbabilityThreshold: Float = 0.90f,
    val minimumMicRms: Double = 600.0,
    val minimumFarEndRms: Double = 180.0,
    val minimumResidualRatio: Double = 0.18,
    val maximumEchoResidualRatio: Double = 0.35,
    val echoSimilarityThreshold: Double = 0.58,
    val echoCoherenceThreshold: Double = 0.65,
    val farEndDominanceRatio: Double = 1.15,
) {
    /** Early rollout is intentionally capped at the documented 250 ms goal. */
    val effectivePlaybackGuardMs: Long
        get() = if (earlyBargeInEnabled) minOf(playbackGuardMs, 250L) else playbackGuardMs

    companion object {
        const val REASON_NORMAL_SPEECH = "normal_speech"
        const val REASON_PLAYBACK_GUARD = "playback_guard"
        const val REASON_REFERENCE_NOT_READY = "reference_not_ready"
        const val REASON_REFERENCE_TIMING_UNRELIABLE = "reference_timing_unreliable"
        const val REASON_FAR_END_DOMINANT = "far_end_dominant"
        const val REASON_ECHO_SIMILARITY_HIGH = "echo_similarity_high"
        const val REASON_NEAR_END_ENERGY_INSUFFICIENT = "near_end_energy_insufficient"
        const val REASON_SILERO_PROBABILITY_LOW = "silero_probability_low"
        const val REASON_CONFIRMATION_INCOMPLETE = "confirmation_incomplete"
        const val REASON_NEAR_END_CONFIRMED = "near_end_confirmed"
        const val REASON_QUEUE_DISCONTINUITY = "queue_discontinuity"
        const val REASON_AEC_UNAVAILABLE = "aec_unavailable"
        const val REASON_NO_SAFE_ACOUSTIC_PATH = "no_safe_acoustic_path"

        const val TIMESTAMP_AUDIO = "AUDIO_TIMESTAMP"
        const val TIMESTAMP_PLAYBACK_HEAD = "PLAYBACK_HEAD_FALLBACK"
        const val TIMESTAMP_WRITE = "WRITE_TIME_FALLBACK"
        const val TIMESTAMP_NONE = "NONE"

        fun isReliableReferenceConfidence(value: String): Boolean =
            value == TIMESTAMP_AUDIO || value == TIMESTAMP_PLAYBACK_HEAD
    }
}

data class BargeInRouteHealth(
    val communicationModeActive: Boolean = true,
    val aecAvailable: Boolean = true,
    val aecEnabled: Boolean = true,
    val aecEffectiveness: String = "ENABLED",
    val automaticLoudspeakerBargeInAllowed: Boolean = true,
)

/** Metadata passed from one native Silero inference observation. */
data class BargeInInput(
    val event: String,
    val monotonicTimestampNs: Long,
    val captureStartNs: Long,
    val captureEndNs: Long,
    val probability: Float,
    val playbackState: String,
    val playbackActive: Boolean,
    val playbackResponseId: String?,
    val playbackPositionMs: Long,
    val referenceReady: Boolean,
    val referenceConfidence: String,
    val echoLikely: Boolean,
    val estimatedDelayMs: Int? = null,
    val echoSimilarity: Double?,
    val echoCoherence: Double?,
    val farEndRms: Double?,
    val micRms: Double?,
    val nearEndResidualRatio: Double?,
    val nearEndFarEndEnergyRatio: Double? = null,
    val discontinuous: Boolean,
    val routeHealth: BargeInRouteHealth = BargeInRouteHealth(),
    val sourceFrameSequenceStart: Long = -1L,
    val sourceFrameSequenceEnd: Long = -1L,
    val inferenceIndex: Long = -1L,
)
