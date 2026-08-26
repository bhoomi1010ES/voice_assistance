package com.voiceaipoc.wakeword

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeWordStateMachineTest {
    private var monotonicMs = 0L
    private var wallMs = 10_000L
    private val config = WakeWordConfig(
        modelName = "test_wake_word",
        detectionThreshold = 0.6f,
        cooldownMs = 2_000L,
    )

    @Test
    fun startsIdleAndEntersListeningExplicitly() {
        val machine = newMachine()

        assertEquals(WakeWordStateMachine.State.IDLE, machine.getStatus().state)
        machine.startListening()

        assertEquals(WakeWordStateMachine.State.LISTENING, machine.getStatus().state)
    }

    @Test
    fun thresholdCrossingProducesOneWakeDetectedTransition() {
        val machine = newMachine()
        machine.startListening()

        val detection = machine.onConfidence(0.8f)

        assertEquals(WakeWordStateMachine.State.WAKE_DETECTED, machine.getStatus().state)
        assertEquals(1L, machine.getStatus().detectionCount)
        assertEquals("test_wake_word", detection?.modelName)
        assertEquals(wallMs, detection?.timestampMs)
        assertEquals(WakeWordStateMachine.State.LISTENING, detection?.stateBefore)
        assertEquals(WakeWordStateMachine.State.WAKE_DETECTED, detection?.stateAfter)
        assertEquals(2_000L, detection?.cooldownRemainingMs)
        assertEquals(null, detection?.millisecondsSincePreviousDetection)
    }

    @Test
    fun confidenceBelowThresholdDoesNotDetect() {
        val machine = newMachine()
        machine.startListening()

        assertNull(machine.onConfidence(0.59f))
        assertEquals(WakeWordStateMachine.State.LISTENING, machine.getStatus().state)
        assertEquals(0L, machine.getStatus().detectionCount)
    }

    @Test
    fun repeatedHighScoreMovesToCooldownAndSuppressesDuplicate() {
        val machine = newMachine()
        machine.startListening()
        machine.onConfidence(0.9f)

        assertNull(machine.onConfidence(0.95f))

        val status = machine.getStatus()
        assertEquals(WakeWordStateMachine.State.COOLDOWN, status.state)
        assertEquals(1L, status.detectionCount)
        assertEquals(1L, status.duplicateSuppressionCount)
        assertEquals(2_000L, status.cooldownRemainingMs)
    }

    @Test
    fun cooldownExpiryReturnsToListeningAndAllowsNextDetection() {
        val machine = newMachine()
        machine.startListening()
        machine.onConfidence(0.9f)
        machine.onConfidence(0.9f)
        monotonicMs = config.cooldownMs
        wallMs += config.cooldownMs

        val secondDetection = machine.onConfidence(0.9f)

        assertEquals(2L, secondDetection?.detectionCount)
        assertEquals(WakeWordStateMachine.State.WAKE_DETECTED, machine.getStatus().state)
    }

    @Test
    fun resetClearsDetectionAndCooldownState() {
        val machine = newMachine()
        machine.startListening()
        machine.onConfidence(0.9f)
        machine.onConfidence(0.9f)

        machine.reset()

        val status = machine.getStatus()
        assertEquals(WakeWordStateMachine.State.IDLE, status.state)
        assertEquals(0L, status.detectionCount)
        assertEquals(0L, status.duplicateSuppressionCount)
        assertNull(status.lastConfidence)
    }

    @Test
    fun configurationRejectsInvalidThresholdCooldownAndQueue() {
        assertThrows(IllegalArgumentException::class.java) {
            WakeWordConfig(detectionThreshold = 0.0f)
        }
        assertThrows(IllegalArgumentException::class.java) {
            WakeWordConfig(cooldownMs = 0L)
        }
        assertThrows(IllegalArgumentException::class.java) {
            WakeWordConfig(inputFramesPerInference = 4, queueCapacityFrames = 3)
        }
        assertTrue(WakeWordConfig().enabled)
    }

    private fun newMachine(): WakeWordStateMachine = WakeWordStateMachine(
        config = config,
        monotonicClockMs = { monotonicMs },
        wallClockMs = { wallMs },
    )
}
