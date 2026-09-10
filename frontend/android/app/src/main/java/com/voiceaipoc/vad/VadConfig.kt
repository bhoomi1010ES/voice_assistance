package com.voiceaipoc.vad

/** Configuration for the deterministic Phase 0.5 energy VAD. */
data class VadConfig(
    val enabled: Boolean = true,
    val speechThresholdDbFs: Double = -42.0,
    val minimumSpeechDurationMs: Int = 100,
    val minimumSilenceDurationMs: Int = 300,
    val speechStartConfirmationFrames: Int = 5,
    val speechEndConfirmationFrames: Int = 15,
) {
    init {
        require(speechThresholdDbFs in MIN_DBFS..MAX_DBFS) {
            "speechThresholdDbFs must be between $MIN_DBFS and $MAX_DBFS"
        }
        require(minimumSpeechDurationMs >= 0) { "minimumSpeechDurationMs cannot be negative" }
        require(minimumSilenceDurationMs >= 0) { "minimumSilenceDurationMs cannot be negative" }
        require(speechStartConfirmationFrames > 0) {
            "speechStartConfirmationFrames must be positive"
        }
        require(speechEndConfirmationFrames > 0) {
            "speechEndConfirmationFrames must be positive"
        }
    }

    companion object {
        const val MIN_DBFS = -120.0
        const val MAX_DBFS = 0.0
    }
}
