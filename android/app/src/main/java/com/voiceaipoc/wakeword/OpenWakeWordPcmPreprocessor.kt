package com.voiceaipoc.wakeword

import java.nio.FloatBuffer

/** Explicitly preserves openWakeWord's raw signed-PCM16-to-float32 contract. */
internal object OpenWakeWordPcmPreprocessor {
    fun copyRawPcm16ToFloat(
        source: ShortArray,
        sourceOffset: Int,
        destination: FloatBuffer,
        destinationOffset: Int,
        sampleCount: Int,
    ) {
        require(sourceOffset >= 0 && sourceOffset + sampleCount <= source.size) {
            "source does not contain sampleCount PCM16 values"
        }
        require(destinationOffset >= 0 && destinationOffset + sampleCount <= destination.capacity()) {
            "destination cannot hold sampleCount float32 values"
        }
        for (index in 0 until sampleCount) {
            destination.put(destinationOffset + index, source[sourceOffset + index].toFloat())
        }
    }
}
