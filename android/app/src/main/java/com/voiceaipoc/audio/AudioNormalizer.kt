package com.voiceaipoc.audio

/**
 * Validates the model-facing PCM contract without changing sample amplitude.
 *
 * AudioRecord reads into a [ShortArray], so Android has already decoded the
 * little-endian PCM16 bytes into signed 16-bit samples. Phase 0.4 intentionally
 * performs no gain adjustment, resampling, clipping, or floating-point
 * conversion. Keeping this boundary explicit lets later native DSP stages add
 * a measured transform without moving PCM through React Native.
 */
class AudioNormalizer(
    private val channelCount: Int,
) {
    init {
        require(channelCount > 0) { "channelCount must be positive" }
    }

    /** Returns true only for a non-empty, complete set of interleaved PCM frames. */
    fun validatePcm16InPlace(pcmSamples: ShortArray, samplesRead: Int): Boolean =
        samplesRead > 0 &&
            samplesRead <= pcmSamples.size &&
            samplesRead % channelCount == 0
}
