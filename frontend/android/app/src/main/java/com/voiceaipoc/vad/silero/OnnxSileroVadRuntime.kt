package com.voiceaipoc.vad.silero

import ai.onnxruntime.NodeInfo
import ai.onnxruntime.OnnxJavaType
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import ai.onnxruntime.TensorInfo
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.nio.LongBuffer
import java.security.MessageDigest
import java.util.LinkedHashMap

/** Real CPU ONNX Runtime adapter for the approved Silero VAD v6.2.1 model. */
class OnnxSileroVadRuntime(
    private val config: SileroVadConfig,
    private val modelLoader: () -> ByteArray,
    private val contract: SileroVadModelContract = ApprovedSileroVadModel.CONTRACT,
) : SileroVadRuntime {
    override val runtimeName: String = RUNTIME_NAME
    override val runtimeVersion: String = RUNTIME_VERSION

    private var sessionOptions: OrtSession.SessionOptions? = null
    private var session: OrtSession? = null
    private var audioInputBuffer: FloatBuffer? = null
    private var stateInputBuffer: FloatBuffer? = null
    private var sampleRateInputBuffer: LongBuffer? = null
    private var probabilityOutputBuffer: FloatBuffer? = null
    private var stateOutputBuffer: FloatBuffer? = null
    private var inputTensors: LinkedHashMap<String, OnnxTensor>? = null
    private var outputTensors: LinkedHashMap<String, OnnxTensor>? = null
    private val context = FloatArray(config.modelContextSamples)
    private var initialized = false
    private var closed = false

    override fun initialize() {
        check(!closed) { "Silero ONNX runtime is closed." }
        if (initialized) {
            return
        }

        try {
            val modelBytes = modelLoader()
            validateModelIdentity(modelBytes)

            val environment = OrtEnvironment.getEnvironment()
            val options = OrtSession.SessionOptions().also {
                it.setInterOpNumThreads(THREAD_COUNT)
                it.setIntraOpNumThreads(THREAD_COUNT)
                it.setExecutionMode(OrtSession.SessionOptions.ExecutionMode.SEQUENTIAL)
            }
            sessionOptions = options
            val loadedSession = environment.createSession(modelBytes, options)
            session = loadedSession
            validateTensorContract(loadedSession)

            val audioBuffer = allocateFloatBuffer(MODEL_INPUT_SAMPLES)
            val stateInBuffer = allocateFloatBuffer(RECURRENT_STATE_ELEMENTS)
            val sampleRateBuffer = allocateLongBuffer(1)
            val probabilityBuffer = allocateFloatBuffer(1)
            val stateOutBuffer = allocateFloatBuffer(RECURRENT_STATE_ELEMENTS)
            sampleRateBuffer.put(0, config.sampleRateHz.toLong())

            audioInputBuffer = audioBuffer
            stateInputBuffer = stateInBuffer
            sampleRateInputBuffer = sampleRateBuffer
            probabilityOutputBuffer = probabilityBuffer
            stateOutputBuffer = stateOutBuffer

            inputTensors = linkedMapOf(
                ApprovedSileroVadModel.AUDIO_INPUT_NAME to OnnxTensor.createTensor(
                    environment,
                    audioBuffer,
                    AUDIO_INPUT_SHAPE,
                ),
                ApprovedSileroVadModel.STATE_INPUT_NAME to OnnxTensor.createTensor(
                    environment,
                    stateInBuffer,
                    STATE_SHAPE,
                ),
                ApprovedSileroVadModel.SAMPLE_RATE_INPUT_NAME to OnnxTensor.createTensor(
                    environment,
                    sampleRateBuffer,
                    SCALAR_SHAPE,
                ),
            )
            outputTensors = linkedMapOf(
                ApprovedSileroVadModel.PROBABILITY_OUTPUT_NAME to OnnxTensor.createTensor(
                    environment,
                    probabilityBuffer,
                    PROBABILITY_OUTPUT_SHAPE,
                ),
                ApprovedSileroVadModel.STATE_OUTPUT_NAME to OnnxTensor.createTensor(
                    environment,
                    stateOutBuffer,
                    STATE_SHAPE,
                ),
            )
            resetBuffers()
            initialized = true
        } catch (exception: Throwable) {
            if (exception is VirtualMachineError || exception is ThreadDeath) {
                throw exception
            }
            closeResources()
            if (exception is SileroVadRuntimeException) {
                throw exception
            }
            throw SileroVadRuntimeException(
                SileroVadEngine.ERROR_RUNTIME_INITIALIZATION,
                "ONNX Runtime could not initialize Silero VAD: ${exception.message}",
                exception,
            )
        }
    }

    override fun infer(pcm16: ShortArray, samplesRead: Int): Float {
        ensureReady()
        if (samplesRead != config.inferenceChunkSamples || samplesRead > pcm16.size) {
            throw SileroVadRuntimeException(
                SileroVadEngine.ERROR_MALFORMED_PCM,
                "Silero inference requires ${config.inferenceChunkSamples} samples; " +
                    "received $samplesRead.",
            )
        }

        val audioBuffer = requireNotNull(audioInputBuffer)
        for (index in context.indices) {
            audioBuffer.put(index, context[index])
        }
        for (index in 0 until samplesRead) {
            audioBuffer.put(config.modelContextSamples + index, pcm16[index] / PCM_SCALE)
        }

        try {
            val activeSession = requireNotNull(session)
            val inputs = requireNotNull(inputTensors)
            val outputs = requireNotNull(outputTensors)
            activeSession.run(inputs, outputs).use {
                val probability = requireNotNull(probabilityOutputBuffer).get(0)
                if (!probability.isFinite() || probability !in 0f..1f) {
                    throw SileroVadRuntimeException(
                        SileroVadEngine.ERROR_INFERENCE,
                        "Silero model returned invalid probability $probability.",
                    )
                }

                val stateIn = requireNotNull(stateInputBuffer)
                val stateOut = requireNotNull(stateOutputBuffer)
                for (index in 0 until RECURRENT_STATE_ELEMENTS) {
                    stateIn.put(index, stateOut.get(index))
                }
                val contextStart = samplesRead - config.modelContextSamples
                for (index in context.indices) {
                    context[index] = pcm16[contextStart + index] / PCM_SCALE
                }
                return probability
            }
        } catch (exception: Throwable) {
            if (exception is VirtualMachineError || exception is ThreadDeath) {
                throw exception
            }
            resetBuffers()
            if (exception is SileroVadRuntimeException) {
                throw exception
            }
            throw SileroVadRuntimeException(
                SileroVadEngine.ERROR_INFERENCE,
                "Silero ONNX inference failed: ${exception.message}",
                exception,
            )
        }
    }

    override fun reset() {
        resetBuffers()
    }

    override fun close() {
        if (closed) {
            return
        }
        resetBuffers()
        val failure = closeResources()
        closed = true
        if (failure != null) {
            throw SileroVadRuntimeException(
                SileroVadEngine.ERROR_RUNTIME_RELEASE,
                "Silero ONNX resources did not close cleanly: ${failure.message}",
                failure,
            )
        }
    }

    internal fun recurrentStateSnapshot(): FloatArray =
        FloatArray(RECURRENT_STATE_ELEMENTS) { index -> stateInputBuffer?.get(index) ?: 0f }

    internal fun contextSnapshot(): FloatArray = context.copyOf()

    internal fun isInitializedForTest(): Boolean = initialized

    internal fun isClosedForTest(): Boolean = closed

    private fun validateModelIdentity(modelBytes: ByteArray) {
        if (modelBytes.size.toLong() != contract.sizeBytes) {
            throw SileroVadRuntimeException(
                SileroVadEngine.ERROR_MODEL_INVALID,
                "Silero model size mismatch: expected=${contract.sizeBytes}, " +
                    "actual=${modelBytes.size}.",
            )
        }
        val actualHash = MessageDigest.getInstance(SHA256_ALGORITHM)
            .digest(modelBytes)
            .toUpperHex()
        if (actualHash != contract.sha256) {
            throw SileroVadRuntimeException(
                SileroVadEngine.ERROR_MODEL_INVALID,
                "Silero model SHA-256 mismatch.",
            )
        }
    }

    private fun validateTensorContract(loadedSession: OrtSession) {
        validateNodes("input", loadedSession.inputInfo, contract.inputs)
        validateNodes("output", loadedSession.outputInfo, contract.outputs)
    }

    private fun validateNodes(
        direction: String,
        actualNodes: Map<String, NodeInfo>,
        expectedNodes: List<SileroVadTensorContract>,
    ) {
        val expectedNames = expectedNodes.mapTo(linkedSetOf()) { it.name }
        if (actualNodes.keys != expectedNames) {
            throw invalidContract(
                "Silero $direction names mismatch: expected=$expectedNames, " +
                    "actual=${actualNodes.keys}.",
            )
        }
        for (expected in expectedNodes) {
            val tensorInfo = actualNodes[expected.name]?.info as? TensorInfo
                ?: throw invalidContract("Silero ${expected.name} is not a tensor.")
            val expectedType = when (expected.type) {
                SileroVadTensorType.FLOAT32 -> OnnxJavaType.FLOAT
                SileroVadTensorType.INT64 -> OnnxJavaType.INT64
            }
            if (tensorInfo.type != expectedType) {
                throw invalidContract(
                    "Silero ${expected.name} type mismatch: expected=$expectedType, " +
                        "actual=${tensorInfo.type}.",
                )
            }
            if (!tensorInfo.shape.contentEquals(expected.graphShape.toLongArray())) {
                throw invalidContract(
                    "Silero ${expected.name} shape mismatch: " +
                        "expected=${expected.graphShape}, " +
                        "actual=${tensorInfo.shape.contentToString()}.",
                )
            }
        }
    }

    private fun invalidContract(message: String) = SileroVadRuntimeException(
        SileroVadEngine.ERROR_MODEL_CONTRACT,
        message,
    )

    private fun ensureReady() {
        if (!initialized || closed) {
            throw SileroVadRuntimeException(
                SileroVadEngine.ERROR_RUNTIME_UNAVAILABLE,
                "Silero ONNX runtime is not initialized.",
            )
        }
    }

    private fun resetBuffers() {
        context.fill(0f)
        audioInputBuffer?.fillWithZero()
        stateInputBuffer?.fillWithZero()
        probabilityOutputBuffer?.fillWithZero()
        stateOutputBuffer?.fillWithZero()
        sampleRateInputBuffer?.put(0, config.sampleRateHz.toLong())
    }

    private fun closeResources(): Throwable? {
        var firstFailure: Throwable? = null
        fun close(resource: AutoCloseable?) {
            try {
                resource?.close()
            } catch (exception: Throwable) {
                if (firstFailure == null) {
                    firstFailure = exception
                }
            }
        }

        outputTensors?.values?.forEach(::close)
        inputTensors?.values?.forEach(::close)
        outputTensors?.clear()
        inputTensors?.clear()
        outputTensors = null
        inputTensors = null
        close(session)
        session = null
        close(sessionOptions)
        sessionOptions = null
        audioInputBuffer = null
        stateInputBuffer = null
        sampleRateInputBuffer = null
        probabilityOutputBuffer = null
        stateOutputBuffer = null
        initialized = false
        return firstFailure
    }

    private fun FloatBuffer.fillWithZero() {
        for (index in 0 until capacity()) {
            put(index, 0f)
        }
    }

    private fun ByteArray.toUpperHex(): String = buildString(size * 2) {
        for (byte in this@toUpperHex) {
            val value = byte.toInt() and 0xFF
            append(HEX_DIGITS[value ushr 4])
            append(HEX_DIGITS[value and 0x0F])
        }
    }

    companion object {
        const val RUNTIME_NAME = "ONNX_RUNTIME_ANDROID_CPU"
        const val RUNTIME_VERSION = "1.24.3"
        const val THREAD_COUNT = 1

        private const val PCM_SCALE = 32_768.0f
        private const val MODEL_INPUT_SAMPLES = 576
        private const val RECURRENT_STATE_ELEMENTS = 2 * 1 * 128
        private const val SHA256_ALGORITHM = "SHA-256"
        private const val HEX_DIGITS = "0123456789ABCDEF"
        private val AUDIO_INPUT_SHAPE = longArrayOf(1, MODEL_INPUT_SAMPLES.toLong())
        private val STATE_SHAPE = longArrayOf(2, 1, 128)
        private val SCALAR_SHAPE = longArrayOf()
        private val PROBABILITY_OUTPUT_SHAPE = longArrayOf(1, 1)

        private fun allocateFloatBuffer(elements: Int): FloatBuffer =
            ByteBuffer.allocateDirect(elements * Float.SIZE_BYTES)
                .order(ByteOrder.nativeOrder())
                .asFloatBuffer()

        private fun allocateLongBuffer(elements: Int): LongBuffer =
            ByteBuffer.allocateDirect(elements * Long.SIZE_BYTES)
                .order(ByteOrder.nativeOrder())
                .asLongBuffer()
    }
}
