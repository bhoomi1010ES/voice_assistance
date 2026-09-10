package com.voiceaipoc.wakeword

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.File
import java.io.IOException
import java.nio.FloatBuffer
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

/** Executes the exact three official v0.5.1 ONNX binaries with desktop ORT JNI. */
class OnnxWakeWordRuntimeTest {
    @Test
    fun pcm16ConversionPreservesSignedAmplitudeWithoutScalingOrByteSwap() {
        val source = shortArrayOf(Short.MIN_VALUE, -256, -1, 0, 1, 256, Short.MAX_VALUE)
        val destination = FloatBuffer.allocate(source.size)

        OpenWakeWordPcmPreprocessor.copyRawPcm16ToFloat(
            source = source,
            sourceOffset = 0,
            destination = destination,
            destinationOffset = 0,
            sampleCount = source.size,
        )

        assertArrayEquals(
            floatArrayOf(-32_768f, -256f, -1f, 0f, 1f, 256f, 32_767f),
            FloatArray(source.size) { destination.get(it) },
            0f,
        )
    }

    @Test
    fun approvedModelChainExistsWithExactSizesAndSha256Digests() {
        for (artifact in ApprovedOpenWakeWordModel.CONTRACT.artifacts) {
            val file = modelFile(artifact)
            assertTrue("Missing ${artifact.fileName}", file.isFile)
            assertEquals(artifact.sizeBytes, file.length())
            assertEquals(
                artifact.sha256,
                MessageDigest.getInstance("SHA-256").digest(file.readBytes()).toUpperHex(),
            )
        }
    }

    @Test
    fun actualModelChainLoadsAndValidatesEveryTensorContract() {
        val runtime = newRuntime()

        runtime.initialize()

        assertTrue(runtime.isInitializedForTest())
        assertEquals("ONNX_RUNTIME_ANDROID_CPU", runtime.runtimeName)
        assertEquals("1.24.3", runtime.runtimeVersion)
        assertEquals(1, OnnxWakeWordRuntime.THREAD_COUNT)
        runtime.close()
        assertTrue(runtime.isClosedForTest())
    }

    @Test
    fun realStreamingInferenceRunsAndPreservesPcmInput() {
        val runtime = newRuntime()
        val silence = ShortArray(INFERENCE_SAMPLES)
        val tone = ShortArray(INFERENCE_SAMPLES) { index ->
            (8_192.0 * sin(2.0 * PI * 220.0 * index / SAMPLE_RATE_HZ)).toInt().toShort()
        }
        val toneBefore = tone.copyOf()
        runtime.initialize()

        val silenceScores = List(8) { runtime.predict(silence, silence.size) }
        val toneScores = List(8) { runtime.predict(tone, tone.size) }

        assertTrue(silenceScores.all { it in 0f..1f })
        assertTrue(toneScores.all { it in 0f..1f })
        assertEquals(16, runtime.predictionCountForTest())
        assertArrayEquals(toneBefore, tone)
        runtime.close()
    }

    @Test
    fun firstFiveClassifierResultsAreSuppressedLikeOfficialWrapper() {
        val runtime = newRuntime()
        runtime.initialize()

        repeat(ApprovedOpenWakeWordModel.INITIAL_SCORE_SUPPRESSION_CALLS) {
            assertEquals(0f, runtime.predict(ShortArray(INFERENCE_SAMPLES), INFERENCE_SAMPLES))
        }
        val sixth = runtime.predict(ShortArray(INFERENCE_SAMPLES), INFERENCE_SAMPLES)

        assertTrue(sixth in 0f..1f)
        assertEquals(6, runtime.predictionCountForTest())
        runtime.close()
    }

    @Test
    fun contextPropagatesAndResetRestoresCleanStreamingState() {
        val runtime = newRuntime()
        val pcm = ShortArray(INFERENCE_SAMPLES) { index -> index.toShort() }
        runtime.initialize()
        val baselineHistory = runtime.featureHistorySnapshot()

        runtime.predict(pcm, pcm.size)

        assertArrayEquals(
            pcm.copyOfRange(INFERENCE_SAMPLES - CONTEXT_SAMPLES, INFERENCE_SAMPLES),
            runtime.pcmContextSnapshot(),
        )
        assertFalse(runtime.featureHistorySnapshot().contentEquals(baselineHistory))

        runtime.reset()

        assertTrue(runtime.pcmContextSnapshot().all { it == 0.toShort() })
        assertArrayEquals(baselineHistory, runtime.featureHistorySnapshot(), 0f)
        assertEquals(0, runtime.predictionCountForTest())
        runtime.close()
    }

    @Test
    fun streamingMelContextTransformEmbeddingAndClassifierMatchDirectOnnxOutputs() {
        val runtime = newRuntime()
        val firstPcm = ShortArray(INFERENCE_SAMPLES) { index ->
            (6_000.0 * sin(2.0 * PI * 220.0 * index / SAMPLE_RATE_HZ)).toInt().toShort()
        }
        val secondPcm = ShortArray(INFERENCE_SAMPLES) { index ->
            (9_000.0 * sin(2.0 * PI * 440.0 * index / SAMPLE_RATE_HZ)).toInt().toShort()
        }
        runtime.initialize()

        runtime.predict(firstPcm, firstPcm.size)
        val directFirstMel = runOnnx(
            ApprovedOpenWakeWordModel.MEL_ARTIFACT,
            ApprovedOpenWakeWordModel.MEL_INPUT_NAME,
            arrayOf(FloatArray(firstPcm.size) { firstPcm[it].toFloat() }),
        ).map { it / 10f + 2f }.toFloatArray()
        assertEquals(
            ApprovedOpenWakeWordModel.MEL_FRAMES_FIRST_INFERENCE *
                ApprovedOpenWakeWordModel.MEL_BINS,
            directFirstMel.size,
        )
        assertArrayEquals(
            directFirstMel,
            runtime.melHistorySnapshot().takeLast(directFirstMel.size).toFloatArray(),
            0.000_01f,
        )

        runtime.predict(secondPcm, secondPcm.size)
        val steadyPcm = FloatArray(CONTEXT_SAMPLES + INFERENCE_SAMPLES)
        for (index in 0 until CONTEXT_SAMPLES) {
            steadyPcm[index] = firstPcm[INFERENCE_SAMPLES - CONTEXT_SAMPLES + index].toFloat()
        }
        for (index in secondPcm.indices) {
            steadyPcm[CONTEXT_SAMPLES + index] = secondPcm[index].toFloat()
        }
        val directSteadyMel = runOnnx(
            ApprovedOpenWakeWordModel.MEL_ARTIFACT,
            ApprovedOpenWakeWordModel.MEL_INPUT_NAME,
            arrayOf(steadyPcm),
        ).map { it / 10f + 2f }.toFloatArray()
        assertEquals(
            ApprovedOpenWakeWordModel.MEL_FRAMES_STEADY_INFERENCE *
                ApprovedOpenWakeWordModel.MEL_BINS,
            directSteadyMel.size,
        )
        val melHistory = runtime.melHistorySnapshot()
        assertArrayEquals(
            directSteadyMel,
            melHistory.takeLast(directSteadyMel.size).toFloatArray(),
            0.000_01f,
        )

        val embeddingInput = Array(1) {
            Array(ApprovedOpenWakeWordModel.MEL_HISTORY_FRAMES) { frame ->
                Array(ApprovedOpenWakeWordModel.MEL_BINS) { bin ->
                    floatArrayOf(
                        melHistory[frame * ApprovedOpenWakeWordModel.MEL_BINS + bin],
                    )
                }
            }
        }
        val directEmbedding = runOnnx(
            ApprovedOpenWakeWordModel.EMBEDDING_ARTIFACT,
            ApprovedOpenWakeWordModel.EMBEDDING_INPUT_NAME,
            embeddingInput,
        )
        assertEquals(ApprovedOpenWakeWordModel.CLASSIFIER_FEATURE_SIZE, directEmbedding.size)
        assertArrayEquals(directEmbedding, runtime.latestEmbeddingSnapshot(), 0.000_01f)

        repeat(3) { runtime.predict(secondPcm, secondPcm.size) }
        val sixthScore = runtime.predict(secondPcm, secondPcm.size)
        val featureHistory = runtime.featureHistorySnapshot()
        val classifierInput = Array(1) {
            Array(ApprovedOpenWakeWordModel.CLASSIFIER_HISTORY_FRAMES) { frame ->
                FloatArray(ApprovedOpenWakeWordModel.CLASSIFIER_FEATURE_SIZE) { feature ->
                    featureHistory[
                        frame * ApprovedOpenWakeWordModel.CLASSIFIER_FEATURE_SIZE + feature
                    ]
                }
            }
        }
        val directClassifier = runOnnx(
            ApprovedOpenWakeWordModel.CLASSIFIER_ARTIFACT,
            ApprovedOpenWakeWordModel.CLASSIFIER_INPUT_NAME,
            classifierInput,
        )
        assertEquals(1, directClassifier.size)
        assertEquals(directClassifier[0], sixthScore, 0.000_001f)
        runtime.close()
    }

    @Test
    fun repeatedRealInferenceSessionsInitializeRunResetAndClose() {
        var referenceScores: List<Float>? = null
        repeat(3) {
            val runtime = newRuntime()
            runtime.initialize()
            val scores = mutableListOf<Float>()
            repeat(6) {
                scores += runtime.predict(ShortArray(INFERENCE_SAMPLES), INFERENCE_SAMPLES)
            }
            assertTrue(scores.all { it in 0f..1f })
            referenceScores?.let { assertArrayEquals(it.toFloatArray(), scores.toFloatArray(), 0f) }
            referenceScores = scores
            runtime.reset()
            assertEquals(0, runtime.predictionCountForTest())
            runtime.close()
            assertTrue(runtime.isClosedForTest())
        }
    }

    @Test
    fun missingModelFailsBeforeRuntimeBecomesAvailable() {
        val runtime = OnnxWakeWordRuntime(CONFIG, modelLoader = { artifact ->
            if (artifact.fileName == ApprovedOpenWakeWordModel.CLASSIFIER_FILE_NAME) {
                throw IOException("test classifier missing")
            }
            modelBytes(artifact)
        })

        val error = assertThrows(WakeWordRuntimeException::class.java) {
            runtime.initialize()
        }

        assertEquals(WakeWordEngine.ERROR_RUNTIME_INITIALIZATION, error.errorCode)
        assertFalse(runtime.isInitializedForTest())
        runtime.close()
    }

    @Test
    fun sameSizeTamperedModelIsRejectedBySha256() {
        val tamperedClassifier = modelBytes(ApprovedOpenWakeWordModel.CLASSIFIER_ARTIFACT)
            .copyOf()
            .also { bytes -> bytes[bytes.lastIndex] = (bytes.last().toInt() xor 0x01).toByte() }
        val runtime = OnnxWakeWordRuntime(CONFIG, modelLoader = { artifact ->
            if (artifact.fileName == ApprovedOpenWakeWordModel.CLASSIFIER_FILE_NAME) {
                tamperedClassifier
            } else {
                modelBytes(artifact)
            }
        })

        val error = assertThrows(WakeWordRuntimeException::class.java) {
            runtime.initialize()
        }

        assertEquals(WakeWordEngine.ERROR_MODEL_INVALID, error.errorCode)
        assertFalse(runtime.isInitializedForTest())
        runtime.close()
    }

    @Test
    fun invalidTensorContractRejectsActualApprovedBinary() {
        val invalidClassifier = ApprovedOpenWakeWordModel.CLASSIFIER_ARTIFACT.copy(
            outputs = listOf(
                ApprovedOpenWakeWordModel.CLASSIFIER_ARTIFACT.outputs.single().copy(
                    name = "unexpected_output",
                ),
            ),
        )
        val invalidContract = ApprovedOpenWakeWordModel.CONTRACT.copy(
            artifacts = ApprovedOpenWakeWordModel.CONTRACT.artifacts.map { artifact ->
                if (artifact.fileName == invalidClassifier.fileName) invalidClassifier else artifact
            },
        )
        val runtime = OnnxWakeWordRuntime(CONFIG, ::modelBytes, invalidContract)

        val error = assertThrows(WakeWordRuntimeException::class.java) {
            runtime.initialize()
        }

        assertEquals(WakeWordEngine.ERROR_MODEL_CONTRACT, error.errorCode)
        assertFalse(runtime.isInitializedForTest())
        runtime.close()
    }

    @Test
    fun malformedInputAndInferenceAfterCleanupFailClearly() {
        val runtime = newRuntime()
        runtime.initialize()

        val malformed = assertThrows(WakeWordRuntimeException::class.java) {
            runtime.predict(ShortArray(INFERENCE_SAMPLES - 1), INFERENCE_SAMPLES - 1)
        }
        assertEquals(WakeWordEngine.ERROR_MALFORMED_PCM, malformed.errorCode)

        runtime.close()
        runtime.close()
        val closed = assertThrows(WakeWordRuntimeException::class.java) {
            runtime.predict(ShortArray(INFERENCE_SAMPLES), INFERENCE_SAMPLES)
        }
        assertEquals(WakeWordEngine.ERROR_RUNTIME_UNAVAILABLE, closed.errorCode)
    }

    private fun newRuntime(): OnnxWakeWordRuntime =
        OnnxWakeWordRuntime(CONFIG, ::modelBytes)

    private fun modelBytes(artifact: WakeWordArtifactContract): ByteArray =
        modelFile(artifact).readBytes()

    private fun modelFile(artifact: WakeWordArtifactContract): File = listOf(
        File("src/main/assets/${artifact.assetPath}"),
        File("app/src/main/assets/${artifact.assetPath}"),
        File("android/app/src/main/assets/${artifact.assetPath}"),
    ).firstOrNull(File::isFile) ?: File("src/main/assets/${artifact.assetPath}")

    private fun runOnnx(
        artifact: WakeWordArtifactContract,
        inputName: String,
        input: Any,
    ): FloatArray {
        val environment = OrtEnvironment.getEnvironment()
        OrtSession.SessionOptions().use { options ->
            options.setInterOpNumThreads(1)
            options.setIntraOpNumThreads(1)
            environment.createSession(modelBytes(artifact), options).use { session ->
                OnnxTensor.createTensor(environment, input).use { tensor ->
                    session.run(mapOf(inputName to tensor)).use { result ->
                        return flattenFloatTensor(result.get(0).value)
                    }
                }
            }
        }
    }

    private fun flattenFloatTensor(value: Any?): FloatArray {
        val flattened = mutableListOf<Float>()
        fun append(node: Any?) {
            when (node) {
                is FloatArray -> node.forEach(flattened::add)
                is Array<*> -> node.forEach(::append)
                is Number -> flattened += node.toFloat()
                else -> error("Unexpected ONNX output value: ${node?.javaClass}")
            }
        }
        append(value)
        return flattened.toFloatArray()
    }

    private fun ByteArray.toUpperHex(): String = joinToString(separator = "") {
        "%02X".format(it.toInt() and 0xFF)
    }

    companion object {
        private const val SAMPLE_RATE_HZ = 16_000
        private const val INFERENCE_SAMPLES = 1_280
        private const val CONTEXT_SAMPLES = 480
        private val CONFIG = WakeWordConfig()
    }
}
