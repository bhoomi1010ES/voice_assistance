package com.voiceaipoc.vad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class Phase7DiagnosticsStatusTest {
    @Test
    fun detectorStatusRetainsReasonCorrelationAndLocalStopLatency() {
        val detector = PlaybackAwareBargeInDetector()
        val decision = detector.evaluate(
            BargeInInput(
                event = "SILERO_VAD_SPEECH_ACTIVITY",
                monotonicTimestampNs = 2_000_000_000L,
                captureStartNs = 1_980_000_000L,
                captureEndNs = 2_000_000_000L,
                probability = 0.60f,
                playbackState = "PLAYING",
                playbackActive = true,
                playbackResponseId = "response-1",
                playbackPositionMs = 900L,
                referenceReady = true,
                referenceConfidence = BargeInConfig.TIMESTAMP_AUDIO,
                estimatedDelayMs = 80,
                echoLikely = false,
                echoSimilarity = 0.10,
                echoCoherence = 0.10,
                farEndRms = 200.0,
                micRms = 900.0,
                nearEndResidualRatio = 0.90,
                nearEndFarEndEnergyRatio = 4.5,
                discontinuous = false,
                sourceFrameSequenceStart = 40L,
                sourceFrameSequenceEnd = 63L,
                inferenceIndex = 12L,
            ),
        )

        assertNotNull(decision)
        val beforeStop = detector.getStatus()
        assertEquals(PlaybackAwareBargeInDetector.EVENT_CANDIDATE, beforeStop.lastEvent)
        assertEquals("silero_probability_low", beforeStop.lastReason)
        assertEquals(1L, beforeStop.candidateCount)
        assertEquals("response-1", beforeStop.lastResponseId)
        assertEquals(40L, beforeStop.lastSourceFrameSequenceStart)
        assertEquals(63L, beforeStop.lastSourceFrameSequenceEnd)

        detector.recordLocalStopLatency("response-1", 18L)
        val afterStop = detector.getStatus()
        assertEquals(18L, afterStop.lastLocalStopLatencyMs)
        assertEquals("response-1", afterStop.lastLocalStopResponseId)
    }
}
