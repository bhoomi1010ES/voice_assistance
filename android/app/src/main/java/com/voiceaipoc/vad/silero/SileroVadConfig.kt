package com.voiceaipoc.vad.silero

/** Configuration for the native Silero VAD worker and transition smoothing. */
data class SileroVadConfig(
    val enabled: Boolean = true,
    val modelFileName: String = ApprovedSileroVadModel.FILE_NAME,
    val assetDirectory: String = ApprovedSileroVadModel.ASSET_DIRECTORY,
    val modelFormat: String = "ONNX",
    val sampleRateHz: Int = 16_000,
    /** Silero v6.2.1 consumes 512 new samples at 16 kHz. */
    val inferenceChunkSamples: Int = 512,
    /** The official wrapper carries this prior-audio context inside the runtime. */
    val modelContextSamples: Int = 64,
    val speechProbabilityThreshold: Float = 0.5f,
    /** Three 32 ms inference decisions. */
    val speechStartConfirmationMs: Int = 96,
    /** Ten 32 ms inference decisions. */
    val speechStopHangoverMs: Int = 320,
    /** Eight existing 20 ms frames, or 160 ms, bounds scheduling jitter. */
    val queueCapacityFrames: Int = 8,
    /** Audio capture waits only for off-thread model/session initialization. */
    val runtimeInitializationTimeoutMs: Long = 5_000L,
    val workerJoinTimeoutMs: Long = 2_000L,
) {
    init {
        require(modelFileName.isNotBlank()) { "modelFileName cannot be blank" }
        require(modelFileName.endsWith(".onnx", ignoreCase = true)) {
            "Silero VAD model must use the ONNX format"
        }
        require(assetDirectory.isNotBlank()) { "assetDirectory cannot be blank" }
        require(modelFormat == "ONNX") { "modelFormat must be ONNX" }
        require(sampleRateHz == 16_000) { "Phase 0.6 requires 16 kHz Silero input" }
        require(inferenceChunkSamples == 512) {
            "Approved Silero v6.2.1 requires 512 current samples"
        }
        require(modelContextSamples == 64) {
            "Approved Silero v6.2.1 requires 64 context samples"
        }
        require(speechProbabilityThreshold > 0f && speechProbabilityThreshold <= 1f) {
            "speechProbabilityThreshold must be in (0, 1]"
        }
        require(speechStartConfirmationMs > 0) {
            "speechStartConfirmationMs must be positive"
        }
        require(speechStopHangoverMs > 0) { "speechStopHangoverMs must be positive" }
        require(queueCapacityFrames > 0) { "queueCapacityFrames must be positive" }
        require(runtimeInitializationTimeoutMs > 0L) {
            "runtimeInitializationTimeoutMs must be positive"
        }
        require(workerJoinTimeoutMs > 0L) { "workerJoinTimeoutMs must be positive" }
        require((inferenceChunkSamples.toLong() * MILLIS_PER_SECOND) % sampleRateHz == 0L) {
            "inferenceChunkSamples must represent a whole number of milliseconds"
        }
    }

    val modelAssetPath: String
        get() = "$assetDirectory/$modelFileName"

    val inferenceChunkDurationMs: Int
        get() = (inferenceChunkSamples.toLong() * MILLIS_PER_SECOND / sampleRateHz).toInt()

    val speechStartConfirmationChunks: Int
        get() = ceilDiv(speechStartConfirmationMs, inferenceChunkDurationMs)

    val speechStopConfirmationChunks: Int
        get() = ceilDiv(speechStopHangoverMs, inferenceChunkDurationMs)

    private fun ceilDiv(value: Int, divisor: Int): Int =
        ((value.toLong() + divisor - 1L) / divisor).toInt()

    private companion object {
        const val MILLIS_PER_SECOND = 1_000L
    }
}
