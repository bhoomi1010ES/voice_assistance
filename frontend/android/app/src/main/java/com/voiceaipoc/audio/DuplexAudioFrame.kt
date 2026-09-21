package com.voiceaipoc.audio

/**
 * A capture interval together with the presentation-timed far-end evidence
 * observed for that interval.
 *
 * This is intentionally a passive value object. It does not own a queue;
 * callers that need a durable PCM copy
 * must copy [pcm16] before handing the frame to another owner.
 */
data class DuplexAudioFrame(
    val frameSequence: Long,
    val captureStartNs: Long,
    val captureEndNs: Long,
    val pcm16: ShortArray,
    val playbackState: FarEndReferenceBuffer.State,
    val playbackResponseId: String?,
    val referenceReady: Boolean,
    val referenceConfidence: String,
    val estimatedEchoDelayMs: Int?,
    val echoSimilarity: Double?,
    val farEndRms: Double?,
    val micRms: Double?,
    val nearEndResidualRatio: Double?,
) {
    init {
        require(frameSequence >= 0L) { "frameSequence must be non-negative" }
        require(captureStartNs <= captureEndNs) { "capture interval must be ordered" }
        require(pcm16.isNotEmpty()) { "pcm16 must not be empty" }
    }

    val durationNs: Long
        get() = captureEndNs - captureStartNs

    companion object {
        fun fromAssessment(
            frameSequence: Long,
            captureStartNs: Long,
            captureEndNs: Long,
            pcm16: ShortArray,
            assessment: FarEndReferenceBuffer.Assessment,
        ): DuplexAudioFrame = DuplexAudioFrame(
            frameSequence = frameSequence,
            captureStartNs = captureStartNs,
            captureEndNs = captureEndNs,
            pcm16 = pcm16,
            playbackState = assessment.state,
            playbackResponseId = assessment.responseId,
            referenceReady = assessment.referenceAvailable,
            referenceConfidence = assessment.timestampConfidence,
            estimatedEchoDelayMs = assessment.estimatedDelayMs,
            echoSimilarity = assessment.similarity,
            farEndRms = assessment.farEndRms,
            micRms = assessment.micRms,
            nearEndResidualRatio = assessment.nearEndResidualRatio,
        )
    }
}
