package com.voiceaipoc.audio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

class DuplexAudioFrameTest {
    @Test
    fun fromAssessmentPreservesCaptureIntervalPcmOwnershipAndReferenceMetadata() {
        val pcm = shortArrayOf(1, -2, 3)
        val assessment = FarEndReferenceBuffer.Assessment(
            state = FarEndReferenceBuffer.State.PLAYING,
            playbackActive = true,
            responseId = "response-1",
            playbackPositionMs = 240L,
            similarity = 0.91,
            lagMs = 40,
            echoLikely = true,
            referenceAvailable = true,
            timestampConfidence = "AUDIO_TIMESTAMP",
            estimatedDelayMs = 60,
            correlation = 0.94,
            coherence = 0.92,
            farEndRms = 420.0,
            micRms = 300.0,
            nearEndResidualRatio = 0.18,
            timestampNs = 2_000_000_000L,
        )

        val frame = DuplexAudioFrame.fromAssessment(
            frameSequence = 7L,
            captureStartNs = 1_000_000_000L,
            captureEndNs = 1_020_000_000L,
            pcm16 = pcm,
            assessment = assessment,
        )

        assertEquals(20_000_000L, frame.durationNs)
        assertSame(pcm, frame.pcm16)
        assertEquals(FarEndReferenceBuffer.State.PLAYING, frame.playbackState)
        assertEquals("response-1", frame.playbackResponseId)
        assertEquals("AUDIO_TIMESTAMP", frame.referenceConfidence)
        assertEquals(60, frame.estimatedEchoDelayMs)
        assertEquals(0.18, frame.nearEndResidualRatio!!, 0.0)
    }
}
