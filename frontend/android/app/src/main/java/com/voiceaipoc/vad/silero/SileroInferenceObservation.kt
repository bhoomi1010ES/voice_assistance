package com.voiceaipoc.vad.silero

/**
 * Metadata for exactly the capture interval consumed by one Silero call.
 * Numeric reference fields are sample-weighted when an inference spans
 * multiple 20 ms frames.
 */
data class SileroInferenceObservation(
    val inferenceIndex: Long,
    val sourceFrameSequenceStart: Long,
    val sourceFrameSequenceEnd: Long,
    val captureStartNs: Long,
    val captureEndNs: Long,
    val probability: Float,
    val playbackState: String,
    val playbackActive: Boolean,
    val playbackPositionMs: Long,
    val playbackResponseId: String?,
    val referenceReady: Boolean,
    val referenceConfidence: String,
    val estimatedEchoDelayMs: Int?,
    val echoSimilarity: Double?,
    val echoLagMs: Int?,
    val echoCoherence: Double?,
    val farEndRms: Double?,
    val micRms: Double?,
    val nearEndResidualRatio: Double?,
    val echoLikely: Boolean,
    val discontinuous: Boolean,
    val monotonicTimestampNs: Long,
    val nearEndFarEndEnergyRatio: Double? = null,
)
