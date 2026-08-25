package com.voiceaipoc.wakeword

import java.io.File
import java.security.MessageDigest
import kotlin.math.PI
import kotlin.math.sin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class WakeWordDiagnosticReplayTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun actualModelReplayRunsTwiceDeterministicallyAndWritesFeatureTraces() {
        val directory = temporaryFolder.newFolder("replay")
        val capture = createValidCapture(directory)
        val replay = WakeWordDiagnosticReplay(
            capture = capture,
            runtimeFactory = WakeWordRuntimeFactory { newRuntime() },
            sampleRateHz = SAMPLE_RATE,
            inferenceSamples = INFERENCE_SAMPLES,
        )

        val result = replay.replayAll(repetitions = 2)

        assertEquals(1, result.captureCount)
        assertEquals(2, result.replayCount)
        assertEquals(2, result.repetitionCount)
        assertTrue(result.results.all { it.inferenceCount == WINDOW_COUNT })
        assertTrue(result.results.all { it.runtimeErrorCount == 0 })
        assertEquals(
            result.results[0].maximumEffectiveScore,
            result.results[1].maximumEffectiveScore,
            0f,
        )
        assertEquals(result.results[0].maximumRawScore, result.results[1].maximumRawScore, 0f)
        assertEquals(result.results[0].pcmSha256, result.results[1].pcmSha256)
        val trace1 = File(directory, result.results[0].traceFileName)
        val trace2 = File(directory, result.results[1].traceFileName)
        assertTrue(trace1.isFile)
        assertTrue(trace2.isFile)
        assertEquals(trace1.sha256(), trace2.sha256())
    }

    @Test
    fun replayRejectsEmptyCaptureSetAndInvalidRepetitionCount() {
        val capture = WakeWordDiagnosticCapture(
            directory = temporaryFolder.newFolder("empty"),
            inferenceSamples = INFERENCE_SAMPLES,
            sampleRateHz = SAMPLE_RATE,
        )
        val replay = WakeWordDiagnosticReplay(
            capture = capture,
            runtimeFactory = WakeWordRuntimeFactory { newRuntime() },
            sampleRateHz = SAMPLE_RATE,
            inferenceSamples = INFERENCE_SAMPLES,
        )

        assertThrows(IllegalArgumentException::class.java) { replay.replayAll(0) }
        assertThrows(IllegalArgumentException::class.java) { replay.replayAll(2) }
    }

    private fun createValidCapture(directory: File): WakeWordDiagnosticCapture {
        val capture = WakeWordDiagnosticCapture(
            directory = directory,
            inferenceSamples = INFERENCE_SAMPLES,
            sampleRateHz = SAMPLE_RATE,
            queueCapacityWindows = WINDOW_COUNT,
        )
        assertTrue(capture.start("POSITIVE_REPLAY", DURATION_MS).succeeded)
        repeat(WINDOW_COUNT) { window ->
            val samples = ShortArray(INFERENCE_SAMPLES) { index ->
                val absoluteIndex = window * INFERENCE_SAMPLES + index
                (8_000.0 * sin(2.0 * PI * 220.0 * absoluteIndex / SAMPLE_RATE)).toInt()
                    .toShort()
            }
            assertTrue(capture.offerInferenceWindow(samples, samples.size))
            Thread.sleep(1)
        }
        val deadline = System.currentTimeMillis() + 5_000L
        while (capture.getStatus().active && System.currentTimeMillis() < deadline) {
            Thread.sleep(10)
        }
        assertTrue(capture.getStatus().records.single().valid)
        return capture
    }

    private fun newRuntime(): OnnxWakeWordRuntime = OnnxWakeWordRuntime(
        config = WakeWordConfig(),
        modelLoader = { artifact -> modelFile(artifact).readBytes() },
    )

    private fun modelFile(artifact: WakeWordArtifactContract): File = listOf(
        File("src/main/assets/${artifact.assetPath}"),
        File("app/src/main/assets/${artifact.assetPath}"),
        File("android/app/src/main/assets/${artifact.assetPath}"),
    ).firstOrNull(File::isFile) ?: File("src/main/assets/${artifact.assetPath}")

    private fun File.sha256(): String = MessageDigest.getInstance("SHA-256")
        .digest(readBytes())
        .joinToString("") { "%02X".format(it.toInt() and 0xFF) }

    companion object {
        private const val SAMPLE_RATE = 16_000
        private const val INFERENCE_SAMPLES = 1_280
        private const val DURATION_MS = 2_560
        private const val WINDOW_COUNT = DURATION_MS / 80
    }
}
