package com.voiceaipoc.wakeword

/** Configuration for the native openWakeWord integration boundary. */
data class WakeWordConfig(
    val enabled: Boolean = true,
    val modelName: String = ApprovedOpenWakeWordModel.MODEL_NAME,
    val modelFormat: String = ApprovedOpenWakeWordModel.MODEL_FORMAT,
    val assetDirectory: String = ApprovedOpenWakeWordModel.ASSET_DIRECTORY,
    val melSpectrogramAssetName: String = ApprovedOpenWakeWordModel.MEL_FILE_NAME,
    val embeddingAssetName: String = ApprovedOpenWakeWordModel.EMBEDDING_FILE_NAME,
    val classifierAssetName: String = ApprovedOpenWakeWordModel.CLASSIFIER_FILE_NAME,
    val detectionThreshold: Float = ApprovedOpenWakeWordModel.BASELINE_THRESHOLD,
    val cooldownMs: Long = 2_000L,
    /** Four existing 20 ms frames form the official 80 ms inference hop. */
    val inputFramesPerInference: Int = 4,
    /** 160 ms decoupling queue at the existing 20 ms frame duration. */
    val queueCapacityFrames: Int = 8,
    /** Metadata-only calibration diagnostics; raw PCM is never retained. */
    val acousticDiagnosticsEnabled: Boolean = true,
    val diagnosticScoreThresholds: List<Float> = listOf(
        0.10f,
        0.20f,
        0.30f,
        0.35f,
        0.40f,
        0.45f,
        0.50f,
    ),
    /** One manually marked trial observes three seconds of inference metadata. */
    val calibrationTrialDurationMs: Long = 3_000L,
    /** Bounded metadata only; enough for the complete controlled calibration matrix. */
    val calibrationTrialHistoryCapacity: Int = 512,
    /** Audio capture waits only for off-thread model/session initialization. */
    val runtimeInitializationTimeoutMs: Long = 5_000L,
    val workerJoinTimeoutMs: Long = 2_000L,
) {
    init {
        require(modelName.isNotBlank()) { "modelName cannot be blank" }
        require(modelFormat == "ONNX") { "Approved openWakeWord assets must use ONNX" }
        require(assetDirectory.isNotBlank()) { "assetDirectory cannot be blank" }
        require(melSpectrogramAssetName.isNotBlank()) {
            "melSpectrogramAssetName cannot be blank"
        }
        require(embeddingAssetName.isNotBlank()) { "embeddingAssetName cannot be blank" }
        require(classifierAssetName.isNotBlank()) { "classifierAssetName cannot be blank" }
        require(detectionThreshold > 0.0f && detectionThreshold <= 1.0f) {
            "detectionThreshold must be in (0, 1]"
        }
        require(cooldownMs > 0L) { "cooldownMs must be positive" }
        require(inputFramesPerInference > 0) { "inputFramesPerInference must be positive" }
        require(queueCapacityFrames >= inputFramesPerInference) {
            "queueCapacityFrames must hold at least one inference window"
        }
        require(diagnosticScoreThresholds.isNotEmpty()) {
            "diagnosticScoreThresholds cannot be empty"
        }
        require(diagnosticScoreThresholds.all { it.isFinite() && it in 0f..1f }) {
            "diagnosticScoreThresholds must be finite and in [0, 1]"
        }
        require(diagnosticScoreThresholds.toSet().size == diagnosticScoreThresholds.size) {
            "diagnosticScoreThresholds must be unique"
        }
        require(calibrationTrialDurationMs > 0L) {
            "calibrationTrialDurationMs must be positive"
        }
        require(calibrationTrialHistoryCapacity > 0) {
            "calibrationTrialHistoryCapacity must be positive"
        }
        require(runtimeInitializationTimeoutMs > 0L) {
            "runtimeInitializationTimeoutMs must be positive"
        }
        require(workerJoinTimeoutMs > 0L) { "workerJoinTimeoutMs must be positive" }
    }

    fun requiredAssetPaths(): List<String> = listOf(
        "$assetDirectory/$melSpectrogramAssetName",
        "$assetDirectory/$embeddingAssetName",
        "$assetDirectory/$classifierAssetName",
    )
}
