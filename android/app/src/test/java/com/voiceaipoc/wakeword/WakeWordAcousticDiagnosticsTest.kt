package com.voiceaipoc.wakeword

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeWordAcousticDiagnosticsTest {
    @Test
    fun calibrationConfigurationRejectsInvalidThresholdsAndBounds() {
        assertThrows(IllegalArgumentException::class.java) {
            WakeWordConfig(diagnosticScoreThresholds = emptyList())
        }
        assertThrows(IllegalArgumentException::class.java) {
            WakeWordConfig(diagnosticScoreThresholds = listOf(0.1f, 1.1f))
        }
        assertThrows(IllegalArgumentException::class.java) {
            WakeWordConfig(calibrationTrialDurationMs = 0L)
        }
        assertThrows(IllegalArgumentException::class.java) {
            WakeWordConfig(calibrationTrialHistoryCapacity = 0)
        }
    }

    @Test
    fun silenceProducesSignedPcmStatisticsWithoutMutation() {
        val diagnostics = newDiagnostics()
        val pcm = ShortArray(1_280)
        val before = pcm.copyOf()

        diagnostics.recordInference(
            pcm16 = pcm,
            samplesRead = pcm.size,
            inferenceIndex = 1L,
            score = 0.01f,
            timestampMs = 1_000L,
            queueDepthFrames = 0,
            inferenceDurationNanos = 2_000_000L,
            aecEnabled = true,
            noiseSuppressionEnabled = true,
            detectionOccurred = false,
            duplicateSuppressed = false,
        )

        val status = diagnostics.snapshot()
        assertArrayEquals(before, pcm)
        assertEquals(0, status.lastPcmMinimum)
        assertEquals(0, status.lastPcmMaximum)
        assertEquals(0, status.lastPcmPeak)
        assertEquals(0.0, status.lastPcmRms, 0.0)
        assertEquals(-120.0, status.lastPcmDbFs, 0.0)
        assertFalse(status.byteSwapApplied)
        assertFalse(status.normalizationApplied)
    }

    @Test
    fun pcmExtremaRmsClippingAndRawScaleAreReported() {
        val diagnostics = newDiagnostics()
        val pcm = shortArrayOf(Short.MIN_VALUE, -16_384, 0, 16_384, Short.MAX_VALUE)

        diagnostics.recordInference(
            pcm16 = pcm,
            samplesRead = pcm.size,
            inferenceIndex = 1L,
            score = 0.2f,
            timestampMs = 1_000L,
            queueDepthFrames = 2,
            inferenceDurationNanos = 3_000_000L,
            aecEnabled = true,
            noiseSuppressionEnabled = false,
            detectionOccurred = false,
            duplicateSuppressed = false,
        )

        val status = diagnostics.snapshot()
        assertEquals(Short.MIN_VALUE.toInt(), status.lastPcmMinimum)
        assertEquals(Short.MAX_VALUE.toInt(), status.lastPcmMaximum)
        assertEquals(32_768, status.lastPcmPeak)
        assertEquals(2L, status.clippedSampleCount)
        assertTrue(status.lastPcmRms > 0.0)
        assertTrue(status.lastPcmDbFs <= 0.0)
        assertEquals("RAW_PCM16_TO_FLOAT32_NO_SCALING", status.pcmScaling)
    }

    @Test
    fun scoreDistributionThresholdCountsAndPercentilesAreDeterministic() {
        val diagnostics = newDiagnostics()
        val scores = floatArrayOf(0.05f, 0.15f, 0.25f, 0.35f, 0.45f, 0.55f)

        scores.forEachIndexed { index, score ->
            diagnostics.recordInference(
                pcm16 = ShortArray(16),
                samplesRead = 16,
                inferenceIndex = (index + 1).toLong(),
                score = score,
                timestampMs = 1_000L + index,
                queueDepthFrames = index,
                inferenceDurationNanos = 1_000_000L,
                aecEnabled = false,
                noiseSuppressionEnabled = true,
                detectionOccurred = false,
                duplicateSuppressed = false,
            )
        }

        val status = diagnostics.snapshot()
        assertEquals(6L, status.inferenceWindowCount)
        assertEquals(0.05f, status.scoreMinimum)
        assertEquals(0.55f, status.scoreMaximum)
        assertEquals(0.30, status.scoreAverage, 0.000_001)
        assertEquals(
            listOf(5L, 4L, 3L, 3L, 2L, 2L, 1L),
            status.thresholdCounts.map { it.count },
        )
        assertEquals(0.25f, status.scoreP50)
        assertEquals(0.55f, status.scoreP90)
        assertEquals(0.55f, status.scoreP95)
        assertEquals(0.55f, status.scoreP99)
    }

    @Test
    fun markedTrialCapturesOnlyMetadataAndFinishesAtConfiguredDuration() {
        val diagnostics = newDiagnostics()
        assertEquals(
            "QUIET_25CM_POSITIVE_1",
            diagnostics.beginTrial(true, "Quiet 25cm", 1_000L, true, false),
        )
        assertNull(diagnostics.beginTrial(false, "QUIET_25CM", 1_001L, true, false))

        repeat(3) { index ->
            diagnostics.recordInference(
                pcm16 = ShortArray(32) { 1_000 },
                samplesRead = 32,
                inferenceIndex = (index + 10).toLong(),
                score = 0.1f * (index + 1),
                timestampMs = if (index == 2) 4_001L else 1_000L + index * 1_000L,
                queueDepthFrames = index,
                inferenceDurationNanos = (index + 1) * 1_000_000L,
                aecEnabled = true,
                noiseSuppressionEnabled = false,
                detectionOccurred = index == 2,
                duplicateSuppressed = false,
            )
        }

        val status = diagnostics.snapshot()
        assertNull(status.activeTrialLabel)
        assertEquals(1, status.completedPositiveTrials)
        val trial = status.calibrationTrials.single()
        assertEquals("QUIET_25CM_POSITIVE_1", trial.label)
        assertEquals("QUIET_25CM", trial.condition)
        assertEquals(1, trial.attemptNumber)
        assertEquals("AEC_ONLY", trial.audioProcessingMode)
        assertTrue(trial.aecEnabled)
        assertFalse(trial.noiseSuppressionEnabled)
        assertEquals(3L, trial.inferenceWindowCount)
        assertEquals(10L, trial.firstInferenceIndex)
        assertEquals(12L, trial.lastInferenceIndex)
        assertEquals(0.3f, trial.maximumScore)
        assertEquals(1L, trial.detectionCount)
        assertEquals(0L, trial.duplicateDetectionCount)
    }

    @Test
    fun trialHistoryIsBoundedAndResetClearsSessionMetadata() {
        val diagnostics = newDiagnostics(historyCapacity = 2, trialDurationMs = 1L)
        repeat(3) { index ->
            assertEquals(
                "QUIET_NEGATIVE_${index + 1}",
                diagnostics.beginTrial(false, "QUIET", index * 10L, false, false),
            )
            diagnostics.recordInference(
                pcm16 = ShortArray(8),
                samplesRead = 8,
                inferenceIndex = (index + 1).toLong(),
                score = 0.01f,
                timestampMs = index * 10L + 2L,
                queueDepthFrames = 0,
                inferenceDurationNanos = 1L,
                aecEnabled = false,
                noiseSuppressionEnabled = false,
                detectionOccurred = false,
                duplicateSuppressed = false,
            )
        }

        assertEquals(
            listOf("QUIET_NEGATIVE_2", "QUIET_NEGATIVE_3"),
            diagnostics.snapshot().calibrationTrials.map { it.label },
        )

        diagnostics.reset()

        val reset = diagnostics.snapshot()
        assertEquals(0L, reset.inferenceWindowCount)
        assertTrue(reset.calibrationTrials.isEmpty())
        assertNull(reset.scoreMinimum)
        assertNull(reset.scoreMaximum)
    }

    @Test
    fun calibrationIsExplicitlyEnabledAndSessionResetPreservesCompletedTrials() {
        val diagnostics = newDiagnostics(initiallyEnabled = false, trialDurationMs = 1L)
        assertFalse(diagnostics.snapshot().enabled)
        assertNull(diagnostics.beginTrial(true, "QUIET", 0L, true, true))

        diagnostics.setEnabled(true, 1L)
        assertEquals(
            "QUIET_POSITIVE_1",
            diagnostics.beginTrial(true, "QUIET", 2L, true, true),
        )
        recordInference(diagnostics, score = 0.51f, timestampMs = 4L)
        assertEquals(1, diagnostics.snapshot().completedPositiveTrials)

        diagnostics.resetSessionMetrics()

        val resetSession = diagnostics.snapshot()
        assertEquals(0L, resetSession.inferenceWindowCount)
        assertEquals(1, resetSession.completedPositiveTrials)
        assertEquals("QUIET_POSITIVE_1", resetSession.calibrationTrials.single().label)
    }

    @Test
    fun offlineThresholdAnalysisUsesSamePositiveAndNegativeTrials() {
        val diagnostics = newDiagnostics(trialDurationMs = 1L)

        diagnostics.beginTrial(true, "QUIET", 0L, true, true)
        recordInference(diagnostics, score = 0.46f, timestampMs = 2L)
        diagnostics.beginTrial(true, "QUIET", 10L, true, true)
        recordInference(diagnostics, score = 0.34f, timestampMs = 12L)
        diagnostics.beginTrial(false, "QUIET", 20L, true, true)
        recordInference(diagnostics, score = 0.44f, timestampMs = 22L)
        diagnostics.beginTrial(false, "QUIET", 30L, true, true)
        recordInference(diagnostics, score = 0.05f, timestampMs = 32L)

        val status = diagnostics.snapshot()
        val at035 = status.thresholdAnalysis.single { it.threshold == 0.35f }
        assertEquals(2, at035.positiveTrials)
        assertEquals(2, at035.negativeTrials)
        assertEquals(1, at035.trueAccepts)
        assertEquals(1, at035.falseRejects)
        assertEquals(1, at035.falseAccepts)
        assertEquals(1, at035.trueNegatives)
        assertEquals(0.5, at035.trueAcceptRate, 0.0)
        assertEquals(0.5, at035.falseRejectRate, 0.0)
        assertEquals(0.5, at035.falseAcceptRate, 0.0)

        val at050 = status.thresholdAnalysis.single { it.threshold == 0.5f }
        assertEquals(0, at050.trueAccepts)
        assertEquals(0, at050.falseAccepts)
    }

    @Test
    fun candidateCooldownCountsDuplicateWindowsWithoutChangingProductionThreshold() {
        val diagnostics = newDiagnostics(trialDurationMs = 3_000L, cooldownMs = 2_000L)
        diagnostics.beginTrial(true, "QUIET", 1_000L, false, true)
        recordInference(diagnostics, score = 0.51f, timestampMs = 1_100L)
        recordInference(diagnostics, score = 0.52f, timestampMs = 1_180L)
        recordInference(diagnostics, score = 0.53f, timestampMs = 4_001L)

        val trial = diagnostics.snapshot().calibrationTrials.single()
        val at050 = trial.thresholdResults.single { it.threshold == 0.5f }
        assertEquals(2L, at050.detectionCount)
        assertEquals(1L, at050.duplicateSuppressionCount)
        assertEquals(100L, at050.firstDetectionLatencyMs)
        assertEquals("NS_ONLY", trial.audioProcessingMode)
    }

    @Test
    fun allAudioProcessingMatrixModesAreRecorded() {
        val diagnostics = newDiagnostics(trialDurationMs = 1L)
        val modes = listOf(
            Triple(true, true, "AEC_NS"),
            Triple(true, false, "AEC_ONLY"),
            Triple(false, true, "NS_ONLY"),
            Triple(false, false, "DISABLED"),
        )

        modes.forEachIndexed { index, (aec, noiseSuppression, expectedMode) ->
            val startedAt = index * 10L
            diagnostics.beginTrial(true, "MATRIX", startedAt, aec, noiseSuppression)
            recordInference(
                diagnostics = diagnostics,
                score = 0.1f,
                timestampMs = startedAt + 2L,
                aecEnabled = aec,
                noiseSuppressionEnabled = noiseSuppression,
            )
            assertEquals(expectedMode, diagnostics.snapshot().calibrationTrials.last().audioProcessingMode)
        }
    }

    private fun newDiagnostics(
        historyCapacity: Int = 64,
        trialDurationMs: Long = 3_000L,
        cooldownMs: Long = 2_000L,
        initiallyEnabled: Boolean = true,
    ) = WakeWordAcousticDiagnostics(
        available = true,
        thresholds = listOf(0.1f, 0.2f, 0.3f, 0.35f, 0.4f, 0.45f, 0.5f),
        trialDurationMs = trialDurationMs,
        trialHistoryCapacity = historyCapacity,
        cooldownMs = cooldownMs,
        initiallyEnabled = initiallyEnabled,
    )

    private fun recordInference(
        diagnostics: WakeWordAcousticDiagnostics,
        score: Float,
        timestampMs: Long,
        aecEnabled: Boolean = true,
        noiseSuppressionEnabled: Boolean = true,
    ) {
        diagnostics.recordInference(
            pcm16 = ShortArray(32) { 1_000 },
            samplesRead = 32,
            inferenceIndex = timestampMs,
            score = score,
            timestampMs = timestampMs,
            queueDepthFrames = 0,
            inferenceDurationNanos = 1_000_000L,
            aecEnabled = aecEnabled,
            noiseSuppressionEnabled = noiseSuppressionEnabled,
            detectionOccurred = false,
            duplicateSuppressed = false,
        )
    }
}
