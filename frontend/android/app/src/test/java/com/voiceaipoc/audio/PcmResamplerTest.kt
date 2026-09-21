package com.voiceaipoc.audio

import kotlin.math.PI
import kotlin.math.sqrt
import kotlin.math.sin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PcmResamplerTest {
    @Test
    fun resamplingIsContinuousAcrossArbitraryChunkBoundaries() {
        val input = ShortArray(24_000) { index ->
            (sin(2.0 * PI * 440.0 * index / 24_000.0) * 12_000.0).toInt().toShort()
        }
        val oneShot = ShortArray(24_000)
        val chunked = ShortArray(24_000)
        val reference = PcmResampler(24_000, 16_000)
        val referenceCount = reference.process(input, 0, input.size, oneShot)

        val chunkedResampler = PcmResampler(24_000, 16_000)
        var inputOffset = 0
        var outputOffset = 0
        val chunkSizes = intArrayOf(1, 7, 31, 97, 257, 13, 509, 2, 401)
        var chunkIndex = 0
        while (inputOffset < input.size) {
            val count = minOf(chunkSizes[chunkIndex % chunkSizes.size], input.size - inputOffset)
            outputOffset += chunkedResampler.process(input, inputOffset, count, chunked, outputOffset)
            inputOffset += count
            chunkIndex += 1
        }

        assertEquals(referenceCount, outputOffset)
        for (index in 0 until referenceCount) {
            assertEquals("sample=$index", oneShot[index], chunked[index])
        }
    }

    @Test
    fun decimationAttenuatesOutOfBandEnergy() {
        val low = ShortArray(24_000) { index ->
            (sin(2.0 * PI * 2_000.0 * index / 24_000.0) * 10_000.0).toInt().toShort()
        }
        val high = ShortArray(24_000) { index ->
            (sin(2.0 * PI * 10_000.0 * index / 24_000.0) * 10_000.0).toInt().toShort()
        }
        val lowOut = ShortArray(24_000)
        val highOut = ShortArray(24_000)
        val lowCount = PcmResampler(24_000, 16_000).process(low, 0, low.size, lowOut)
        val highCount = PcmResampler(24_000, 16_000).process(high, 0, high.size, highOut)

        fun rms(samples: ShortArray, count: Int): Double {
            var energy = 0.0
            for (index in 512 until count) energy += samples[index].toDouble() * samples[index].toDouble()
            return sqrt(energy / (count - 512).toDouble())
        }

        assertTrue(rms(lowOut, lowCount) > rms(highOut, highCount) * 2.0)
    }
}
