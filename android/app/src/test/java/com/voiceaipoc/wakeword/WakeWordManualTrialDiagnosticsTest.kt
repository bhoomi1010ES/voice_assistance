package com.voiceaipoc.wakeword

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeWordManualTrialDiagnosticsTest {
    private val diagnostics = WakeWordManualTrialDiagnostics(
        detectionThreshold = 0.5f,
        cooldownDurationMs = 2_000L,
    )

    @Test
    fun oneThresholdCrossingProducesOneVisibleDetectionRecord() {
        diagnostics.begin(microphoneSessionId = 42, timestampMs = 1_000L, workerGeneration = 3L)

        val machine = newMachine()
        machine.startListening()
        val before = machine.getStatus()
        val detection = machine.onConfidence(0.75f, wallTimestampMs = 1_080L)
        val after = machine.getStatus()
        diagnostics.recordInference(
            inferenceWindowSequence = 7L,
            inferenceTimestampMs = 1_080L,
            score = 0.75f,
            wakeStateBefore = before.state.name,
            wakeStateAfter = after.state.name,
            cooldownRemainingMs = after.cooldownRemainingMs,
            queueDepthFrames = 1,
            queueHighWaterMarkFrames = 2,
            queueDrops = 0L,
            runtimeErrors = 0L,
            detection = detection,
            suppressedByCooldown = false,
        )

        val status = diagnostics.snapshot()
        assertTrue(status.active)
        assertEquals("MANUAL_WAKE_000001", status.trialId)
        assertEquals(42, status.microphoneSessionId)
        assertEquals(1L, status.inferenceWindowCount)
        assertEquals(1L, status.aboveThresholdWindowCount)
        assertEquals(1L, status.wakeDetectionCount)
        assertEquals(0.75f, status.maximumScore)
        assertEquals(1, status.detections.size)
        assertEquals(7L, status.detections.single().inferenceWindowSequence)
        assertEquals(1, status.thresholdCrossings.size)
        assertTrue(status.thresholdCrossings.single().generatedWakeEvent)
    }

    @Test
    fun repeatedClassifierPeaksRemainVisibleAsCooldownSuppressedCrossings() {
        diagnostics.begin(microphoneSessionId = 8, timestampMs = 2_000L, workerGeneration = 1L)
        val machine = newMachine()
        machine.startListening()

        repeat(3) { index ->
            val before = machine.getStatus()
            val detection = machine.onConfidence(
                0.80f + index * 0.01f,
                wallTimestampMs = 2_100L + index * 80L,
            )
            val after = machine.getStatus()
            diagnostics.recordInference(
                inferenceWindowSequence = index + 1L,
                inferenceTimestampMs = 2_100L + index * 80L,
                score = 0.80f + index * 0.01f,
                wakeStateBefore = before.state.name,
                wakeStateAfter = after.state.name,
                cooldownRemainingMs = after.cooldownRemainingMs,
                queueDepthFrames = 0,
                queueHighWaterMarkFrames = 0,
                queueDrops = 0L,
                runtimeErrors = 0L,
                detection = detection,
                suppressedByCooldown =
                    after.duplicateSuppressionCount > before.duplicateSuppressionCount,
            )
        }

        val status = diagnostics.snapshot()
        assertEquals(3L, status.aboveThresholdWindowCount)
        assertEquals(1L, status.wakeDetectionCount)
        assertEquals(1, status.detections.size)
        assertEquals(3, status.thresholdCrossings.size)
        assertFalse(status.thresholdCrossings.first().suppressedByCooldown)
        assertTrue(status.thresholdCrossings[1].suppressedByCooldown)
        assertTrue(status.thresholdCrossings[2].suppressedByCooldown)
        assertEquals("COOLDOWN", status.thresholdCrossings[1].wakeStateAfter)
    }

    @Test
    fun finishPreservesTrialAndNextSessionGetsNewIdAndWorkerGeneration() {
        diagnostics.begin(microphoneSessionId = 10, timestampMs = 3_000L, workerGeneration = 4L)
        diagnostics.finish(
            timestampMs = 4_000L,
            currentWakeState = "STOPPED",
            cooldownRemainingMs = 0L,
            queueDepthFrames = 0,
            queueHighWaterMarkFrames = 3,
            queueDrops = 0L,
            runtimeErrors = 0L,
        )

        assertFalse(diagnostics.snapshot().active)
        assertEquals(4_000L, diagnostics.snapshot().stopTimestampMs)
        assertEquals(1, diagnostics.history().size)

        diagnostics.begin(microphoneSessionId = 11, timestampMs = 5_000L, workerGeneration = 5L)
        val active = diagnostics.snapshot()
        assertTrue(active.active)
        assertEquals("MANUAL_WAKE_000002", active.trialId)
        assertEquals(5L, active.workerGeneration)
        assertNotNull(active.trialId)
        assertEquals(1, diagnostics.history().size)
    }

    private fun newMachine(): WakeWordStateMachine = WakeWordStateMachine(
        config = WakeWordConfig(
            modelName = "test_wake_word",
            detectionThreshold = 0.5f,
            cooldownMs = 2_000L,
        ),
        monotonicClockMs = { monotonicMs++ },
        wallClockMs = { 1_000L },
    )

    private var monotonicMs = 0L
}
