package com.voiceaipoc.audio

import com.voiceaipoc.vad.VadConfig
import com.voiceaipoc.vad.silero.SileroVadConfig
import com.voiceaipoc.wakeword.WakeWordConfig

/** Native capture and platform-processing configuration. */
data class AudioConfig(
    val sampleRateHz: Int = 16_000,
    val channelCount: Int = 1,
    val bitsPerSample: Int = 16,
    val frameDurationMs: Int = 20,
    /** Bounded to 500 ms with the default 20 ms processing frame. */
    val ringBufferCapacityFrames: Int = 25,
    /** Explicitly configurable so device/platform AEC can be disabled for comparisons. */
    val enableAcousticEchoCancellation: Boolean = true,
    /** Explicitly configurable so device/platform NS can be disabled for comparisons. */
    val enableNoiseSuppression: Boolean = true,
    /** Native energy-VAD configuration for Phase 0.5. */
    val vadConfig: VadConfig = VadConfig(),
    /** Native Silero VAD worker/model configuration for Phase 0.6. */
    val sileroVadConfig: SileroVadConfig = SileroVadConfig(),
    /** Native openWakeWord worker/model integration configuration. */
    val wakeWordConfig: WakeWordConfig = WakeWordConfig(),
) {
    init {
        require(sampleRateHz > 0) { "sampleRateHz must be positive" }
        require(channelCount > 0) { "channelCount must be positive" }
        require(bitsPerSample == 16) { "The native PCM pipeline requires signed PCM16" }
        require(frameDurationMs > 0) { "frameDurationMs must be positive" }
        require(ringBufferCapacityFrames > 0) { "ringBufferCapacityFrames must be positive" }
        require((sampleRateHz.toLong() * frameDurationMs) % MILLIS_PER_SECOND == 0L) {
            "frameDurationMs must produce a whole number of PCM samples"
        }
    }

    val bytesPerSample: Int
        get() = bitsPerSample / BITS_PER_BYTE

    /** Interleaved samples per deterministic processing frame. */
    val frameSizeSamples: Int
        get() = ((sampleRateHz.toLong() * frameDurationMs / MILLIS_PER_SECOND) * channelCount).toInt()

    val frameSizeBytes: Int
        get() = frameSizeSamples * bytesPerSample

    val maxBufferedDurationMs: Int
        get() = frameDurationMs * ringBufferCapacityFrames

    private companion object {
        const val BITS_PER_BYTE = 8
        const val MILLIS_PER_SECOND = 1_000L
    }
}
