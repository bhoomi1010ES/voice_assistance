package com.voiceaipoc.audio

import com.voiceaipoc.voice.TtsAudioTimestamp
import com.voiceaipoc.voice.TtsPlaybackPosition
import kotlin.math.PI
import kotlin.math.sin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FarEndReferenceBufferTest {
    @Test
    fun presentationQueryRemainsAlignedWhenWritesAreAheadBy50To500Ms() {
        val responseId = "response-alignment"
        val pcm = sinePcm(1_920, 320.0, 8_000.0, 16_000)

        for (writeAheadMs in intArrayOf(50, 100, 200, 500)) {
            val reference = FarEndReferenceBuffer(
                referenceSampleRateHz = 16_000,
                capacityMs = 2_000,
                analysisContextMs = 120,
            )
            val nowNs = 10_000_000_000L
            reference.onPlaybackStarted(responseId, 16_000, nowNs)
            reference.onPcmWritten(
                responseId = responseId,
                payload = pcm,
                offsetBytes = 0,
                byteCount = pcm.size,
                sampleRateHz = 16_000,
                writtenFrameStart = writeAheadMs * 16L,
                playbackPosition = TtsPlaybackPosition(
                    playbackHeadFramePosition = 0L,
                    timestamp = null,
                    measuredAtNs = nowNs,
                ),
            )

            val assessment = reference.assess(
                microphone = pcm.decodePcm16(),
                samplesRead = pcm.size / 2,
                captureStartNs = nowNs + writeAheadMs * 1_000_000L,
                captureEndNs = nowNs + writeAheadMs * 1_000_000L + 120_000_000L,
                microphoneSampleRateHz = 16_000,
            )

            assertTrue("writeAheadMs=$writeAheadMs", assessment.referenceAvailable)
            assertTrue("writeAheadMs=$writeAheadMs", assessment.echoLikely)
            assertEquals("writeAheadMs=$writeAheadMs", 0, assessment.estimatedDelayMs)
            assertEquals("writeAheadMs=$writeAheadMs", "PLAYBACK_HEAD_FALLBACK", assessment.timestampConfidence)
        }
    }

    @Test
    fun boundedDelaySearchFindsInjectedAcousticDelay() {
        val responseId = "response-delay"
        val pcm = sinePcm(1_920, 440.0, 8_000.0, 16_000)
        val reference = FarEndReferenceBuffer(
            referenceSampleRateHz = 16_000,
            capacityMs = 2_000,
            analysisContextMs = 120,
        )
        val nowNs = 20_000_000_000L
        reference.onPlaybackStarted(responseId, 16_000, nowNs)
        reference.onPcmWritten(
            responseId,
            pcm,
            0,
            pcm.size,
            16_000,
            3_200L,
            TtsPlaybackPosition(0L, TtsAudioTimestamp(0L, nowNs), nowNs),
        )

        val assessment = reference.assess(
            microphone = pcm.decodePcm16(),
            samplesRead = pcm.size / 2,
            captureStartNs = nowNs + 200_000_000L + 100_000_000L,
            captureEndNs = nowNs + 200_000_000L + 220_000_000L,
            microphoneSampleRateHz = 16_000,
        )

        assertTrue(assessment.echoLikely)
        assertEquals(100, assessment.estimatedDelayMs)
        assertTrue((assessment.nearEndResidualRatio ?: 1.0) < 0.05)
    }

    @Test
    fun missingTimestampUsesPlaybackHeadFallback() {
        val reference = FarEndReferenceBuffer(analysisContextMs = 80)
        val responseId = "response-fallback"
        val pcm = sinePcm(1_280, 320.0, 8_000.0, 16_000)
        val nowNs = 30_000_000_000L
        reference.onPlaybackStarted(responseId, 16_000, nowNs)
        reference.onPcmWritten(
            responseId,
            pcm,
            0,
            pcm.size,
            16_000,
            1_600L,
            TtsPlaybackPosition(0L, null, nowNs),
        )

        val assessment = reference.assess(
            pcm.decodePcm16(),
            pcm.size / 2,
            nowNs + 100_000_000L,
            nowNs + 180_000_000L,
            16_000,
        )

        assertEquals("PLAYBACK_HEAD_FALLBACK", assessment.timestampConfidence)
        assertTrue(assessment.referenceAvailable)
    }

    @Test
    fun disabledPresentationTimingForcesConservativeWriteTimeFallback() {
        val reference = FarEndReferenceBuffer(
            analysisContextMs = 80,
            presentationTimingEnabled = false,
        )
        val responseId = "response-untimed"
        val pcm = sinePcm(1_280, 320.0, 8_000.0, 16_000)
        val nowNs = 35_000_000_000L
        reference.onPlaybackStarted(responseId, 16_000, nowNs)
        reference.onPcmWritten(
            responseId,
            pcm,
            0,
            pcm.size,
            16_000,
            0L,
            TtsPlaybackPosition(0L, TtsAudioTimestamp(0L, nowNs), nowNs),
        )

        val assessment = reference.assess(
            pcm.decodePcm16(),
            pcm.size / 2,
            nowNs,
            nowNs + 80_000_000L,
            16_000,
        )

        assertEquals("WRITE_TIME_FALLBACK", assessment.timestampConfidence)
    }

    @Test
    fun timestampedPlaybackPositionTakesPriorityOverHeadFallback() {
        val reference = FarEndReferenceBuffer(analysisContextMs = 80)
        val responseId = "response-timestamp"
        val pcm = sinePcm(1_280, 320.0, 8_000.0, 16_000)
        val nowNs = 40_000_000_000L
        reference.onPlaybackStarted(responseId, 16_000, nowNs)
        reference.onPcmWritten(
            responseId,
            pcm,
            0,
            pcm.size,
            16_000,
            800L,
            TtsPlaybackPosition(0L, TtsAudioTimestamp(0L, nowNs), nowNs),
        )

        val assessment = reference.assess(
            pcm.decodePcm16(),
            pcm.size / 2,
            nowNs + 50_000_000L,
            nowNs + 130_000_000L,
            16_000,
        )

        assertEquals("AUDIO_TIMESTAMP", assessment.timestampConfidence)
        assertTrue(assessment.echoLikely)
    }

    @Test
    fun gainScaledAndPhaseShiftedReferenceStillHasHighCoherence() {
        val reference = FarEndReferenceBuffer(analysisContextMs = 120)
        val responseId = "response-gain-phase"
        val source = sinePcm(1_920, 320.0, 8_000.0, 16_000)
        val microphone = sineSamples(1_920, 320.0, 2_000.0, 16_000, phase = PI / 9.0)
        val nowNs = 50_000_000_000L
        reference.onPlaybackStarted(responseId, 16_000, nowNs)
        reference.onPcmWritten(
            responseId,
            source,
            0,
            source.size,
            16_000,
            0L,
            TtsPlaybackPosition(0L, TtsAudioTimestamp(0L, nowNs), nowNs),
        )

        val assessment = reference.assess(
            microphone,
            microphone.size / 2,
            nowNs,
            nowNs + 120_000_000L,
            16_000,
        )

        assertTrue(assessment.echoLikely)
        assertTrue((assessment.coherence ?: 0.0) > 0.9)
        assertTrue((assessment.nearEndResidualRatio ?: 1.0) < 0.4)
    }

    @Test
    fun impulseSweepAndSpeechLikeFixturesProduceReferenceEvidence() {
        val fixtures = listOf(
            ShortArray(1_920) { index -> if (index == 960) 12_000 else 0 },
            ShortArray(1_920) { index ->
                val frequency = 180.0 + 3_000.0 * index / 1_920.0
                sin(2.0 * PI * frequency * index / 16_000.0).times(8_000.0).toInt().toShort()
            },
            ShortArray(1_920) { index ->
                (
                    sin(2.0 * PI * 180.0 * index / 16_000.0) * 4_000.0 +
                        sin(2.0 * PI * 320.0 * index / 16_000.0) * 3_000.0 +
                        sin(2.0 * PI * 760.0 * index / 16_000.0) * 2_000.0
                    ).toInt().toShort()
            },
        )

        fixtures.forEachIndexed { index, samples ->
            val reference = FarEndReferenceBuffer(analysisContextMs = 120)
            val responseId = "response-fixture-$index"
            val nowNs = 70_000_000_000L + index * 1_000_000_000L
            val payload = samples.toPcm16()
            reference.onPlaybackStarted(responseId, 16_000, nowNs)
            reference.onPcmWritten(
                responseId,
                payload,
                0,
                payload.size,
                16_000,
                0L,
                TtsPlaybackPosition(0L, TtsAudioTimestamp(0L, nowNs), nowNs),
            )

            val assessment = reference.assess(
                samples,
                samples.size,
                nowNs,
                nowNs + 120_000_000L,
                16_000,
            )

            assertTrue("fixture=$index", assessment.referenceAvailable)
            assertTrue("fixture=$index", assessment.echoLikely)
        }
    }

    @Test
    fun tailUsesFinalPresentationTimeAndTransitionsToCompleted() {
        val reference = FarEndReferenceBuffer(
            referenceSampleRateHz = 16_000,
            analysisContextMs = 80,
            tailSuppressionMs = 300L,
        )
        val responseId = "response-tail"
        val nowNs = 60_000_000_000L
        reference.onPlaybackStarted(responseId, 16_000, nowNs)
        reference.onPlaybackDraining(responseId, nowNs + 10_000_000L)
        reference.onPlaybackCompleted(
            responseId,
            TtsPlaybackPosition(1_000L, null, nowNs + 500_000_000L),
            nowNs + 500_000_000L,
        )

        val duringTail = reference.assess(ShortArray(640), 640, nowNs + 700_000_000L, nowNs + 740_000_000L)
        assertEquals(FarEndReferenceBuffer.State.TAIL_SUPPRESSION, duringTail.state)
        assertTrue(duringTail.playbackActive)

        val completed = reference.assess(ShortArray(640), 640, nowNs + 800_000_000L, nowNs + 840_000_000L)
        assertEquals(FarEndReferenceBuffer.State.COMPLETED, completed.state)
        assertFalse(completed.playbackActive)
    }

    private fun sinePcm(sampleCount: Int, frequencyHz: Double, amplitude: Double, sampleRateHz: Int): ByteArray {
        val samples = sineSamples(sampleCount, frequencyHz, amplitude, sampleRateHz)
        val payload = ByteArray(samples.size * 2)
        for (index in samples.indices) {
            payload[index * 2] = (samples[index].toInt() and 0xff).toByte()
            payload[index * 2 + 1] = (samples[index].toInt() shr 8).toByte()
        }
        return payload
    }

    private fun sineSamples(
        sampleCount: Int,
        frequencyHz: Double,
        amplitude: Double,
        sampleRateHz: Int,
        phase: Double = 0.0,
    ): ShortArray = ShortArray(sampleCount) { index ->
        (sin(2.0 * PI * frequencyHz * index / sampleRateHz + phase) * amplitude)
            .toInt()
            .coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
            .toShort()
    }

    private fun ByteArray.decodePcm16(): ShortArray = ShortArray(size / 2) { index ->
        ((this[index * 2].toInt() and 0xff) or (this[index * 2 + 1].toInt() shl 8)).toShort()
    }

    private fun ShortArray.toPcm16(): ByteArray = ByteArray(size * 2).also { payload ->
        for (index in indices) {
            payload[index * 2] = (this[index].toInt() and 0xff).toByte()
            payload[index * 2 + 1] = (this[index].toInt() shr 8).toByte()
        }
    }
}
