package com.voiceaipoc.vad

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VadEngineTest {
    private class RecordingListener : VadEngine.Listener {
        val startedEvents = mutableListOf<VadEngine.Event>()
        val stoppedEvents = mutableListOf<VadEngine.Event>()

        override fun onSpeechStarted(event: VadEngine.Event) {
            startedEvents += event
        }

        override fun onSpeechStopped(event: VadEngine.Event) {
            stoppedEvents += event
        }
    }

    private val testConfig = VadConfig(
        enabled = true,
        speechThresholdDbFs = -40.0,
        minimumSpeechDurationMs = 60,
        minimumSilenceDurationMs = 80,
        speechStartConfirmationFrames = 3,
        speechEndConfirmationFrames = 4,
    )

    @Test
    fun silenceFrameIsClassifiedAsNonSpeech() {
        val engine = newEngine()
        engine.startSession()

        val classification = engine.processFrame(silenceFrame(), FRAME_SAMPLES)

        assertEquals(VadEngine.FrameClassification.NON_SPEECH, classification)
        assertEquals("SILENCE", engine.getStatus().state)
        assertEquals(1L, engine.getStatus().nonSpeechFrames)
    }

    @Test
    fun sufficientEnergyFrameIsClassifiedAsSpeech() {
        val engine = newEngine()
        engine.startSession()

        val classification = engine.processFrame(speechFrame(), FRAME_SAMPLES)

        assertEquals(VadEngine.FrameClassification.SPEECH, classification)
        assertTrue(engine.getStatus().lastEnergyDbFs > testConfig.speechThresholdDbFs)
    }

    @Test
    fun singleNoisyFrameDoesNotStartSpeech() {
        val listener = RecordingListener()
        val engine = newEngine(listener = listener)
        engine.startSession()

        engine.processFrame(speechFrame(), FRAME_SAMPLES)

        assertEquals("SILENCE", engine.getStatus().state)
        assertTrue(listener.startedEvents.isEmpty())
    }

    @Test
    fun requiredConsecutiveSpeechFramesStartSpeechOnce() {
        val listener = RecordingListener()
        val engine = newEngine(listener = listener)
        engine.startSession()

        processFrames(engine, speechFrame(), 3)

        assertEquals("SPEECH", engine.getStatus().state)
        assertEquals(1L, engine.getStatus().speechSegments)
        assertEquals(1, listener.startedEvents.size)
        assertEquals(3L, listener.startedEvents.single().frameIndex)
    }

    @Test
    fun singleQuietFrameDoesNotEndSpeech() {
        val listener = RecordingListener()
        val engine = newEngine(listener = listener)
        engine.startSession()
        processFrames(engine, speechFrame(), 3)

        engine.processFrame(silenceFrame(), FRAME_SAMPLES)

        assertEquals("SPEECH", engine.getStatus().state)
        assertTrue(listener.stoppedEvents.isEmpty())
    }

    @Test
    fun requiredConsecutiveSilenceFramesEndSpeechOnce() {
        val listener = RecordingListener()
        val engine = newEngine(listener = listener)
        engine.startSession()
        processFrames(engine, speechFrame(), 3)

        processFrames(engine, silenceFrame(), 4)

        assertEquals("SILENCE", engine.getStatus().state)
        assertEquals(1, listener.stoppedEvents.size)
        assertEquals(7L, listener.stoppedEvents.single().frameIndex)
    }

    @Test
    fun currentAndStoppedSpeechDurationUseTwentyMillisecondFrames() {
        val listener = RecordingListener()
        val engine = newEngine(listener = listener)
        engine.startSession()
        processFrames(engine, speechFrame(), 10)

        assertEquals(200L, engine.getStatus().currentSpeechDurationMs)

        processFrames(engine, silenceFrame(), 4)
        assertEquals(200L, listener.stoppedEvents.single().speechDurationMs)
    }

    @Test
    fun currentSilenceDurationUsesTwentyMillisecondFrames() {
        val engine = newEngine()
        engine.startSession()

        processFrames(engine, silenceFrame(), 6)

        assertEquals(120L, engine.getStatus().currentSilenceDurationMs)
    }

    @Test
    fun stoppingSessionReturnsStateToSilenceAndClearsTransientState() {
        val engine = newEngine()
        engine.startSession()
        processFrames(engine, speechFrame(), 3)

        engine.stopSession()

        val status = engine.getStatus()
        assertFalse(status.sessionActive)
        assertEquals("SILENCE", status.state)
        assertEquals(0, status.consecutiveSpeechFrames)
        assertEquals(0L, status.currentSpeechDurationMs)
    }

    @Test
    fun multipleSpeechSegmentsAreCountedWithoutEventStorms() {
        val listener = RecordingListener()
        val engine = newEngine(listener = listener)
        engine.startSession()

        processFrames(engine, speechFrame(), 3)
        processFrames(engine, silenceFrame(), 4)
        processFrames(engine, speechFrame(), 3)
        processFrames(engine, silenceFrame(), 4)

        assertEquals(2L, engine.getStatus().speechSegments)
        assertEquals(2, listener.startedEvents.size)
        assertEquals(2, listener.stoppedEvents.size)
    }

    @Test
    fun speechThresholdConfigurationChangesClassification() {
        val frame = constantFrame(500)
        val strictEngine = newEngine(testConfig.copy(speechThresholdDbFs = -30.0))
        val permissiveEngine = newEngine(testConfig.copy(speechThresholdDbFs = -40.0))
        strictEngine.startSession()
        permissiveEngine.startSession()

        assertEquals(
            VadEngine.FrameClassification.NON_SPEECH,
            strictEngine.processFrame(frame, FRAME_SAMPLES),
        )
        assertEquals(
            VadEngine.FrameClassification.SPEECH,
            permissiveEngine.processFrame(frame, FRAME_SAMPLES),
        )
    }

    @Test
    fun onlyExactTwentyMillisecondFramesAreProcessed() {
        val engine = newEngine()
        engine.startSession()

        engine.processFrame(ShortArray(FRAME_SAMPLES - 1), FRAME_SAMPLES - 1)
        val rejectedStatus = engine.getStatus()
        assertEquals(1L, rejectedStatus.vadErrorCount)
        assertEquals(0L, rejectedStatus.vadFramesProcessed)

        engine.processFrame(silenceFrame(), FRAME_SAMPLES)
        assertEquals(1L, engine.getStatus().vadFramesProcessed)
        assertEquals(FRAME_DURATION_MS, engine.getStatus().frameDurationMs)
    }

    @Test
    fun vadDoesNotMutatePcmInput() {
        val engine = newEngine()
        val frame = ShortArray(FRAME_SAMPLES) { index ->
            ((index * 97) % Short.MAX_VALUE).toShort()
        }
        val original = frame.copyOf()
        engine.startSession()

        engine.processFrame(frame, frame.size)

        assertArrayEquals(original, frame)
    }

    @Test
    fun multipleCaptureCyclesStartWithFreshSilenceStateAndCounters() {
        val engine = newEngine()
        engine.startSession()
        processFrames(engine, speechFrame(), 3)
        assertEquals("SPEECH", engine.getStatus().state)
        assertEquals(3L, engine.getStatus().vadFramesProcessed)
        engine.stopSession()

        engine.startSession()

        val restarted = engine.getStatus()
        assertTrue(restarted.sessionActive)
        assertEquals("SILENCE", restarted.state)
        assertEquals(0L, restarted.vadFramesProcessed)
        assertEquals(0L, restarted.speechFrames)
        assertEquals(0L, restarted.speechSegments)
        assertEquals(-120.0, restarted.lastEnergyDbFs, 0.001)
    }

    private fun newEngine(
        config: VadConfig = testConfig,
        listener: VadEngine.Listener? = null,
    ): VadEngine = VadEngine(
        config = config,
        frameDurationMs = FRAME_DURATION_MS,
        frameSizeSamples = FRAME_SAMPLES,
        listener = listener,
    )

    private fun processFrames(engine: VadEngine, frame: ShortArray, count: Int) {
        repeat(count) {
            engine.processFrame(frame, frame.size)
        }
    }

    private fun silenceFrame(): ShortArray = ShortArray(FRAME_SAMPLES)

    private fun speechFrame(): ShortArray = constantFrame(5_000)

    private fun constantFrame(amplitude: Int): ShortArray =
        ShortArray(FRAME_SAMPLES) { amplitude.toShort() }

    companion object {
        private const val FRAME_DURATION_MS = 20
        private const val FRAME_SAMPLES = 320
    }
}
