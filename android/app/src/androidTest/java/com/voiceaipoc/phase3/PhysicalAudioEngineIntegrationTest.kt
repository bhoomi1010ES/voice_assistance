package com.voiceaipoc.phase3

import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.voiceaipoc.audio.AudioConfig
import com.voiceaipoc.audio.AudioEngine
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Test-only physical integration coverage for the existing AudioEngine.
 *
 * This invokes the production AudioEngine repeatedly without changing its
 * configuration or retaining PCM. WebSocket transport coverage is kept in
 * PhysicalVoiceGatewayTest; this test verifies the native capture/effects/VAD
 * integration does not leak state between sessions.
 */
@RunWith(AndroidJUnit4::class)
class PhysicalAudioEngineIntegrationTest {
    @Test
    fun physicalAudioEngineRepeatsStartStopWithNativeStages() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val config = AudioConfig()
        val frameCount = AtomicLong(0)
        val invalidFrame = AtomicBoolean(false)
        val engine = AudioEngine(
            context = context,
            config = config,
            pcmDataCallback = AudioEngine.PcmDataCallback { _, samplesRead ->
                frameCount.incrementAndGet()
                if (samplesRead != config.frameSizeSamples) invalidFrame.set(true)
            },
        )

        try {
            repeat(3) { sessionIndex ->
                val started = engine.startRecording()
                assertTrue("AudioEngine start failed: ${started.errorMessage}", started.succeeded)
                assertTrue(engine.getStatus().audioRecordInitialized)
                assertTrue(engine.getStatus().state == "RECORDING")

                Thread.sleep(1_500)
                val processing = engine.getAudioProcessingStatus()
                val pipeline = engine.getAudioPipelineStatus()
                assertEquals(config.sampleRateHz, pipeline.sampleRateHz)
                assertEquals(config.channelCount, pipeline.channelCount)
                assertEquals(config.frameSizeSamples, pipeline.frameSizeSamples)
                assertEquals(config.frameSizeBytes, pipeline.frameSizeBytes)
                assertTrue("no PCM frames reached the native pipeline", pipeline.totalFramesProcessed > 0)
                assertTrue("Silero VAD was not running", pipeline.sileroVad.running)
                assertTrue("AEC was not enabled", processing.aec.enabled)
                assertTrue("NS was not enabled", processing.noiseSuppression.enabled)
                assertEquals(0L, pipeline.overflowCount)
                assertEquals(0L, pipeline.readErrorCount)
                assertEquals(0L, pipeline.pipelineErrorCount)
                assertEquals(0L, pipeline.sileroVad.errorCount)
                assertFalse("PCM callback received a partial frame", invalidFrame.get())

                val stopped = engine.stopRecording()
                assertTrue("AudioEngine stop failed: ${stopped.errorMessage}", stopped.succeeded)
                assertFalse(engine.getStatus().audioRecordInitialized)
                assertFalse(engine.getAudioPipelineStatus().recording)
                assertEquals(0, engine.getAudioPipelineStatus().bufferedFrames)

                Log.i(
                    TAG,
                    "session=${sessionIndex + 1} frames=${pipeline.totalFramesProcessed} " +
                        "aec=${processing.aec.enabled} ns=${processing.noiseSuppression.enabled} " +
                        "silero_inference=${pipeline.sileroVad.inferenceCount} " +
                        "overflows=${pipeline.overflowCount} read_errors=${pipeline.readErrorCount}",
                )
            }

            assertTrue("no PCM frames were observed across repeated sessions", frameCount.get() > 0)
        } finally {
            engine.release()
        }
    }

    companion object {
        private const val TAG = "Phase3AudioIntegration"
    }
}
