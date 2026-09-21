package com.voiceaipoc.audio

import android.media.AudioAttributes
import android.media.MediaRecorder
import com.voiceaipoc.vad.BargeInConfig
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
    /** Communication capture is the production duplex route. MIC is diagnostic-only A/B mode. */
    val captureSource: CaptureSource = CaptureSource.VOICE_COMMUNICATION,
    /** Optional API 31+ communication-device target; AUTO preserves platform route choice. */
    val communicationDevicePreference: AudioRouteController.DevicePreference =
        AudioRouteController.DevicePreference.AUTO,
    /** TTS uses the same communication usage so platform AEC sees the far-end render path. */
    val playbackUsage: Int = AudioAttributes.USAGE_VOICE_COMMUNICATION,
    val playbackContentType: Int = AudioAttributes.CONTENT_TYPE_SPEECH,
    /**
     * Software AEC3 is opt-in. PLATFORM preserves the Phase 1-5 route. AUTO
     * selects AEC3 only when the pinned native backend is present and otherwise
     * keeps platform AEC. WEBRTC_AEC3 is an explicit diagnostic mode and stays
     * degraded rather than silently combining platform and software AEC.
     */
    val softwareAecMode: SoftwareAecMode = SoftwareAecMode.PLATFORM,
    /** Render-to-capture delay supplied to WebRTC APM; calibrate per route. */
    val softwareAecRenderToCaptureDelayMs: Int = 80,
    /** Independent software NS policy; platform NS remains separately configurable. */
    val enableSoftwareNoiseSuppression: Boolean = false,
    /** Conservative diagnostic tail; replace with route-specific measured values when available. */
    val playbackTailSuppressionMs: Long = FarEndReferenceBuffer.DEFAULT_TAIL_SUPPRESSION_MS,
    /** Native energy-VAD configuration for Phase 0.5. */
    val vadConfig: VadConfig = VadConfig(),
    /** Native playback-aware barge-in policy; rollout flags may tighten it. */
    val bargeInConfig: BargeInConfig = BargeInConfig(),
    /** Native Silero VAD worker/model configuration for Phase 0.6. */
    val sileroVadConfig: SileroVadConfig = SileroVadConfig(),
    /** Native openWakeWord worker/model integration configuration. */
    val wakeWordConfig: WakeWordConfig = WakeWordConfig(),
) {
    enum class SoftwareAecMode {
        PLATFORM,
        AUTO,
        WEBRTC_AEC3,
    }

    enum class CaptureSource(
        val androidValue: Int,
        val diagnosticName: String,
    ) {
        VOICE_COMMUNICATION(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            "VOICE_COMMUNICATION",
        ),
        MIC(MediaRecorder.AudioSource.MIC, "MIC"),
    }

    init {
        require(sampleRateHz > 0) { "sampleRateHz must be positive" }
        require(channelCount > 0) { "channelCount must be positive" }
        require(bitsPerSample == 16) { "The native PCM pipeline requires signed PCM16" }
        require(frameDurationMs > 0) { "frameDurationMs must be positive" }
        require(ringBufferCapacityFrames > 0) { "ringBufferCapacityFrames must be positive" }
        require(softwareAecRenderToCaptureDelayMs in 0..500) {
            "softwareAecRenderToCaptureDelayMs must be between 0 and 500"
        }
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
