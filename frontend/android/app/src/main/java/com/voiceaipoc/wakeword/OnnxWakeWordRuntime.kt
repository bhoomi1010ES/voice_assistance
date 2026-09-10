package com.voiceaipoc.wakeword

import android.util.Log
import ai.onnxruntime.NodeInfo
import ai.onnxruntime.OnnxJavaType
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import ai.onnxruntime.TensorInfo
import com.voiceaipoc.audio.AudioEngine
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.security.MessageDigest
import java.util.LinkedHashMap

/**
 * Real CPU ONNX implementation of the official openWakeWord v0.5.1 chain.
 *
 * The runtime ports the upstream streaming contract: 80 ms PCM hops, 30 ms
 * prior PCM context for melspectrogram continuity, a 76x32 mel history, one
 * 96-value embedding per hop, and a 16x96 classifier history. All hot-path
 * tensors and buffers are allocated during initialization and reused.
 */
class OnnxWakeWordRuntime(
    private val config: WakeWordConfig,
    private val modelLoader: (WakeWordArtifactContract) -> ByteArray,
    private val contract: OpenWakeWordModelContract = ApprovedOpenWakeWordModel.CONTRACT,
) : WakeWordInferenceRuntime {
    override val runtimeName: String = RUNTIME_NAME
    override val runtimeVersion: String = RUNTIME_VERSION
    override var tensorContractVerified: Boolean = false
        private set

    private var sessionOptions: OrtSession.SessionOptions? = null
    private var melSession: OrtSession? = null
    private var embeddingSession: OrtSession? = null
    private var classifierSession: OrtSession? = null

    private var melFirstInputBuffer: FloatBuffer? = null
    private var melFirstOutputBuffer: FloatBuffer? = null
    private var melSteadyInputBuffer: FloatBuffer? = null
    private var melSteadyOutputBuffer: FloatBuffer? = null
    private var embeddingInputBuffer: FloatBuffer? = null
    private var embeddingOutputBuffer: FloatBuffer? = null
    private var classifierInputBuffer: FloatBuffer? = null
    private var classifierOutputBuffer: FloatBuffer? = null

    private var melFirstInputs: LinkedHashMap<String, OnnxTensor>? = null
    private var melFirstOutputs: LinkedHashMap<String, OnnxTensor>? = null
    private var melSteadyInputs: LinkedHashMap<String, OnnxTensor>? = null
    private var melSteadyOutputs: LinkedHashMap<String, OnnxTensor>? = null
    private var embeddingInputs: LinkedHashMap<String, OnnxTensor>? = null
    private var embeddingOutputs: LinkedHashMap<String, OnnxTensor>? = null
    private var classifierInputs: LinkedHashMap<String, OnnxTensor>? = null
    private var classifierOutputs: LinkedHashMap<String, OnnxTensor>? = null

    private val pcmContext = ShortArray(ApprovedOpenWakeWordModel.MEL_CONTEXT_SAMPLES)
    private val melHistory = FloatArray(
        ApprovedOpenWakeWordModel.MEL_HISTORY_FRAMES * ApprovedOpenWakeWordModel.MEL_BINS,
    )
    private val featureHistory = FloatArray(
        ApprovedOpenWakeWordModel.CLASSIFIER_HISTORY_FRAMES *
            ApprovedOpenWakeWordModel.CLASSIFIER_FEATURE_SIZE,
    )
    private val baselineEmbedding = FloatArray(ApprovedOpenWakeWordModel.CLASSIFIER_FEATURE_SIZE)

    private var hasPriorContext = false
    private var predictionCount = 0
    private var lastRawConfidence: Float? = null
    private var initialized = false
    private var closed = false

    override fun initialize() {
        check(!closed) { "openWakeWord ONNX runtime is closed." }
        if (initialized) {
            return
        }

        try {
            val melArtifact = contract.artifactNamed(ApprovedOpenWakeWordModel.MEL_FILE_NAME)
            val embeddingArtifact = contract.artifactNamed(
                ApprovedOpenWakeWordModel.EMBEDDING_FILE_NAME,
            )
            val classifierArtifact = contract.artifactNamed(
                ApprovedOpenWakeWordModel.CLASSIFIER_FILE_NAME,
            )
            val artifactBytes = contract.artifacts.associateWith { artifact ->
                modelLoader(artifact).also { validateModelIdentity(artifact, it) }
            }
            val environment = OrtEnvironment.getEnvironment()
            val options = OrtSession.SessionOptions().also {
                it.setInterOpNumThreads(THREAD_COUNT)
                it.setIntraOpNumThreads(THREAD_COUNT)
                it.setExecutionMode(OrtSession.SessionOptions.ExecutionMode.SEQUENTIAL)
            }
            sessionOptions = options

            val loadedMelSession = environment.createSession(
                requireNotNull(artifactBytes[melArtifact]),
                options,
            )
            val loadedEmbeddingSession = environment.createSession(
                requireNotNull(artifactBytes[embeddingArtifact]),
                options,
            )
            val loadedClassifierSession = environment.createSession(
                requireNotNull(artifactBytes[classifierArtifact]),
                options,
            )
            melSession = loadedMelSession
            embeddingSession = loadedEmbeddingSession
            classifierSession = loadedClassifierSession
            validateTensorContract(loadedMelSession, melArtifact)
            validateTensorContract(loadedEmbeddingSession, embeddingArtifact)
            validateTensorContract(loadedClassifierSession, classifierArtifact)
            tensorContractVerified = true

            allocateTensorBindings(environment)
            initializeDeterministicFeatureHistory()
            resetBuffers()
            initialized = true
        } catch (exception: Throwable) {
            if (exception is VirtualMachineError || exception is ThreadDeath) {
                throw exception
            }
            closeResources()
            if (exception is WakeWordRuntimeException) {
                throw exception
            }
            throw WakeWordRuntimeException(
                WakeWordEngine.ERROR_RUNTIME_INITIALIZATION,
                "ONNX Runtime could not initialize openWakeWord: ${exception.message}",
                exception,
            )
        }
    }

    override fun predict(pcm16: ShortArray, samplesRead: Int): Float {
        ensureReady()
        if (samplesRead != contract.inferenceSamples || samplesRead > pcm16.size) {
            throw WakeWordRuntimeException(
                WakeWordEngine.ERROR_MALFORMED_PCM,
                "openWakeWord requires ${contract.inferenceSamples} PCM samples; " +
                    "received $samplesRead.",
            )
        }

        try {
            val melFrames = runMelspectrogram(pcm16)
            appendMelFrames(melFrames)
            runEmbedding()
            val rawConfidence = runClassifier()
            lastRawConfidence = rawConfidence
            predictionCount += 1
            return if (predictionCount <= ApprovedOpenWakeWordModel.INITIAL_SCORE_SUPPRESSION_CALLS) {
                0f
            } else {
                rawConfidence
            }
        } catch (exception: Throwable) {
            if (exception is VirtualMachineError || exception is ThreadDeath) {
                throw exception
            }
            resetBuffers()
            if (exception is WakeWordRuntimeException) {
                throw exception
            }
            throw WakeWordRuntimeException(
                WakeWordEngine.ERROR_INFERENCE,
                "openWakeWord ONNX inference failed: ${exception.message}",
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
            throw WakeWordRuntimeException(
                WakeWordEngine.ERROR_RUNTIME_RELEASE,
                "openWakeWord ONNX resources did not close cleanly: ${failure.message}",
                failure,
            )
        }
    }

    internal fun pcmContextSnapshot(): ShortArray = pcmContext.copyOf()

    internal fun featureHistorySnapshot(): FloatArray = featureHistory.copyOf()

    internal fun melHistorySnapshot(): FloatArray = melHistory.copyOf()

    internal fun latestEmbeddingSnapshot(): FloatArray = featureHistory.copyOfRange(
        featureHistory.size - contract.classifierFeatureSize,
        featureHistory.size,
    )

    /** Raw classifier output before the upstream first-five score suppression. */
    internal fun lastRawConfidenceSnapshot(): Float? = lastRawConfidence

    internal fun predictionCountForTest(): Int = predictionCount

    internal fun isInitializedForTest(): Boolean = initialized

    internal fun isClosedForTest(): Boolean = closed

    private fun allocateTensorBindings(environment: OrtEnvironment) {
        val firstInput = allocateFloatBuffer(contract.inferenceSamples)
        val firstOutput = allocateFloatBuffer(
            ApprovedOpenWakeWordModel.MEL_FRAMES_FIRST_INFERENCE *
                ApprovedOpenWakeWordModel.MEL_BINS,
        )
        val steadyInput = allocateFloatBuffer(
            contract.inferenceSamples + contract.melContextSamples,
        )
        val steadyOutput = allocateFloatBuffer(
            ApprovedOpenWakeWordModel.MEL_FRAMES_STEADY_INFERENCE *
                ApprovedOpenWakeWordModel.MEL_BINS,
        )
        val embeddingInput = allocateFloatBuffer(melHistory.size)
        val embeddingOutput = allocateFloatBuffer(contract.classifierFeatureSize)
        val classifierInput = allocateFloatBuffer(featureHistory.size)
        val classifierOutput = allocateFloatBuffer(1)

        melFirstInputBuffer = firstInput
        melFirstOutputBuffer = firstOutput
        melSteadyInputBuffer = steadyInput
        melSteadyOutputBuffer = steadyOutput
        embeddingInputBuffer = embeddingInput
        embeddingOutputBuffer = embeddingOutput
        classifierInputBuffer = classifierInput
        classifierOutputBuffer = classifierOutput

        melFirstInputs = linkedMapOf(
            ApprovedOpenWakeWordModel.MEL_INPUT_NAME to OnnxTensor.createTensor(
                environment,
                firstInput,
                longArrayOf(1, contract.inferenceSamples.toLong()),
            ),
        )
        melFirstOutputs = linkedMapOf(
            ApprovedOpenWakeWordModel.MEL_OUTPUT_NAME to OnnxTensor.createTensor(
                environment,
                firstOutput,
                longArrayOf(
                    1,
                    1,
                    ApprovedOpenWakeWordModel.MEL_FRAMES_FIRST_INFERENCE.toLong(),
                    ApprovedOpenWakeWordModel.MEL_BINS.toLong(),
                ),
            ),
        )
        melSteadyInputs = linkedMapOf(
            ApprovedOpenWakeWordModel.MEL_INPUT_NAME to OnnxTensor.createTensor(
                environment,
                steadyInput,
                longArrayOf(
                    1,
                    (contract.inferenceSamples + contract.melContextSamples).toLong(),
                ),
            ),
        )
        melSteadyOutputs = linkedMapOf(
            ApprovedOpenWakeWordModel.MEL_OUTPUT_NAME to OnnxTensor.createTensor(
                environment,
                steadyOutput,
                longArrayOf(
                    1,
                    1,
                    ApprovedOpenWakeWordModel.MEL_FRAMES_STEADY_INFERENCE.toLong(),
                    ApprovedOpenWakeWordModel.MEL_BINS.toLong(),
                ),
            ),
        )
        embeddingInputs = linkedMapOf(
            ApprovedOpenWakeWordModel.EMBEDDING_INPUT_NAME to OnnxTensor.createTensor(
                environment,
                embeddingInput,
                longArrayOf(
                    1,
                    ApprovedOpenWakeWordModel.MEL_HISTORY_FRAMES.toLong(),
                    ApprovedOpenWakeWordModel.MEL_BINS.toLong(),
                    1,
                ),
            ),
        )
        embeddingOutputs = linkedMapOf(
            ApprovedOpenWakeWordModel.EMBEDDING_OUTPUT_NAME to OnnxTensor.createTensor(
                environment,
                embeddingOutput,
                longArrayOf(1, 1, 1, contract.classifierFeatureSize.toLong()),
            ),
        )
        classifierInputs = linkedMapOf(
            ApprovedOpenWakeWordModel.CLASSIFIER_INPUT_NAME to OnnxTensor.createTensor(
                environment,
                classifierInput,
                longArrayOf(
                    1,
                    contract.classifierHistoryFrames.toLong(),
                    contract.classifierFeatureSize.toLong(),
                ),
            ),
        )
        classifierOutputs = linkedMapOf(
            ApprovedOpenWakeWordModel.CLASSIFIER_OUTPUT_NAME to OnnxTensor.createTensor(
                environment,
                classifierOutput,
                longArrayOf(1, 1),
            ),
        )
    }

    /**
     * The upstream v0.5.1 wrapper seeds classifier history with embeddings of
     * random PCM. For deterministic Android restarts, this port instead seeds
     * all history slots with the embedding of the wrapper's documented initial
     * all-ones 76x32 mel buffer, then suppresses the same first five outputs.
     */
    private fun initializeDeterministicFeatureHistory() {
        requireNotNull(embeddingInputBuffer).fillWith(1f)
        requireNotNull(embeddingSession).run(
            requireNotNull(embeddingInputs),
            requireNotNull(embeddingOutputs),
        ).use {
            val output = requireNotNull(embeddingOutputBuffer)
            for (index in baselineEmbedding.indices) {
                baselineEmbedding[index] = output.get(index)
            }
        }
    }

    private fun runMelspectrogram(pcm16: ShortArray): Int {
        return if (!hasPriorContext) {
            val input = requireNotNull(melFirstInputBuffer)
            OpenWakeWordPcmPreprocessor.copyRawPcm16ToFloat(
                source = pcm16,
                sourceOffset = 0,
                destination = input,
                destinationOffset = 0,
                sampleCount = contract.inferenceSamples,
            )
            requireNotNull(melSession).run(
                requireNotNull(melFirstInputs),
                requireNotNull(melFirstOutputs),
            ).use { }
            copyLatestPcmContext(pcm16)
            hasPriorContext = true
            ApprovedOpenWakeWordModel.MEL_FRAMES_FIRST_INFERENCE
        } else {
            val input = requireNotNull(melSteadyInputBuffer)
            OpenWakeWordPcmPreprocessor.copyRawPcm16ToFloat(
                source = pcmContext,
                sourceOffset = 0,
                destination = input,
                destinationOffset = 0,
                sampleCount = pcmContext.size,
            )
            OpenWakeWordPcmPreprocessor.copyRawPcm16ToFloat(
                source = pcm16,
                sourceOffset = 0,
                destination = input,
                destinationOffset = contract.melContextSamples,
                sampleCount = contract.inferenceSamples,
            )
            requireNotNull(melSession).run(
                requireNotNull(melSteadyInputs),
                requireNotNull(melSteadyOutputs),
            ).use { }
            copyLatestPcmContext(pcm16)
            ApprovedOpenWakeWordModel.MEL_FRAMES_STEADY_INFERENCE
        }
    }

    private fun appendMelFrames(frameCount: Int) {
        val valuesToAppend = frameCount * ApprovedOpenWakeWordModel.MEL_BINS
        System.arraycopy(
            melHistory,
            valuesToAppend,
            melHistory,
            0,
            melHistory.size - valuesToAppend,
        )
        val output = if (frameCount == ApprovedOpenWakeWordModel.MEL_FRAMES_FIRST_INFERENCE) {
            requireNotNull(melFirstOutputBuffer)
        } else {
            requireNotNull(melSteadyOutputBuffer)
        }
        val destinationStart = melHistory.size - valuesToAppend
        for (index in 0 until valuesToAppend) {
            melHistory[destinationStart + index] = output.get(index) / MEL_SCALE + MEL_OFFSET
        }
    }

    private fun runEmbedding() {
        val input = requireNotNull(embeddingInputBuffer)
        for (index in melHistory.indices) {
            input.put(index, melHistory[index])
        }
        requireNotNull(embeddingSession).run(
            requireNotNull(embeddingInputs),
            requireNotNull(embeddingOutputs),
        ).use { }

        val featureSize = contract.classifierFeatureSize
        System.arraycopy(featureHistory, featureSize, featureHistory, 0, featureHistory.size - featureSize)
        val output = requireNotNull(embeddingOutputBuffer)
        val destinationStart = featureHistory.size - featureSize
        for (index in 0 until featureSize) {
            featureHistory[destinationStart + index] = output.get(index)
        }
    }

    private fun runClassifier(): Float {
        val input = requireNotNull(classifierInputBuffer)
        for (index in featureHistory.indices) {
            input.put(index, featureHistory[index])
        }
        requireNotNull(classifierSession).run(
            requireNotNull(classifierInputs),
            requireNotNull(classifierOutputs),
        ).use { }
        val confidence = requireNotNull(classifierOutputBuffer).get(0)
        if (!confidence.isFinite() || confidence !in 0f..1f) {
            throw WakeWordRuntimeException(
                WakeWordEngine.ERROR_INFERENCE,
                "openWakeWord classifier returned invalid confidence $confidence.",
            )
        }
        return confidence
    }

    private fun copyLatestPcmContext(pcm16: ShortArray) {
        System.arraycopy(
            pcm16,
            contract.inferenceSamples - contract.melContextSamples,
            pcmContext,
            0,
            contract.melContextSamples,
        )
    }

    private fun resetBuffers() {
        pcmContext.fill(0)
        melHistory.fill(1f)
        for (historyIndex in 0 until contract.classifierHistoryFrames) {
            System.arraycopy(
                baselineEmbedding,
                0,
                featureHistory,
                historyIndex * contract.classifierFeatureSize,
                contract.classifierFeatureSize,
            )
        }
        melFirstInputBuffer?.fillWith(0f)
        melFirstOutputBuffer?.fillWith(0f)
        melSteadyInputBuffer?.fillWith(0f)
        melSteadyOutputBuffer?.fillWith(0f)
        embeddingInputBuffer?.fillWith(0f)
        embeddingOutputBuffer?.fillWith(0f)
        classifierInputBuffer?.fillWith(0f)
        classifierOutputBuffer?.fillWith(0f)
        hasPriorContext = false
        predictionCount = 0
        lastRawConfidence = null
    }

    private fun validateModelIdentity(artifact: WakeWordArtifactContract, bytes: ByteArray) {
        if (bytes.size.toLong() != artifact.sizeBytes) {
            throw WakeWordRuntimeException(
                WakeWordEngine.ERROR_MODEL_INVALID,
                "${artifact.fileName} size mismatch: expected=${artifact.sizeBytes}, " +
                    "actual=${bytes.size}.",
            )
        }
        val actualHash = MessageDigest.getInstance(SHA256_ALGORITHM)
            .digest(bytes)
            .toUpperHex()
        if (actualHash != artifact.sha256) {
            throw WakeWordRuntimeException(
                WakeWordEngine.ERROR_MODEL_INVALID,
                "${artifact.fileName} SHA-256 mismatch.",
            )
        }
    }

    private fun OpenWakeWordModelContract.artifactNamed(
        fileName: String,
    ): WakeWordArtifactContract = artifacts.singleOrNull { it.fileName == fileName }
        ?: throw WakeWordRuntimeException(
            WakeWordEngine.ERROR_MODEL_CONTRACT,
            "Approved openWakeWord contract is missing $fileName.",
        )

    private fun validateTensorContract(
        loadedSession: OrtSession,
        artifact: WakeWordArtifactContract,
    ) {
        validateNodes(artifact.fileName, "input", loadedSession.inputInfo, artifact.inputs)
        validateNodes(artifact.fileName, "output", loadedSession.outputInfo, artifact.outputs)
    }

    private fun validateNodes(
        fileName: String,
        direction: String,
        actualNodes: Map<String, NodeInfo>,
        expectedNodes: List<WakeWordTensorContract>,
    ) {
        val expectedNames = expectedNodes.mapTo(linkedSetOf()) { it.name }
        if (actualNodes.keys != expectedNames) {
            throw invalidContract(
                "$fileName $direction names mismatch: expected=$expectedNames, " +
                    "actual=${actualNodes.keys}.",
            )
        }
        for (expected in expectedNodes) {
            val tensorInfo = actualNodes[expected.name]?.info as? TensorInfo
                ?: throw invalidContract("$fileName ${expected.name} is not a tensor.")
            val expectedType = when (expected.type) {
                WakeWordTensorType.FLOAT32 -> OnnxJavaType.FLOAT
            }
            if (tensorInfo.type != expectedType) {
                throw invalidContract(
                    "$fileName ${expected.name} type mismatch: expected=$expectedType, " +
                        "actual=${tensorInfo.type}.",
                )
            }
            if (!tensorInfo.shape.contentEquals(expected.graphShape.toLongArray())) {
                throw invalidContract(
                    "$fileName ${expected.name} shape mismatch: " +
                        "expected=${expected.graphShape}, " +
                        "actual=${tensorInfo.shape.contentToString()}.",
                )
            }
            Log.i(
                AudioEngine.TAG,
                "openWakeWord tensor metadata: model=$fileName, direction=$direction, " +
                    "name=${expected.name}, type=${tensorInfo.type}, " +
                    "shape=${tensorInfo.shape.contentToString()}",
            )
        }
    }

    private fun invalidContract(message: String) = WakeWordRuntimeException(
        WakeWordEngine.ERROR_MODEL_CONTRACT,
        message,
    )

    private fun ensureReady() {
        if (!initialized || closed) {
            throw WakeWordRuntimeException(
                WakeWordEngine.ERROR_RUNTIME_UNAVAILABLE,
                "openWakeWord ONNX runtime is not initialized.",
            )
        }
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

        listOf(
            melFirstOutputs,
            melFirstInputs,
            melSteadyOutputs,
            melSteadyInputs,
            embeddingOutputs,
            embeddingInputs,
            classifierOutputs,
            classifierInputs,
        ).forEach { tensors ->
            tensors?.values?.forEach(::close)
            tensors?.clear()
        }
        melFirstOutputs = null
        melFirstInputs = null
        melSteadyOutputs = null
        melSteadyInputs = null
        embeddingOutputs = null
        embeddingInputs = null
        classifierOutputs = null
        classifierInputs = null
        close(classifierSession)
        close(embeddingSession)
        close(melSession)
        classifierSession = null
        embeddingSession = null
        melSession = null
        close(sessionOptions)
        sessionOptions = null
        melFirstInputBuffer = null
        melFirstOutputBuffer = null
        melSteadyInputBuffer = null
        melSteadyOutputBuffer = null
        embeddingInputBuffer = null
        embeddingOutputBuffer = null
        classifierInputBuffer = null
        classifierOutputBuffer = null
        tensorContractVerified = false
        initialized = false
        return firstFailure
    }

    private fun FloatBuffer.fillWith(value: Float) {
        for (index in 0 until capacity()) {
            put(index, value)
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

        private const val MEL_SCALE = 10f
        private const val MEL_OFFSET = 2f
        private const val SHA256_ALGORITHM = "SHA-256"
        private const val HEX_DIGITS = "0123456789ABCDEF"

        private fun allocateFloatBuffer(elements: Int): FloatBuffer =
            ByteBuffer.allocateDirect(elements * Float.SIZE_BYTES)
                .order(ByteOrder.nativeOrder())
                .asFloatBuffer()
    }
}
