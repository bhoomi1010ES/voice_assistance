package com.voiceaipoc.audio

/**
 * Narrow render/capture boundary for an echo canceller.
 *
 * Implementations process fixed 10 ms mono PCM frames. The interface carries
 * only native PCM buffers and metadata; no frame or metric is sent to JS.
 */
interface EchoCanceller {
    enum class State {
        DISABLED,
        STARTING,
        ACTIVE,
        DEGRADED,
        STOPPED,
    }

    data class Config(
        val sampleRateHz: Int,
        val channelCount: Int,
        val frameSizeSamples: Int,
        val streamDelayMs: Int,
        val enableAec: Boolean,
        val enableNoiseSuppression: Boolean,
    )

    data class Metrics(
        val state: State,
        val implementation: String,
        val sampleRateHz: Int,
        val frameSizeSamples: Int,
        val streamDelayMs: Int,
        val aecRequested: Boolean,
        val noiseSuppressionRequested: Boolean,
        val renderFrames: Long,
        val captureFrames: Long,
        val processedFrames: Long,
        val bypassedFrames: Long,
        val renderDropCount: Long,
        val processingErrorCount: Long,
        val lastInputRms: Double,
        val lastOutputRms: Double,
        val lastError: String?,
    )

    companion object {
        const val PROCESS_OK = 0
        const val PROCESS_BYPASSED = 1
        const val PROCESS_ERROR = -1
    }

    fun start(config: Config): Boolean

    /** Feeds the presentation-aligned far-end/render frame to the AEC. */
    fun processRender(
        pcm: ShortArray,
        offset: Int,
        count: Int,
        presentationTimestampNs: Long,
        referenceReady: Boolean,
    ): Int

    /** Processes capture PCM into [output]. Input and output may be the same array. */
    fun processCapture(
        input: ShortArray,
        inputOffset: Int,
        output: ShortArray,
        outputOffset: Int,
        count: Int,
        captureTimestampNs: Long,
    ): Int

    fun reset()

    fun stop()

    fun metrics(): Metrics
}
