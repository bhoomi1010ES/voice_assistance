package com.voiceaipoc.vad.silero

import java.io.File
import java.io.IOException
import java.security.MessageDigest
import kotlin.math.PI
import kotlin.math.sin
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** These tests execute the packaged, approved ONNX binary with desktop ORT JNI. */
class OnnxSileroVadRuntimeTest {
    @Test
    fun approvedModelAssetExistsWithExactSizeAndSha256() {
        assertTrue(modelFile.isFile)
        assertEquals(ApprovedSileroVadModel.SIZE_BYTES, modelFile.length())
        assertEquals(
            ApprovedSileroVadModel.SHA256,
            MessageDigest.getInstance("SHA-256").digest(modelBytes).toUpperHex(),
        )
    }

    @Test
    fun actualModelLoadsAndValidatesTensorMetadataWithPinnedRuntime() {
        val runtime = newRuntime()

        runtime.initialize()

        assertTrue(runtime.isInitializedForTest())
        assertEquals("ONNX_RUNTIME_ANDROID_CPU", runtime.runtimeName)
        assertEquals("1.24.3", runtime.runtimeVersion)
        assertEquals(1, OnnxSileroVadRuntime.THREAD_COUNT)
        runtime.close()
        assertTrue(runtime.isClosedForTest())
    }

    @Test
    fun realInferenceReturnsProbabilityAndPropagatesRecurrentStateAndContext() {
        val runtime = newRuntime()
        val silence = ShortArray(INFERENCE_SAMPLES)
        val tone = ShortArray(INFERENCE_SAMPLES) { index ->
            (8_192.0 * sin(2.0 * PI * 220.0 * index / SAMPLE_RATE_HZ)).toInt().toShort()
        }
        val toneBefore = tone.copyOf()
        runtime.initialize()

        val silenceProbability = runtime.infer(silence, silence.size)
        val stateAfterSilence = runtime.recurrentStateSnapshot()
        val toneProbability = runtime.infer(tone, tone.size)

        assertTrue(silenceProbability in 0f..1f)
        assertTrue(toneProbability in 0f..1f)
        assertNotEquals(silenceProbability, toneProbability, 0.000001f)
        assertTrue(stateAfterSilence.any { it != 0f })
        assertTrue(runtime.recurrentStateSnapshot().any { it != 0f })
        val expectedContext = FloatArray(CONTEXT_SAMPLES) { index ->
            tone[INFERENCE_SAMPLES - CONTEXT_SAMPLES + index] / 32_768.0f
        }
        assertArrayEquals(expectedContext, runtime.contextSnapshot(), 0f)
        assertArrayEquals(toneBefore, tone)
        runtime.close()
    }

    @Test
    fun resetClearsRecurrentStateAndStreamingContext() {
        val runtime = newRuntime()
        runtime.initialize()
        runtime.infer(ShortArray(INFERENCE_SAMPLES) { 10_000 }, INFERENCE_SAMPLES)
        assertTrue(runtime.recurrentStateSnapshot().any { it != 0f })
        assertTrue(runtime.contextSnapshot().any { it != 0f })

        runtime.reset()

        assertTrue(runtime.recurrentStateSnapshot().all { it == 0f })
        assertTrue(runtime.contextSnapshot().all { it == 0f })
        runtime.close()
    }

    @Test
    fun repeatedRealInferenceSessionsLoadRunResetAndCloseCleanly() {
        repeat(3) {
            val runtime = newRuntime()
            runtime.initialize()
            val probability = runtime.infer(ShortArray(INFERENCE_SAMPLES), INFERENCE_SAMPLES)
            assertTrue(probability in 0f..1f)
            runtime.reset()
            assertTrue(runtime.recurrentStateSnapshot().all { it == 0f })
            runtime.close()
            assertTrue(runtime.isClosedForTest())
        }
    }

    @Test
    fun missingModelLoadFailsWithoutCreatingAnInferenceSession() {
        val runtime = OnnxSileroVadRuntime(
            config = CONFIG,
            modelLoader = { throw IOException("test model missing") },
        )

        val error = assertThrows(SileroVadRuntimeException::class.java) {
            runtime.initialize()
        }

        assertEquals(SileroVadEngine.ERROR_RUNTIME_INITIALIZATION, error.errorCode)
        assertFalse(runtime.isInitializedForTest())
        runtime.close()
    }

    @Test
    fun invalidModelIdentityIsRejectedBeforeOnnxSessionCreation() {
        val runtime = OnnxSileroVadRuntime(
            config = CONFIG,
            modelLoader = { ByteArray(64) },
        )

        val error = assertThrows(SileroVadRuntimeException::class.java) {
            runtime.initialize()
        }

        assertEquals(SileroVadEngine.ERROR_MODEL_INVALID, error.errorCode)
        assertFalse(runtime.isInitializedForTest())
        runtime.close()
    }

    @Test
    fun sameSizeTamperedModelIsRejectedBySha256() {
        val tampered = modelBytes.copyOf().also { bytes ->
            bytes[bytes.lastIndex] = (bytes.last().toInt() xor 0x01).toByte()
        }
        val runtime = OnnxSileroVadRuntime(
            config = CONFIG,
            modelLoader = { tampered },
        )

        val error = assertThrows(SileroVadRuntimeException::class.java) {
            runtime.initialize()
        }

        assertEquals(SileroVadEngine.ERROR_MODEL_INVALID, error.errorCode)
        assertFalse(runtime.isInitializedForTest())
        runtime.close()
    }

    @Test
    fun invalidTensorContractRejectsTheActualApprovedModel() {
        val invalidContract = ApprovedSileroVadModel.CONTRACT.copy(
            outputs = ApprovedSileroVadModel.CONTRACT.outputs.map { output ->
                if (output.name == ApprovedSileroVadModel.PROBABILITY_OUTPUT_NAME) {
                    output.copy(name = "unexpected_probability")
                } else {
                    output
                }
            },
        )
        val runtime = OnnxSileroVadRuntime(CONFIG, { modelBytes }, invalidContract)

        val error = assertThrows(SileroVadRuntimeException::class.java) {
            runtime.initialize()
        }

        assertEquals(SileroVadEngine.ERROR_MODEL_CONTRACT, error.errorCode)
        assertFalse(runtime.isInitializedForTest())
        runtime.close()
    }

    @Test
    fun malformedInferenceInputIsRejectedWithoutRunningTheModel() {
        val runtime = newRuntime()
        runtime.initialize()

        val error = assertThrows(SileroVadRuntimeException::class.java) {
            runtime.infer(ShortArray(INFERENCE_SAMPLES - 1), INFERENCE_SAMPLES - 1)
        }

        assertEquals(SileroVadEngine.ERROR_MALFORMED_PCM, error.errorCode)
        runtime.close()
    }

    @Test
    fun inferenceAfterCleanupFailsAndCloseIsIdempotent() {
        val runtime = newRuntime()
        runtime.initialize()
        runtime.close()
        runtime.close()

        val error = assertThrows(SileroVadRuntimeException::class.java) {
            runtime.infer(ShortArray(INFERENCE_SAMPLES), INFERENCE_SAMPLES)
        }

        assertEquals(SileroVadEngine.ERROR_RUNTIME_UNAVAILABLE, error.errorCode)
    }

    private fun newRuntime(): OnnxSileroVadRuntime =
        OnnxSileroVadRuntime(config = CONFIG, modelLoader = { modelBytes })

    private fun ByteArray.toUpperHex(): String = joinToString(separator = "") {
        "%02X".format(it.toInt() and 0xFF)
    }

    companion object {
        private const val SAMPLE_RATE_HZ = 16_000
        private const val INFERENCE_SAMPLES = 512
        private const val CONTEXT_SAMPLES = 64
        private val CONFIG = SileroVadConfig()
        private val modelFile: File by lazy {
            listOf(
                File("src/main/assets/${ApprovedSileroVadModel.ASSET_PATH}"),
                File("app/src/main/assets/${ApprovedSileroVadModel.ASSET_PATH}"),
                File("android/app/src/main/assets/${ApprovedSileroVadModel.ASSET_PATH}"),
            ).firstOrNull(File::isFile)
                ?: File("src/main/assets/${ApprovedSileroVadModel.ASSET_PATH}")
        }
        private val modelBytes: ByteArray by lazy { modelFile.readBytes() }
    }
}
