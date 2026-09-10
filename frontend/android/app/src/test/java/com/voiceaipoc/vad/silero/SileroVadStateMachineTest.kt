package com.voiceaipoc.vad.silero

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class SileroVadStateMachineTest {
    private var wallClockMs = 10_000L
    private val config = SileroVadConfig(
        speechProbabilityThreshold = 0.5f,
        speechStartConfirmationMs = 96,
        speechStopHangoverMs = 320,
    )

    @Test
    fun modelConfigurationMatchesSixteenKilohertzStreamingContract() {
        assertEquals("silero_vad/silero_vad.onnx", config.modelAssetPath)
        assertEquals("ONNX", config.modelFormat)
        assertEquals(16_000, config.sampleRateHz)
        assertEquals(512, config.inferenceChunkSamples)
        assertEquals(32, config.inferenceChunkDurationMs)
        assertEquals(64, config.modelContextSamples)
        assertEquals(3, config.speechStartConfirmationChunks)
        assertEquals(10, config.speechStopConfirmationChunks)
    }

    @Test
    fun invalidConfigurationIsRejected() {
        assertThrows(IllegalArgumentException::class.java) {
            SileroVadConfig(modelFileName = "silero_vad.tflite")
        }
        assertThrows(IllegalArgumentException::class.java) {
            SileroVadConfig(sampleRateHz = 8_000)
        }
        assertThrows(IllegalArgumentException::class.java) {
            SileroVadConfig(speechProbabilityThreshold = 0f)
        }
        assertThrows(IllegalArgumentException::class.java) {
            SileroVadConfig(queueCapacityFrames = 0)
        }
    }

    @Test
    fun silenceAndBelowThresholdProbabilityRemainSilent() {
        val machine = newMachine()

        assertNull(machine.onProbability(0f, 1L))
        assertNull(machine.onProbability(0.499f, 2L))

        assertEquals(SileroVadStateMachine.State.SILENCE, machine.getStatus().state)
        assertEquals(2L, machine.getStatus().decisionsProcessed)
    }

    @Test
    fun speechRequiresConfiguredConfirmationAndThresholdIsInclusive() {
        val machine = newMachine()

        assertNull(machine.onProbability(0.5f, 1L))
        assertEquals(
            SileroVadStateMachine.State.SPEECH_START_PENDING,
            machine.getStatus().state,
        )
        assertNull(machine.onProbability(0.8f, 2L))
        val started = machine.onProbability(0.7f, 3L)

        assertEquals(SileroVadEngine.EVENT_SPEECH_STARTED, started?.event)
        assertEquals(SileroVadStateMachine.State.SPEECH, machine.getStatus().state)
        assertEquals(1L, machine.getStatus().speechStartCount)
    }

    @Test
    fun interruptedStartConfirmationReturnsToSilence() {
        val machine = newMachine()
        machine.onProbability(0.8f, 1L)

        assertNull(machine.onProbability(0.1f, 2L))

        assertEquals(SileroVadStateMachine.State.SILENCE, machine.getStatus().state)
        assertEquals(0L, machine.getStatus().speechStartCount)
    }

    @Test
    fun oneQuietDecisionDoesNotEndSpeechAndSpeechCancelsPendingStop() {
        val machine = speakingMachine()

        assertNull(machine.onProbability(0.1f, 4L))
        assertEquals(
            SileroVadStateMachine.State.SPEECH_STOP_PENDING,
            machine.getStatus().state,
        )
        assertNull(machine.onProbability(0.9f, 5L))

        assertEquals(SileroVadStateMachine.State.SPEECH, machine.getStatus().state)
        assertEquals(0L, machine.getStatus().speechStopCount)
    }

    @Test
    fun configuredHangoverEndsSpeechAndCalculatesDuration() {
        val machine = speakingMachine()
        var stopped: SileroVadStateMachine.Transition? = null

        repeat(config.speechStopConfirmationChunks) { offset ->
            stopped = machine.onProbability(0.1f, 4L + offset)
        }

        assertEquals(SileroVadEngine.EVENT_SPEECH_STOPPED, stopped?.event)
        assertEquals("SILENCE_CONFIRMED", stopped?.reason)
        assertEquals(13L * config.inferenceChunkDurationMs, stopped?.speechDurationMs)
        assertEquals(SileroVadStateMachine.State.SILENCE, machine.getStatus().state)
        assertEquals(1L, machine.getStatus().speechStopCount)
    }

    @Test
    fun resetAndRepeatedSpeechCyclesAreDeterministic() {
        val machine = newMachine()
        repeat(2) { cycle ->
            val base = cycle * 13L
            repeat(config.speechStartConfirmationChunks) { offset ->
                machine.onProbability(0.9f, base + offset + 1L)
            }
            repeat(config.speechStopConfirmationChunks) { offset ->
                machine.onProbability(
                    0.1f,
                    base + config.speechStartConfirmationChunks + offset + 1L,
                )
            }
        }
        assertEquals(2L, machine.getStatus().speechStartCount)
        assertEquals(2L, machine.getStatus().speechStopCount)

        machine.reset()

        val status = machine.getStatus()
        assertEquals(SileroVadStateMachine.State.SILENCE, status.state)
        assertEquals(0L, status.speechStartCount)
        assertEquals(0L, status.speechStopCount)
        assertEquals(0L, status.decisionsProcessed)
        assertNull(status.lastProbability)
    }

    @Test
    fun sessionStopEmitsOneSemanticStopOnlyWhenSpeechIsActive() {
        val machine = speakingMachine()

        val stopped = machine.stop(3L)

        assertEquals(SileroVadEngine.EVENT_SPEECH_STOPPED, stopped?.event)
        assertEquals("SESSION_STOPPED", stopped?.reason)
        assertEquals(SileroVadStateMachine.State.SILENCE, machine.getStatus().state)
        assertNull(machine.stop(3L))
        assertTrue(machine.getStatus().speechStopCount >= 1L)
    }

    private fun speakingMachine(): SileroVadStateMachine = newMachine().also { machine ->
        repeat(config.speechStartConfirmationChunks) { offset ->
            machine.onProbability(0.9f, offset + 1L)
        }
    }

    private fun newMachine(): SileroVadStateMachine = SileroVadStateMachine(
        config,
        wallClockMs = { wallClockMs },
    )
}
