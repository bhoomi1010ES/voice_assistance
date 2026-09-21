package com.voiceaipoc.vad.silero

import android.util.Log
import com.voiceaipoc.audio.AudioEngine
import com.voiceaipoc.audio.DuplexAudioFrameQueue
import com.voiceaipoc.audio.FarEndReferenceBuffer
import com.voiceaipoc.diagnostics.DiagnosticSessionContext
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * Bounded native worker that decouples Silero inference from PCM capture.
 *
 * Existing 20 ms frames are copied into a small preallocated queue. This
 * worker assembles a continuous 512-sample stream for the runtime without
 * changing AudioRecord or exposing PCM to React Native.
 */
class SileroVadEngine(
    private val config: SileroVadConfig,
    private val frameDurationMs: Int,
    private val frameSizeSamples: Int,
    private val modelAsset: SileroVadModelAsset,
    private val runtimeName: String = SELECTED_RUNTIME,
    private val runtimeVersion: String = RUNTIME_VERSION_NOT_PACKAGED,
    private val runtimeAvailable: Boolean = false,
    private val runtimeFactory: SileroVadRuntimeFactory =
        UnavailableSileroVadRuntimeFactory(SELECTED_RUNTIME),
    private val listener: Listener? = null,
    private val nanoClock: () -> Long = System::nanoTime,
    private val wallClockMs: () -> Long = System::currentTimeMillis,
    private val diagnosticSession: DiagnosticSessionContext = DiagnosticSessionContext(),
) {
    interface Listener {
        fun onEngineStarted(status: Status)
        fun onEngineStopped(status: Status)
        fun onSpeechStarted(event: Event)
        fun onSpeechStopped(event: Event)
        /** Periodic metadata while a confirmed speech segment remains active. */
        fun onSpeechActivity(event: Event) = Unit
        fun onEngineError(status: Status)
    }

    data class Event(
        val event: String,
        val timestampMs: Long,
        val probability: Float,
        val inferenceIndex: Long,
        val speechDurationMs: Long,
        val reason: String,
        val monotonicTimestampNs: Long,
        val sourceFrameSequenceStart: Long,
        val sourceFrameSequenceEnd: Long,
        val captureStartNs: Long,
        val captureEndNs: Long,
        val playbackState: String,
        val playbackActive: Boolean,
        val playbackPositionMs: Long,
        val playbackResponseId: String?,
        val referenceReady: Boolean,
        val referenceConfidence: String,
        val estimatedEchoDelayMs: Int?,
        val echoSimilarity: Double?,
        val echoLagMs: Int?,
        val echoCoherence: Double?,
        val farEndRms: Double?,
        val micRms: Double?,
        val nearEndResidualRatio: Double?,
        val echoLikely: Boolean,
        val discontinuous: Boolean,
        val nearEndFarEndEnergyRatio: Double? = null,
    )

    data class Status(
        val enabled: Boolean,
        val available: Boolean,
        val modelPresent: Boolean,
        val modelLoaded: Boolean,
        val modelName: String,
        val modelVersion: String,
        val modelGitTag: String,
        val modelGitCommit: String,
        val modelAssetPath: String,
        val modelFormat: String,
        val modelSizeBytes: Long,
        val modelSha256: String?,
        val modelSha256Verified: Boolean,
        val modelOnnxOpset: Int,
        val modelError: String?,
        val runtimeName: String,
        val runtimeVersion: String,
        val runtimeAvailable: Boolean,
        val runtimeInitialized: Boolean,
        val inferenceAvailable: Boolean,
        val sessionActive: Boolean,
        val running: Boolean,
        val workerThreadAlive: Boolean,
        val lifecycleState: String,
        val state: String,
        val speechProbabilityThreshold: Float,
        val speechStartConfirmationMs: Int,
        val speechStartConfirmationChunks: Int,
        val speechStopHangoverMs: Int,
        val speechStopConfirmationChunks: Int,
        val inputFrameDurationMs: Int,
        val inputFrameSizeSamples: Int,
        val inferenceChunkDurationMs: Int,
        val inferenceChunkSamples: Int,
        val modelContextSamples: Int,
        val queueDepthFrames: Int,
        val queueCapacityFrames: Int,
        val queueHighWaterMarkFrames: Int,
        val framesOffered: Long,
        val framesConsumed: Long,
        val droppedFrames: Long,
        val malformedFrames: Long,
        val inferenceCount: Long,
        val successfulInferenceCount: Long,
        val failedInferenceCount: Long,
        val averageInferenceDurationMs: Double,
        val maximumInferenceDurationMs: Double,
        val lastInferenceTimestampMs: Long,
        val lastInferenceMonotonicNs: Long,
        val lastObservationSourceFrameSequenceStart: Long,
        val lastObservationSourceFrameSequenceEnd: Long,
        val lastObservationCaptureStartNs: Long,
        val lastObservationCaptureEndNs: Long,
        val lastObservationDiscontinuous: Boolean,
        val discontinuityCount: Long,
        val discontinuityPending: Boolean,
        val currentProbability: Float?,
        val speechStartCount: Long,
        val speechStopCount: Long,
        val resetCount: Long,
        val errorCount: Long,
        val lastErrorCode: String?,
        val lastErrorMessage: String?,
    )

    data class StartResult(
        val succeeded: Boolean,
        val errorCode: String? = null,
        val errorMessage: String? = null,
    )

    companion object {
        const val EVENT_SPEECH_STARTED = "SILERO_VAD_SPEECH_STARTED"
        const val EVENT_SPEECH_STOPPED = "SILERO_VAD_SPEECH_STOPPED"
        const val EVENT_SPEECH_ACTIVITY = "SILERO_VAD_SPEECH_ACTIVITY"
        const val EVENT_ERROR = "SILERO_VAD_ERROR"

        const val SELECTED_RUNTIME = "ONNX_RUNTIME_ANDROID_CPU"
        const val RUNTIME_VERSION_NOT_PACKAGED = "NOT_PACKAGED"
        const val ERROR_MODEL_MISSING = "E_SILERO_MODEL_MISSING"
        const val ERROR_MODEL_INVALID = "E_SILERO_MODEL_INVALID"
        const val ERROR_MODEL_CONTRACT = "E_SILERO_MODEL_CONTRACT"
        const val ERROR_RUNTIME_UNAVAILABLE = "E_SILERO_RUNTIME_UNAVAILABLE"
        const val ERROR_ALREADY_RUNNING = "E_SILERO_ALREADY_RUNNING"
        const val ERROR_WORKER_START = "E_SILERO_WORKER_START"
        const val ERROR_RUNTIME_INITIALIZATION = "E_SILERO_RUNTIME_INITIALIZATION"
        const val ERROR_RUNTIME_INITIALIZATION_TIMEOUT =
            "E_SILERO_RUNTIME_INITIALIZATION_TIMEOUT"
        const val ERROR_INFERENCE = "E_SILERO_INFERENCE"
        const val ERROR_WORKER_FAILURE = "E_SILERO_WORKER_FAILURE"
        const val ERROR_MALFORMED_PCM = "E_SILERO_MALFORMED_PCM"
        const val ERROR_RUNTIME_RESET = "E_SILERO_RUNTIME_RESET"
        const val ERROR_RUNTIME_RELEASE = "E_SILERO_RUNTIME_RELEASE"
        const val ERROR_WORKER_STOP = "E_SILERO_WORKER_STOP"

        private const val WORKER_THREAD_NAME = "VoiceAI-SileroVAD"
        private const val QUEUE_WAIT_TIMEOUT_MS = 50L
        private const val OVERFLOW_LOG_INTERVAL = 100L
        private const val MALFORMED_LOG_INTERVAL = 100L
        private const val ACTIVITY_EVENT_INTERVAL_INFERENCES = 5L
        private const val NANOS_PER_MILLISECOND = 1_000_000.0
        private const val NANOS_PER_MILLISECOND_LONG = 1_000_000L
    }

    private val lock = Any()
    private val stateMachine = SileroVadStateMachine(config, wallClockMs)
    private val frameQueue = DuplexAudioFrameQueue(
        capacityFrames = config.queueCapacityFrames,
        frameSizeSamples = frameSizeSamples,
    )
    private val workerFrame = ShortArray(frameSizeSamples)
    private val workerMetadata = DuplexAudioFrameQueue.FrameMetadata()
    private val inferenceChunk = ShortArray(config.inferenceChunkSamples)
    private val inferenceMetadata = InferenceMetadataAccumulator()

    @Volatile
    private var running = false
    private var sessionActive = false
    private var runtimeInitialized = false
    private var modelLoaded = false
    private var workerThread: Thread? = null
    private var startupLatch: CountDownLatch? = null
    private var lifecycleState = "IDLE"
    private var pendingInferenceSamples = 0
    private var observationDiscontinuous = false
    private var discontinuityPending = false
    private var discontinuityCount = 0L
    private var legacyFrameSequence = 0L
    private var framesOffered = 0L
    private var framesConsumed = 0L
    private var droppedFrames = 0L
    private var malformedFrames = 0L
    private var queueHighWaterMarkFrames = 0
    private var inferenceCount = 0L
    private var successfulInferenceCount = 0L
    private var failedInferenceCount = 0L
    private var totalInferenceDurationNanos = 0L
    private var maximumInferenceDurationNanos = 0L
    private var lastInferenceTimestampMs = 0L
    private var lastInferenceMonotonicNs = 0L
    private var lastObservation: SileroInferenceObservation? = null
    private var resetCount = 0L
    private var errorCount = 0L
    private var lastErrorCode: String? = null
    private var lastErrorMessage: String? = null

    init {
        require(frameDurationMs > 0) { "frameDurationMs must be positive" }
        require(frameSizeSamples > 0) { "frameSizeSamples must be positive" }
        require(config.sampleRateHz * frameDurationMs / 1_000 == frameSizeSamples) {
            "Silero input must match the existing mono PCM frame size"
        }
    }

    /** Starts one worker, or reports explicit model/runtime unavailability. */
    fun startSession(): StartResult {
        var error: Pair<String, String>? = null
        var startException: RuntimeException? = null
        var readyLatch: CountDownLatch? = null
        synchronized(lock) {
            if (sessionActive || running || workerThread?.isAlive == true) {
                val message = "Silero VAD session is already active."
                recordErrorLocked(ERROR_ALREADY_RUNNING, message, fatal = false)
                error = ERROR_ALREADY_RUNNING to message
            } else {
                resetForNewSessionLocked()
                sessionActive = true
                lifecycleState = "STARTING"
                error = when {
                    !config.enabled -> {
                        lifecycleState = "DISABLED"
                        "E_SILERO_DISABLED" to "Silero VAD is disabled by configuration."
                    }
                    !modelAsset.present -> {
                        val message = "Approved Silero VAD model is missing at " +
                            "${modelAsset.assetPath}."
                        recordErrorLocked(ERROR_MODEL_MISSING, message, fatal = true)
                        ERROR_MODEL_MISSING to message
                    }
                    !modelAsset.sha256Verified -> {
                        val message = modelAsset.missingReason
                            ?: "Approved Silero VAD model integrity validation failed."
                        recordErrorLocked(ERROR_MODEL_INVALID, message, fatal = true)
                        ERROR_MODEL_INVALID to message
                    }
                    !runtimeAvailable -> {
                        val message = "$runtimeName is selected but not packaged for the " +
                            "approved Silero model contract."
                        recordErrorLocked(ERROR_RUNTIME_UNAVAILABLE, message, fatal = true)
                        ERROR_RUNTIME_UNAVAILABLE to message
                    }
                    else -> null
                }

                if (error == null) {
                    running = true
                    val latch = CountDownLatch(1)
                    startupLatch = latch
                    readyLatch = latch
                    val newWorker = Thread(::workerLoop, WORKER_THREAD_NAME)
                    workerThread = newWorker
                    try {
                        newWorker.start()
                    } catch (exception: RuntimeException) {
                        running = false
                        workerThread = null
                        startupLatch = null
                        latch.countDown()
                        val message = "Silero VAD worker could not start: ${exception.message}"
                        recordErrorLocked(ERROR_WORKER_START, message, fatal = true)
                        error = ERROR_WORKER_START to message
                        startException = exception
                    }
                }
            }
        }

        if (error != null) {
            val (code, message) = error!!
            Log.w(AudioEngine.TAG, "$code: $message", startException)
            listener?.onEngineError(getStatus())
            return StartResult(false, code, message)
        }

        Log.i(
            AudioEngine.TAG,
            diagnosticSession.tag("Silero VAD worker starting: runtime=$runtimeName, " +
                "model=${config.modelAssetPath}, chunkSamples=${config.inferenceChunkSamples}, " +
                "threshold=${config.speechProbabilityThreshold}"),
        )
        val runtimeReady = try {
            requireNotNull(readyLatch).await(
                config.runtimeInitializationTimeoutMs,
                TimeUnit.MILLISECONDS,
            )
        } catch (interrupted: InterruptedException) {
            Thread.currentThread().interrupt()
            false
        }
        synchronized(lock) {
            if (startupLatch === readyLatch) {
                startupLatch = null
            }
        }
        if (!runtimeReady) {
            val message = "Silero runtime did not initialize within " +
                "${config.runtimeInitializationTimeoutMs} ms."
            synchronized(lock) {
                running = false
                recordErrorLocked(ERROR_RUNTIME_INITIALIZATION_TIMEOUT, message, fatal = true)
            }
            workerThread?.interrupt()
            Log.e(AudioEngine.TAG, "$ERROR_RUNTIME_INITIALIZATION_TIMEOUT: $message")
            listener?.onEngineError(getStatus())
            return StartResult(false, ERROR_RUNTIME_INITIALIZATION_TIMEOUT, message)
        }

        val readyStatus = getStatus()
        if (!readyStatus.runtimeInitialized) {
            return StartResult(
                succeeded = false,
                errorCode = readyStatus.lastErrorCode ?: ERROR_RUNTIME_INITIALIZATION,
                errorMessage = readyStatus.lastErrorMessage
                    ?: "Silero runtime initialization failed.",
            )
        }
        return StartResult(true)
    }

    /** Compatibility producer call for tests and legacy native callers. */
    fun offerPcmFrame(pcmSamples: ShortArray, samplesRead: Int): Boolean {
        val frameSequence = synchronized(lock) { legacyFrameSequence++ }
        val captureEndNs = nanoClock()
        val captureStartNs = captureEndNs -
            frameDurationMs.toLong() * NANOS_PER_MILLISECOND_LONG
        return offerPcmFrame(
            pcmSamples = pcmSamples,
            samplesRead = samplesRead,
            frameSequence = frameSequence,
            captureStartNs = captureStartNs,
            captureEndNs = captureEndNs,
            assessment = FarEndReferenceBuffer.Assessment.stopped(captureEndNs),
        )
    }

    /** Non-blocking producer call carrying the exact capture frame metadata. */
    fun offerPcmFrame(
        pcmSamples: ShortArray,
        samplesRead: Int,
        frameSequence: Long,
        captureStartNs: Long,
        captureEndNs: Long,
        assessment: FarEndReferenceBuffer.Assessment,
    ): Boolean {
        if (!running) {
            return false
        }
        if (
            samplesRead != frameSizeSamples ||
            samplesRead > pcmSamples.size ||
            frameSequence < 0L ||
            captureStartNs > captureEndNs
        ) {
            val count = synchronized(lock) {
                malformedFrames += 1L
                recordErrorLocked(
                    ERROR_MALFORMED_PCM,
                    "Malformed Silero PCM frame: samplesRead=$samplesRead, " +
                        "expected=$frameSizeSamples.",
                    fatal = false,
                )
                malformedFrames
            }
            if (count == 1L || count % MALFORMED_LOG_INTERVAL == 0L) {
                Log.e(AudioEngine.TAG, "$ERROR_MALFORMED_PCM: count=$count")
            }
            if (count == 1L) {
                listener?.onEngineError(getStatus())
            }
            return false
        }

        val result = frameQueue.offer(
            source = pcmSamples,
            sampleCount = samplesRead,
            frameSequence = frameSequence,
            captureStartNs = captureStartNs,
            captureEndNs = captureEndNs,
            assessment = assessment,
        )
        val depth = frameQueue.currentBufferedFrames()
        val overflowCount = synchronized(lock) {
            framesOffered += 1L
            queueHighWaterMarkFrames = max(queueHighWaterMarkFrames, depth)
            if (result == DuplexAudioFrameQueue.WriteResult.WROTE_AFTER_DROPPING_OLDEST) {
                droppedFrames += 1L
                discontinuityPending = true
                discontinuityCount += 1L
            }
            droppedFrames
        }
        if (
            result == DuplexAudioFrameQueue.WriteResult.WROTE_AFTER_DROPPING_OLDEST &&
            (overflowCount == 1L || overflowCount % OVERFLOW_LOG_INTERVAL == 0L)
        ) {
            Log.w(
                AudioEngine.TAG,
                "Silero queue overflow: count=$overflowCount, policy=DROP_OLDEST",
            )
        }
        return true
    }

    /** Stops, joins, resets, and clears all session-owned state. */
    fun stopSession() {
        val thread: Thread?
        val wasActive: Boolean
        synchronized(lock) {
            wasActive = sessionActive || running || workerThread != null
            sessionActive = false
            running = false
            lifecycleState = "STOPPING"
            thread = workerThread
            startupLatch?.countDown()
        }

        thread?.interrupt()
        if (thread != null && thread !== Thread.currentThread()) {
            try {
                thread.join(config.workerJoinTimeoutMs)
            } catch (interrupted: InterruptedException) {
                Thread.currentThread().interrupt()
                recordNonFatalError(ERROR_WORKER_STOP, "Interrupted while stopping Silero worker.")
            }
        }

        val stillAlive = thread?.isAlive == true
        val stopObservation = synchronized(lock) { lastObservation }
        val transition = stateMachine.stop(
            inferenceIndex = synchronized(lock) { inferenceCount },
            captureStartNs = stopObservation?.captureStartNs ?: 0L,
            captureEndNs = stopObservation?.captureEndNs ?: 0L,
        )
        synchronized(lock) {
            if (stillAlive) {
                recordErrorLocked(
                    ERROR_WORKER_STOP,
                    "Silero worker did not stop within ${config.workerJoinTimeoutMs} ms.",
                    fatal = true,
                )
            } else {
                if (workerThread === thread) {
                    workerThread = null
                }
                runtimeInitialized = false
                modelLoaded = false
                lifecycleState = "STOPPED"
            }
            pendingInferenceSamples = 0
            inferenceMetadata.clear()
            observationDiscontinuous = false
            discontinuityPending = false
            workerFrame.fill(0)
            workerMetadata.clear()
            inferenceChunk.fill(0)
            resetCount += 1L
        }
        frameQueue.clear()

        transition?.let { emitTransition(it, stopObservation) }
        if (stillAlive) {
            listener?.onEngineError(getStatus())
        }
        if (wasActive) {
            val status = getStatus()
            Log.i(
                AudioEngine.TAG,
                diagnosticSession.tag("Silero VAD worker stopped: inference=${status.inferenceCount}, " +
                    "successful=${status.successfulInferenceCount}, " +
                    "failed=${status.failedInferenceCount}, dropped=${status.droppedFrames}"),
            )
            listener?.onEngineStopped(status)
        }
    }

    fun getStatus(): Status {
        val vadStatus = stateMachine.getStatus()
        val queueDepth = frameQueue.currentBufferedFrames()
        return synchronized(lock) {
            val averageMs = if (inferenceCount == 0L) {
                0.0
            } else {
                totalInferenceDurationNanos.toDouble() /
                    inferenceCount.toDouble() / NANOS_PER_MILLISECOND
            }
            Status(
                enabled = config.enabled,
                available = config.enabled && modelAsset.present &&
                    modelAsset.sha256Verified && runtimeAvailable,
                modelPresent = modelAsset.present,
                modelLoaded = modelLoaded,
                modelName = config.modelFileName,
                modelVersion = ApprovedSileroVadModel.VERSION,
                modelGitTag = ApprovedSileroVadModel.GIT_TAG,
                modelGitCommit = ApprovedSileroVadModel.GIT_COMMIT,
                modelAssetPath = modelAsset.assetPath,
                modelFormat = config.modelFormat,
                modelSizeBytes = modelAsset.sizeBytes,
                modelSha256 = modelAsset.sha256,
                modelSha256Verified = modelAsset.sha256Verified,
                modelOnnxOpset = ApprovedSileroVadModel.ONNX_OPSET,
                modelError = modelAsset.missingReason,
                runtimeName = runtimeName,
                runtimeVersion = runtimeVersion,
                runtimeAvailable = runtimeAvailable,
                runtimeInitialized = runtimeInitialized,
                inferenceAvailable = running && runtimeInitialized,
                sessionActive = sessionActive,
                running = running,
                workerThreadAlive = workerThread?.isAlive == true,
                lifecycleState = lifecycleState,
                state = vadStatus.state.name,
                speechProbabilityThreshold = config.speechProbabilityThreshold,
                speechStartConfirmationMs = config.speechStartConfirmationMs,
                speechStartConfirmationChunks = config.speechStartConfirmationChunks,
                speechStopHangoverMs = config.speechStopHangoverMs,
                speechStopConfirmationChunks = config.speechStopConfirmationChunks,
                inputFrameDurationMs = frameDurationMs,
                inputFrameSizeSamples = frameSizeSamples,
                inferenceChunkDurationMs = config.inferenceChunkDurationMs,
                inferenceChunkSamples = config.inferenceChunkSamples,
                modelContextSamples = config.modelContextSamples,
                queueDepthFrames = queueDepth,
                queueCapacityFrames = frameQueue.capacityFrames,
                queueHighWaterMarkFrames = queueHighWaterMarkFrames,
                framesOffered = framesOffered,
                framesConsumed = framesConsumed,
                droppedFrames = droppedFrames,
                malformedFrames = malformedFrames,
                inferenceCount = inferenceCount,
                successfulInferenceCount = successfulInferenceCount,
                failedInferenceCount = failedInferenceCount,
                averageInferenceDurationMs = averageMs,
                maximumInferenceDurationMs =
                maximumInferenceDurationNanos.toDouble() / NANOS_PER_MILLISECOND,
                lastInferenceTimestampMs = lastInferenceTimestampMs,
                lastInferenceMonotonicNs = lastInferenceMonotonicNs,
                lastObservationSourceFrameSequenceStart = lastObservation
                    ?.sourceFrameSequenceStart ?: 0L,
                lastObservationSourceFrameSequenceEnd = lastObservation
                    ?.sourceFrameSequenceEnd ?: 0L,
                lastObservationCaptureStartNs = lastObservation?.captureStartNs ?: 0L,
                lastObservationCaptureEndNs = lastObservation?.captureEndNs ?: 0L,
                lastObservationDiscontinuous = lastObservation?.discontinuous == true,
                discontinuityCount = discontinuityCount,
                discontinuityPending = discontinuityPending,
                currentProbability = vadStatus.lastProbability,
                speechStartCount = vadStatus.speechStartCount,
                speechStopCount = vadStatus.speechStopCount,
                resetCount = resetCount,
                errorCount = errorCount,
                lastErrorCode = lastErrorCode,
                lastErrorMessage = lastErrorMessage,
            )
        }
    }

    private fun workerLoop() {
        var runtime: SileroVadRuntime? = null
        try {
            runtime = runtimeFactory.create()
            try {
                runtime.initialize()
                runtime.reset()
            } catch (exception: SileroVadRuntimeException) {
                throw exception
            } catch (exception: RuntimeException) {
                throw SileroVadRuntimeException(
                    ERROR_RUNTIME_INITIALIZATION,
                    "Silero runtime initialization failed: ${exception.message}",
                    exception,
                )
            }

            synchronized(lock) {
                if (!running) {
                    return
                }
                runtimeInitialized = true
                modelLoaded = true
                lifecycleState = "RUNNING"
                startupLatch?.countDown()
            }
            Log.i(
                AudioEngine.TAG,
                diagnosticSession.tag(
                    "Silero VAD engine started: runtime=${runtime.runtimeName} " +
                        "${runtime.runtimeVersion}",
                ),
            )
            listener?.onEngineStarted(getStatus())

            while (running) {
                val samplesRead = try {
                    frameQueue.read(
                        destination = workerFrame,
                        metadata = workerMetadata,
                        waitTimeoutMs = QUEUE_WAIT_TIMEOUT_MS,
                    )
                } catch (interrupted: InterruptedException) {
                    if (running) {
                        throw SileroVadRuntimeException(
                            ERROR_WORKER_FAILURE,
                            "Silero worker was interrupted unexpectedly.",
                            interrupted,
                        )
                    }
                    break
                }
                if (!running || samplesRead == 0) {
                    continue
                }
                if (workerMetadata.discontinuous || consumeDiscontinuityPending()) {
                    runtime.reset()
                    pendingInferenceSamples = 0
                    inferenceMetadata.clear()
                    observationDiscontinuous = true
                }
                synchronized(lock) { framesConsumed += 1L }
                appendFrameAndInfer(runtime, samplesRead, workerMetadata)
            }
        } catch (exception: Throwable) {
            if (running) {
                val code = (exception as? SileroVadRuntimeException)?.errorCode
                    ?: ERROR_WORKER_FAILURE
                val message = exception.message ?: "Unknown Silero VAD worker failure."
                synchronized(lock) {
                    running = false
                    recordErrorLocked(code, message, fatal = true)
                    startupLatch?.countDown()
                }
                stateMachine.stop(synchronized(lock) { inferenceCount })
                Log.e(AudioEngine.TAG, "$code: $message", exception)
                listener?.onEngineError(getStatus())
            }
        } finally {
            synchronized(lock) { startupLatch?.countDown() }
            releaseRuntime(runtime)
            synchronized(lock) {
                runtimeInitialized = false
                modelLoaded = false
                if (workerThread === Thread.currentThread()) {
                    workerThread = null
                }
                if (!sessionActive && lifecycleState != "ERROR") {
                    lifecycleState = "STOPPED"
                }
            }
        }
    }

    private fun appendFrameAndInfer(
        runtime: SileroVadRuntime,
        samplesRead: Int,
        metadata: DuplexAudioFrameQueue.FrameMetadata,
    ) {
        var sourceOffset = 0
        while (sourceOffset < samplesRead && running) {
            val samplesToCopy = minOf(
                config.inferenceChunkSamples - pendingInferenceSamples,
                samplesRead - sourceOffset,
            )
            System.arraycopy(
                workerFrame,
                sourceOffset,
                inferenceChunk,
                pendingInferenceSamples,
                samplesToCopy,
            )
            inferenceMetadata.append(
                metadata = metadata,
                sourceOffset = sourceOffset,
                sampleCount = samplesToCopy,
                frameSampleCount = samplesRead,
            )
            pendingInferenceSamples += samplesToCopy
            sourceOffset += samplesToCopy

            if (pendingInferenceSamples == config.inferenceChunkSamples) {
                runInference(runtime)
                pendingInferenceSamples = 0
                inferenceMetadata.clear()
                observationDiscontinuous = false
            }
        }
    }

    private fun runInference(runtime: SileroVadRuntime) {
        val inferenceIndex = synchronized(lock) {
            inferenceCount += 1L
            inferenceCount
        }
        val startedNanos = nanoClock()
        var successful = false
        try {
            val probability = runtime.infer(inferenceChunk, config.inferenceChunkSamples)
            if (!probability.isFinite() || probability !in 0f..1f) {
                throw SileroVadRuntimeException(
                    ERROR_INFERENCE,
                    "Silero runtime returned invalid probability $probability.",
                )
            }
            val observation = inferenceMetadata.toObservation(
                inferenceIndex = inferenceIndex,
                probability = probability,
                discontinuous = observationDiscontinuous,
            )
            val transition = stateMachine.onProbability(
                probability = probability,
                inferenceIndex = inferenceIndex,
                captureStartNs = observation.captureStartNs,
                captureEndNs = observation.captureEndNs,
            )
            val state = stateMachine.getStatus().state
            synchronized(lock) {
                successfulInferenceCount += 1L
                lastObservation = observation
                lastInferenceMonotonicNs = observation.monotonicTimestampNs
            }
            successful = true
            transition?.let { emitTransition(it, observation) }
            if (
                (state == SileroVadStateMachine.State.SPEECH ||
                    state == SileroVadStateMachine.State.SPEECH_STOP_PENDING) &&
                inferenceIndex % ACTIVITY_EVENT_INTERVAL_INFERENCES == 0L
            ) {
                emitSpeechActivity(
                    observation = observation,
                    speechDurationMs = stateMachine.currentSpeechDurationMs(
                        inferenceIndex,
                        observation.captureEndNs,
                    ),
                )
            }
        } catch (exception: SileroVadRuntimeException) {
            throw exception
        } catch (exception: RuntimeException) {
            throw SileroVadRuntimeException(
                ERROR_INFERENCE,
                "Silero inference failed: ${exception.message}",
                exception,
            )
        } finally {
            val elapsedNanos = (nanoClock() - startedNanos).coerceAtLeast(0L)
            synchronized(lock) {
                totalInferenceDurationNanos += elapsedNanos
                maximumInferenceDurationNanos = max(maximumInferenceDurationNanos, elapsedNanos)
                lastInferenceTimestampMs = wallClockMs()
                if (!successful) {
                    failedInferenceCount += 1L
                }
            }
        }
    }

    private fun emitTransition(
        transition: SileroVadStateMachine.Transition,
        observation: SileroInferenceObservation?,
    ) {
        val source = observation ?: lastObservation ?: emptyObservation(transition)
        val event = Event(
            event = transition.event,
            timestampMs = transition.timestampMs,
            probability = transition.probability,
            inferenceIndex = transition.inferenceIndex,
            speechDurationMs = transition.speechDurationMs,
            reason = transition.reason,
            monotonicTimestampNs = transition.monotonicTimestampNs
                .takeIf { it != 0L } ?: source.monotonicTimestampNs,
            sourceFrameSequenceStart = source.sourceFrameSequenceStart,
            sourceFrameSequenceEnd = source.sourceFrameSequenceEnd,
            captureStartNs = source.captureStartNs,
            captureEndNs = source.captureEndNs,
            playbackState = source.playbackState,
            playbackActive = source.playbackActive,
            playbackPositionMs = source.playbackPositionMs,
            playbackResponseId = source.playbackResponseId,
            referenceReady = source.referenceReady,
            referenceConfidence = source.referenceConfidence,
            estimatedEchoDelayMs = source.estimatedEchoDelayMs,
            echoSimilarity = source.echoSimilarity,
            echoLagMs = source.echoLagMs,
            echoCoherence = source.echoCoherence,
            farEndRms = source.farEndRms,
            micRms = source.micRms,
            nearEndResidualRatio = source.nearEndResidualRatio,
            echoLikely = source.echoLikely,
            discontinuous = source.discontinuous,
            nearEndFarEndEnergyRatio = source.nearEndFarEndEnergyRatio,
        )
        when (event.event) {
            EVENT_SPEECH_STARTED -> {
                Log.i(
                    AudioEngine.TAG,
                    diagnosticSession.tag(
                        "SILERO_VAD_SPEECH_STARTED probability=${event.probability} " +
                            "inference=${event.inferenceIndex} " +
                            "frames=${event.sourceFrameSequenceStart}-" +
                            "${event.sourceFrameSequenceEnd} " +
                            "capture=${event.captureStartNs}-${event.captureEndNs} " +
                            "discontinuous=${event.discontinuous}",
                    ),
                )
                listener?.onSpeechStarted(event)
            }
            EVENT_SPEECH_STOPPED -> {
                Log.i(
                    AudioEngine.TAG,
                    diagnosticSession.tag(
                        "SILERO_VAD_SPEECH_STOPPED durationMs=${event.speechDurationMs} " +
                            "inference=${event.inferenceIndex} " +
                            "frames=${event.sourceFrameSequenceStart}-" +
                            "${event.sourceFrameSequenceEnd} " +
                            "capture=${event.captureStartNs}-${event.captureEndNs} " +
                            "discontinuous=${event.discontinuous}",
                    ),
                )
                listener?.onSpeechStopped(event)
            }
        }
    }

    private fun emitSpeechActivity(
        observation: SileroInferenceObservation,
        speechDurationMs: Long,
    ) {
        val event = Event(
            event = EVENT_SPEECH_ACTIVITY,
            timestampMs = wallClockMs(),
            probability = observation.probability,
            inferenceIndex = observation.inferenceIndex,
            speechDurationMs = speechDurationMs,
            reason = "SPEECH_CONTINUING",
            monotonicTimestampNs = observation.monotonicTimestampNs,
            sourceFrameSequenceStart = observation.sourceFrameSequenceStart,
            sourceFrameSequenceEnd = observation.sourceFrameSequenceEnd,
            captureStartNs = observation.captureStartNs,
            captureEndNs = observation.captureEndNs,
            playbackState = observation.playbackState,
            playbackActive = observation.playbackActive,
            playbackPositionMs = observation.playbackPositionMs,
            playbackResponseId = observation.playbackResponseId,
            referenceReady = observation.referenceReady,
            referenceConfidence = observation.referenceConfidence,
            estimatedEchoDelayMs = observation.estimatedEchoDelayMs,
            echoSimilarity = observation.echoSimilarity,
            echoLagMs = observation.echoLagMs,
            echoCoherence = observation.echoCoherence,
            farEndRms = observation.farEndRms,
            micRms = observation.micRms,
            nearEndResidualRatio = observation.nearEndResidualRatio,
            echoLikely = observation.echoLikely,
            discontinuous = observation.discontinuous,
            nearEndFarEndEnergyRatio = observation.nearEndFarEndEnergyRatio,
        )
        Log.i(
            AudioEngine.TAG,
            diagnosticSession.tag(
                "SILERO_VAD_SPEECH_ACTIVITY probability=${event.probability} " +
                    "durationMs=${event.speechDurationMs} inference=${event.inferenceIndex} " +
                    "frames=${event.sourceFrameSequenceStart}-${event.sourceFrameSequenceEnd}",
            ),
        )
        listener?.onSpeechActivity(event)
    }

    private fun releaseRuntime(runtime: SileroVadRuntime?) {
        if (runtime == null) {
            return
        }
        try {
            runtime.reset()
        } catch (exception: RuntimeException) {
            recordNonFatalError(
                ERROR_RUNTIME_RESET,
                "Silero runtime reset failed: ${exception.message}",
            )
        }
        try {
            runtime.close()
        } catch (exception: RuntimeException) {
            recordNonFatalError(
                ERROR_RUNTIME_RELEASE,
                "Silero runtime release failed: ${exception.message}",
            )
        }
    }

    private fun consumeDiscontinuityPending(): Boolean = synchronized(lock) {
        if (!discontinuityPending) {
            false
        } else {
            discontinuityPending = false
            true
        }
    }

    private fun emptyObservation(
        transition: SileroVadStateMachine.Transition,
    ): SileroInferenceObservation = SileroInferenceObservation(
        inferenceIndex = transition.inferenceIndex,
        sourceFrameSequenceStart = 0L,
        sourceFrameSequenceEnd = 0L,
        captureStartNs = transition.captureStartNs,
        captureEndNs = transition.captureEndNs,
        probability = transition.probability,
        playbackState = FarEndReferenceBuffer.State.STOPPED.name,
        playbackActive = false,
        playbackPositionMs = 0L,
        playbackResponseId = null,
        referenceReady = false,
        referenceConfidence = "NONE",
        estimatedEchoDelayMs = null,
        echoSimilarity = null,
        echoLagMs = null,
        echoCoherence = null,
        farEndRms = null,
        micRms = null,
        nearEndResidualRatio = null,
        echoLikely = false,
        discontinuous = true,
        monotonicTimestampNs = transition.monotonicTimestampNs,
    )

    private fun resetForNewSessionLocked() {
        frameQueue.clear()
        workerFrame.fill(0)
        workerMetadata.clear()
        inferenceChunk.fill(0)
        inferenceMetadata.clear()
        pendingInferenceSamples = 0
        observationDiscontinuous = false
        discontinuityPending = false
        discontinuityCount = 0L
        legacyFrameSequence = 0L
        framesOffered = 0L
        framesConsumed = 0L
        droppedFrames = 0L
        malformedFrames = 0L
        queueHighWaterMarkFrames = 0
        inferenceCount = 0L
        successfulInferenceCount = 0L
        failedInferenceCount = 0L
        totalInferenceDurationNanos = 0L
        maximumInferenceDurationNanos = 0L
        lastInferenceTimestampMs = 0L
        lastInferenceMonotonicNs = 0L
        lastObservation = null
        errorCount = 0L
        lastErrorCode = null
        lastErrorMessage = null
        runtimeInitialized = false
        modelLoaded = false
        startupLatch = null
        stateMachine.reset()
        resetCount += 1L
    }

    private class InferenceMetadataAccumulator {
        private var sampleCount = 0
        private var sourceFrameSequenceStart = 0L
        private var sourceFrameSequenceEnd = 0L
        private var captureStartNs = 0L
        private var captureEndNs = 0L
        private var playbackState = FarEndReferenceBuffer.State.STOPPED.name
        private var playbackStateMixed = false
        private var playbackActive = true
        private var playbackActiveInitialized = false
        private var playbackPositionSum = 0.0
        private var playbackPositionWeight = 0L
        private var responseId: String? = null
        private var responseIdMixed = false
        private var referenceReady = true
        private var referenceInitialized = false
        private var referenceConfidence = "NONE"
        private var referenceConfidenceMixed = false
        private var estimatedEchoDelaySum = 0.0
        private var estimatedEchoDelayWeight = 0L
        private var echoSimilaritySum = 0.0
        private var echoSimilarityWeight = 0L
        private var echoLagSum = 0.0
        private var echoLagWeight = 0L
        private var echoCoherenceSum = 0.0
        private var echoCoherenceWeight = 0L
        private var farEndRmsSum = 0.0
        private var farEndRmsWeight = 0L
        private var micRmsSum = 0.0
        private var micRmsWeight = 0L
        private var nearEndResidualSum = 0.0
        private var nearEndResidualWeight = 0L
        private var nearEndFarEndEnergyRatioSum = 0.0
        private var nearEndFarEndEnergyRatioWeight = 0L
        private var echoLikely = false

        fun append(
            metadata: DuplexAudioFrameQueue.FrameMetadata,
            sourceOffset: Int,
            sampleCount: Int,
            frameSampleCount: Int,
        ) {
            require(sampleCount > 0) { "sampleCount must be positive" }
            val durationNs = metadata.captureEndNs - metadata.captureStartNs
            val portionStartNs = metadata.captureStartNs +
                durationNs * sourceOffset.toLong() / frameSampleCount.toLong()
            val portionEndNs = metadata.captureStartNs +
                durationNs * (sourceOffset + sampleCount).toLong() / frameSampleCount.toLong()

            if (this.sampleCount == 0) {
                sourceFrameSequenceStart = metadata.frameSequence
                captureStartNs = portionStartNs
                playbackState = metadata.playbackState.name
                responseId = metadata.playbackResponseId
                playbackActive = metadata.playbackActive
                playbackActiveInitialized = true
                referenceReady = metadata.referenceReady
                referenceInitialized = true
                referenceConfidence = metadata.referenceConfidence
            } else {
                playbackStateMixed = playbackStateMixed ||
                    playbackState != metadata.playbackState.name
                responseIdMixed = responseIdMixed ||
                    responseId != metadata.playbackResponseId
                referenceConfidenceMixed = referenceConfidenceMixed ||
                    referenceConfidence != metadata.referenceConfidence
                playbackActive = playbackActive && metadata.playbackActive
                referenceReady = referenceReady && metadata.referenceReady
            }

            sourceFrameSequenceEnd = metadata.frameSequence
            captureEndNs = portionEndNs
            this.sampleCount += sampleCount
            echoLikely = echoLikely || metadata.echoLikely
            addWeighted(metadata.playbackPositionMs.toDouble(), sampleCount) { sum, weight ->
                playbackPositionSum += sum
                playbackPositionWeight += weight
            }
            addWeighted(metadata.estimatedEchoDelayMs?.toDouble(), sampleCount) { sum, weight ->
                estimatedEchoDelaySum += sum
                estimatedEchoDelayWeight += weight
            }
            addWeighted(metadata.echoSimilarity, sampleCount) { sum, weight ->
                echoSimilaritySum += sum
                echoSimilarityWeight += weight
            }
            addWeighted(metadata.echoLagMs?.toDouble(), sampleCount) { sum, weight ->
                echoLagSum += sum
                echoLagWeight += weight
            }
            addWeighted(metadata.echoCoherence, sampleCount) { sum, weight ->
                echoCoherenceSum += sum
                echoCoherenceWeight += weight
            }
            addWeighted(metadata.farEndRms, sampleCount) { sum, weight ->
                farEndRmsSum += sum
                farEndRmsWeight += weight
            }
            addWeighted(metadata.micRms, sampleCount) { sum, weight ->
                micRmsSum += sum
                micRmsWeight += weight
            }
            addWeighted(metadata.nearEndResidualRatio, sampleCount) { sum, weight ->
                nearEndResidualSum += sum
                nearEndResidualWeight += weight
            }
            addWeighted(metadata.nearEndFarEndEnergyRatio, sampleCount) { sum, weight ->
                nearEndFarEndEnergyRatioSum += sum
                nearEndFarEndEnergyRatioWeight += weight
            }
        }

        fun toObservation(
            inferenceIndex: Long,
            probability: Float,
            discontinuous: Boolean,
        ): SileroInferenceObservation {
            check(sampleCount > 0) { "An inference observation requires source samples" }
            return SileroInferenceObservation(
                inferenceIndex = inferenceIndex,
                sourceFrameSequenceStart = sourceFrameSequenceStart,
                sourceFrameSequenceEnd = sourceFrameSequenceEnd,
                captureStartNs = captureStartNs,
                captureEndNs = captureEndNs,
                probability = probability,
                playbackState = if (playbackStateMixed) "MIXED" else playbackState,
                playbackActive = playbackActiveInitialized && playbackActive,
                playbackPositionMs = average(playbackPositionSum, playbackPositionWeight)
                    ?.roundToInt()?.toLong() ?: 0L,
                playbackResponseId = if (responseIdMixed) null else responseId,
                referenceReady = referenceInitialized && referenceReady,
                referenceConfidence = if (referenceConfidenceMixed) {
                    "MIXED"
                } else {
                    referenceConfidence
                },
                estimatedEchoDelayMs = averageInt(
                    estimatedEchoDelaySum,
                    estimatedEchoDelayWeight,
                ),
                echoSimilarity = average(echoSimilaritySum, echoSimilarityWeight),
                echoLagMs = averageInt(echoLagSum, echoLagWeight),
                echoCoherence = average(echoCoherenceSum, echoCoherenceWeight),
                farEndRms = average(farEndRmsSum, farEndRmsWeight),
                micRms = average(micRmsSum, micRmsWeight),
                nearEndResidualRatio = average(nearEndResidualSum, nearEndResidualWeight),
                echoLikely = echoLikely,
                discontinuous = discontinuous,
                monotonicTimestampNs = captureEndNs,
                nearEndFarEndEnergyRatio = average(
                    nearEndFarEndEnergyRatioSum,
                    nearEndFarEndEnergyRatioWeight,
                ),
            )
        }

        fun clear() {
            sampleCount = 0
            sourceFrameSequenceStart = 0L
            sourceFrameSequenceEnd = 0L
            captureStartNs = 0L
            captureEndNs = 0L
            playbackState = FarEndReferenceBuffer.State.STOPPED.name
            playbackStateMixed = false
            playbackActive = true
            playbackActiveInitialized = false
            playbackPositionSum = 0.0
            playbackPositionWeight = 0L
            responseId = null
            responseIdMixed = false
            referenceReady = true
            referenceInitialized = false
            referenceConfidence = "NONE"
            referenceConfidenceMixed = false
            estimatedEchoDelaySum = 0.0
            estimatedEchoDelayWeight = 0L
            echoSimilaritySum = 0.0
            echoSimilarityWeight = 0L
            echoLagSum = 0.0
            echoLagWeight = 0L
            echoCoherenceSum = 0.0
            echoCoherenceWeight = 0L
            farEndRmsSum = 0.0
            farEndRmsWeight = 0L
            micRmsSum = 0.0
            micRmsWeight = 0L
            nearEndResidualSum = 0.0
            nearEndResidualWeight = 0L
            nearEndFarEndEnergyRatioSum = 0.0
            nearEndFarEndEnergyRatioWeight = 0L
            echoLikely = false
        }

        private fun addWeighted(
            value: Double?,
            sampleCount: Int,
            sink: (sum: Double, weight: Long) -> Unit,
        ) {
            if (value != null) {
                sink(value * sampleCount.toDouble(), sampleCount.toLong())
            }
        }

        private fun average(sum: Double, weight: Long): Double? =
            if (weight == 0L) null else sum / weight.toDouble()

        private fun averageInt(sum: Double, weight: Long): Int? =
            average(sum, weight)?.roundToInt()
    }

    private fun recordNonFatalError(code: String, message: String) {
        synchronized(lock) {
            recordErrorLocked(code, message, fatal = false)
        }
        Log.w(AudioEngine.TAG, "$code: $message")
    }

    private fun recordErrorLocked(code: String, message: String, fatal: Boolean) {
        errorCount += 1L
        lastErrorCode = code
        lastErrorMessage = message
        if (fatal) {
            lifecycleState = "ERROR"
        }
    }
}
