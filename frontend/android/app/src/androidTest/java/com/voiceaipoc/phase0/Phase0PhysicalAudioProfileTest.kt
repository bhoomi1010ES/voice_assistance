package com.voiceaipoc.phase0

import android.media.AudioManager
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.voiceaipoc.audio.AudioConfig
import com.voiceaipoc.audio.AudioEngine
import com.voiceaipoc.diagnostics.DiagnosticSessionContext
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Phase 0 physical profile capture. It records bounded live microphone
 * metadata only; PCM is consumed by the native pipeline and never retained.
 */
@RunWith(AndroidJUnit4::class)
class Phase0PhysicalAudioProfileTest {
    @Test
    fun capturesAllAudioProcessingProfilesSeparately() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val config = AudioConfig()
        val audioManager = context.getSystemService(AudioManager::class.java)
        val outputRoute = audioManager
            ?.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            ?.joinToString(",") { it.type.toString() }
            .orEmpty()
        val diagnosticSession = DiagnosticSessionContext()
        val engine = AudioEngine(
            context = context,
            config = config,
            diagnosticSession = diagnosticSession,
            pcmDataCallback = AudioEngine.PcmDataCallback { _, _ -> },
        )

        try {
            PROFILES.forEach { profile ->
                diagnosticSession.end()
                val configured = engine.setAudioProcessingCalibrationMode(
                    enableAcousticEchoCancellation = profile.aec,
                    enableNoiseSuppression = profile.ns,
                )
                assertTrue(
                    "profile ${profile.name} was rejected: ${configured.errorMessage}",
                    configured.succeeded,
                )

                val started = engine.startRecording()
                assertTrue(
                    "profile ${profile.name} failed to start: ${started.errorMessage}",
                    started.succeeded,
                )
                Thread.sleep(PROFILE_CAPTURE_MS)

                val processing = engine.getAudioProcessingStatus()
                val pipeline = engine.getAudioPipelineStatus()
                val microphone = engine.getStatus()
                val silero = pipeline.sileroVad
                val echo = engine.getPlaybackEchoAssessment()

                Log.i(
                    TAG,
                    "PHASE0_PROFILE profile=${profile.name} " +
                        "diagnostic_session_id=${microphone.diagnosticSessionId} " +
                        "manufacturer=${processing.manufacturer} model=${processing.model} " +
                        "android_sdk=${processing.androidSdk} android_mode=${audioManager?.mode ?: -1} " +
                        "output_route=$outputRoute " +
                        "capture_source=MIC playback_usage=USAGE_MEDIA " +
                        "audio_session_id=${processing.audioSessionId} " +
                        "aec_supported=${processing.aec.supported} " +
                        "aec_created=${processing.aec.created} " +
                        "aec_enabled=${processing.aec.enabled} " +
                        "ns_supported=${processing.noiseSuppression.supported} " +
                        "ns_created=${processing.noiseSuppression.created} " +
                        "ns_enabled=${processing.noiseSuppression.enabled} " +
                        "silero_probability=${silero.currentProbability ?: "N/A"} " +
                        "silero_inferences=${silero.inferenceCount} " +
                        "echo_similarity=${echo.similarity ?: "N/A"} " +
                        "barge_in_result=NOT_RUN_NO_TTS " +
                        "frames=${pipeline.totalFramesProcessed} " +
                        "overflows=${pipeline.overflowCount} read_errors=${pipeline.readErrorCount} " +
                        "pipeline_errors=${pipeline.pipelineErrorCount}",
                )

                assertTrue("no frames for profile ${profile.name}", pipeline.totalFramesProcessed > 0)
                assertTrue("Silero did not run for profile ${profile.name}", silero.inferenceCount > 0)
                assertTrue("read errors for profile ${profile.name}", pipeline.readErrorCount == 0L)
                assertTrue("pipeline errors for profile ${profile.name}", pipeline.pipelineErrorCount == 0L)
                val stopped = engine.stopRecording()
                assertTrue(
                    "profile ${profile.name} failed to stop: ${stopped.errorMessage}",
                    stopped.succeeded,
                )
            }
        } finally {
            engine.release()
            diagnosticSession.end()
        }
    }

    private data class Profile(val name: String, val aec: Boolean, val ns: Boolean)

    companion object {
        private const val TAG = "Phase0AudioProfile"
        private const val PROFILE_CAPTURE_MS = 1_500L
        private val PROFILES = listOf(
            Profile("DISABLED", aec = false, ns = false),
            Profile("AEC_ONLY", aec = true, ns = false),
            Profile("NS_ONLY", aec = false, ns = true),
            Profile("AEC_NS", aec = true, ns = true),
        )
    }
}
