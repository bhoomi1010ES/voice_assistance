package com.voiceaipoc.audio

import kotlin.math.PI
import kotlin.math.sin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackEchoReferenceTest {
    @Test
    fun matchingRenderedPcmIsClassifiedAsPlaybackEcho() {
        val reference = PlaybackEchoReference(
            referenceSampleRateHz = 16_000,
            capacityMs = 1_000,
            clockNs = { 1_000_000_000L },
        )
        val responseId = "response-1"
        val rendered = sinePcm(
            sampleCount = 1_280,
            frequencyHz = 320.0,
            amplitude = 8_000.0,
        )

        reference.onPlaybackStarted(responseId, nowNs = 1_000_000_000L)
        reference.onPcmRendered(
            responseId = responseId,
            payload = rendered,
            offsetBytes = 0,
            byteCount = rendered.size,
            sampleRateHz = 16_000,
        )

        val assessment = reference.assess(
            microphone = rendered.decodePcm16(),
            samplesRead = rendered.size / 2,
            nowNs = 1_100_000_000L,
        )

        assertTrue(assessment.playbackActive)
        assertTrue(assessment.referenceAvailable)
        assertTrue(assessment.echoLikely)
        assertEquals(0, assessment.lagMs)
        assertTrue((assessment.similarity ?: 0.0) >= 0.99)
    }

    @Test
    fun unrelatedMicrophoneAudioIsNotClassifiedAsPlaybackEcho() {
        val reference = PlaybackEchoReference(
            referenceSampleRateHz = 16_000,
            capacityMs = 1_000,
        )
        val responseId = "response-2"
        val rendered = sinePcm(
            sampleCount = 1_280,
            frequencyHz = 320.0,
            amplitude = 8_000.0,
        )
        val microphone = sinePcm(
            sampleCount = 640,
            frequencyHz = 1_700.0,
            amplitude = 8_000.0,
        ).decodePcm16()

        reference.onPlaybackStarted(responseId, nowNs = 1_000_000_000L)
        reference.onPcmRendered(
            responseId = responseId,
            payload = rendered,
            offsetBytes = 0,
            byteCount = rendered.size,
            sampleRateHz = 16_000,
        )

        val assessment = reference.assess(
            microphone = microphone,
            samplesRead = microphone.size,
            nowNs = 1_100_000_000L,
        )

        assertFalse(assessment.echoLikely)
        assertTrue((assessment.similarity ?: 0.0) < 0.58)
    }

    @Test
    fun playbackTailIsSuppressedBeforeReferenceReturnsToIdle() {
        val reference = PlaybackEchoReference(
            referenceSampleRateHz = 16_000,
            capacityMs = 1_000,
        )
        val responseId = "response-3"
        reference.onPlaybackStarted(responseId, nowNs = 2_000_000_000L)
        reference.onPlaybackEnded(responseId, nowNs = 3_000_000_000L)

        val tail = reference.assess(
            microphone = ShortArray(640),
            samplesRead = 640,
            nowNs = 3_179_000_000L,
        )
        val idle = reference.assess(
            microphone = ShortArray(640),
            samplesRead = 640,
            nowNs = 3_180_000_000L,
        )

        assertEquals(PlaybackEchoReference.State.TTS_TAIL_SUPPRESSION, tail.state)
        assertTrue(tail.playbackActive)
        assertEquals(PlaybackEchoReference.State.IDLE, idle.state)
        assertFalse(idle.playbackActive)
        assertFalse(idle.referenceAvailable)
        assertNotNull(tail.responseId)
    }

    private fun sinePcm(
        sampleCount: Int,
        frequencyHz: Double,
        amplitude: Double,
    ): ByteArray {
        val payload = ByteArray(sampleCount * 2)
        for (index in 0 until sampleCount) {
            val sample = (sin(2.0 * PI * frequencyHz * index / 16_000.0) * amplitude)
                .toInt()
                .coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
                .toShort()
            payload[index * 2] = (sample.toInt() and 0xff).toByte()
            payload[index * 2 + 1] = (sample.toInt() shr 8).toByte()
        }
        return payload
    }

    private fun ByteArray.decodePcm16(): ShortArray {
        val samples = ShortArray(size / 2)
        for (index in samples.indices) {
            samples[index] = (
                (this[index * 2].toInt() and 0xff) or
                    (this[index * 2 + 1].toInt() shl 8)
                ).toShort()
        }
        return samples
    }
}
