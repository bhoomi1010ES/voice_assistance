package com.voiceaipoc.wakeword

import android.util.Log
import com.voiceaipoc.audio.AudioEngine
import com.voiceaipoc.audio.AudioRingBuffer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.max

/**
 * Native openWakeWord worker and lifecycle boundary.
 *
 * The existing PCM consumer is the sole producer for a small preallocated
 * decoupling queue. Feature extraction/classifier inference executes only on
 * [WORKER_THREAD_NAME], never on AudioRecord, the PCM consumer, or JavaScript.
 */
class WakeWordEngine(
    private val config: WakeWordConfig,
    private val frameDurationMs: Int,
    private val frameSizeSamples: Int,
    private val modelAssets: WakeWordModelAssets,
    private val runtimeName: String = SELECTED_RUNTIME,
    private val runtimeVersion: String = SELECTED_RUNTIME_VERSION,
    private val runtimeAvailable: Boolean = false,
    private val runtimeFactory: WakeWordRuntimeFactory =
        UnavailableWakeWordRuntimeFactory(SELECTED_RUNTIME),
    private val diagnosticCapture: WakeWordDiagnosticCapture? = null,
    private val listener: Listener? = null,
    monotonicClockMs: () -> Long = { System.nanoTime() / NANOS_PER_MILLISECOND },
    private val wallClockMs: () -> Long = System::currentTimeMillis,
) {
    interface Listener {
        fun onEngineStarted(status: Status)
        fun onEngineStopped(status: Status)
        fun onWakeWordDetected(event: DetectionEvent)
        fun onEngineError(status: Status)
    }

    data class DetectionEvent(
        val event: String,
        val timestampMs: Long,
        val modelName: String,
        val confidence: Float,
        val detectionCount: Long,
        val detectionSequenceNumber: Long,
        val inferenceIndex: Long,
        val inferenceTimestampMs: Long,
        val wakeStateBefore: String,
        val wakeStateAfter: String,
        val cooldownRemainingMs: Long,
        val millisecondsSincePreviousDetection: Long?,
        val microphoneSessionId: Int,
        val workerGeneration: Long,
        val framesConsumed: Long,
        val queueDepthFrames: Int,
        val droppedFrameCount: Long,
    )

    data class Status(
        val enabled: Boolean,
        val available: Boolean,
        val modelPresent: Boolean,
        val modelName: String,
        val modelVersion: String,
        val modelReleaseTag: String,
        val modelGitCommit: String,
        val modelLicense: String,
        val modelFormat: String,
        val modelAssetDirectory: String,
        val missingModelAssets: String,
        val modelHashVerified: Boolean,
        val classifierSha256: String?,
        val runtimeName: String,
        val runtimeVersion: String,
        val runtimeAvailable: Boolean,
        val runtimeInitialized: Boolean,
        val tensorContractVerified: Boolean,
        val sessionActive: Boolean,
        val running: Boolean,
        val workerThreadAlive: Boolean,
        val state: String,
        val detectionThreshold: Float,
        val cooldownMs: Long,
        val cooldownRemainingMs: Long,
        val inputFrameDurationMs: Int,
        val inputFrameSizeSamples: Int,
        val inferenceWindowDurationMs: Int,
        val inferenceWindowSamples: Int,
        val queuedFrames: Int,
        val queueCapacityFrames: Int,
        val queueHighWaterMarkFrames: Int,
        val framesOffered: Long,
        val framesConsumed: Long,
        val inferenceCount: Long,
        val averageInferenceLatencyMs: Double,
        val maximumInferenceLatencyMs: Double,
        val detectionCount: Long,
        val duplicateSuppressionCount: Long,
        val droppedFrameCount: Long,
        val malformedFrameCount: Long,
        val runtimeErrorCount: Long,
        val lastDetectionTimestampMs: Long,
        val lastConfidence: Float?,
        val manualTrial: WakeWordManualTrialStatus,
        val pcmContextSamples: Int,
        val melHistoryFrames: Int,
        val melBins: Int,
        val embeddingHistoryFrames: Int,
        val embeddingFeatureSize: Int,
        val classifierOutputSemantics: String,
        val acousticDiagnostics: WakeWordAcousticStatus,
        val lastErrorCode: String?,
        val lastErrorMessage: String?,
    )

    data class StartResult(
        val succeeded: Boolean,
        val errorCode: String? = null,
        val errorMessage: String? = null,
    )

    companion object {
        const val EVENT_WAKE_WORD_DETECTED = "WAKE_WORD_DETECTED"
        const val EVENT_ENGINE_STARTED = "WAKE_ENGINE_STARTED"
        const val EVENT_ENGINE_STOPPED = "WAKE_ENGINE_STOPPED"
        const val EVENT_ENGINE_ERROR = "WAKE_ENGINE_ERROR"

        const val SELECTED_RUNTIME = OnnxWakeWordRuntime.RUNTIME_NAME
        const val SELECTED_RUNTIME_VERSION = OnnxWakeWordRuntime.RUNTIME_VERSION
        const val ERROR_MODEL_MISSING = "E_WAKE_MODEL_MISSING"
        const val ERROR_MODEL_INVALID = "E_WAKE_MODEL_INVALID"
        const val ERROR_MODEL_CONTRACT = "E_WAKE_MODEL_CONTRACT"
        const val ERROR_RUNTIME_UNAVAILABLE = "E_WAKE_RUNTIME_UNAVAILABLE"
        const val ERROR_ALREADY_RUNNING = "E_WAKE_ALREADY_RUNNING"
        const val ERROR_WORKER_START = "E_WAKE_WORKER_START"
        const val ERROR_RUNTIME_INITIALIZATION = "E_WAKE_RUNTIME_INITIALIZATION"
        const val ERROR_RUNTIME_INITIALIZATION_TIMEOUT = "E_WAKE_RUNTIME_INITIALIZATION_TIMEOUT"
        const val ERROR_INFERENCE = "E_WAKE_INFERENCE"
        const val ERROR_WORKER_FAILURE = "E_WAKE_WORKER_FAILURE"
        const val ERROR_MALFORMED_PCM = "E_WAKE_MALFORMED_PCM"
        const val ERROR_RUNTIME_RELEASE = "E_WAKE_RUNTIME_RELEASE"
        const val ERROR_WORKER_STOP = "E_WAKE_WORKER_STOP"
        const val ERROR_DIAGNOSTIC_TRIAL = "E_WAKE_DIAGNOSTIC_TRIAL"
        const val ERROR_DIAGNOSTIC_CAPTURE = "E_WAKE_DIAGNOSTIC_CAPTURE"

        private const val WORKER_THREAD_NAME = "VoiceAI-WakeWord"
        private const val QUEUE_WAIT_TIMEOUT_MS = 50L
        private const val OVERFLOW_LOG_INTERVAL = 100L
        private const val MALFORMED_LOG_INTERVAL = 100L
        private const val NANOS_PER_MILLISECOND = 1_000_000L
        private const val OPENWAKEWORD_CHUNK_QUANTUM_MS = 80
    }

    private val lock = Any()
    private val stateMachine = WakeWordStateMachine(config, monotonicClockMs, wallClockMs)
    private val acousticDiagnostics = WakeWordAcousticDiagnostics(
        available = config.acousticDiagnosticsEnabled,
        thresholds = config.diagnosticScoreThresholds,
        trialDurationMs = config.calibrationTrialDurationMs,
        trialHistoryCapacity = config.calibrationTrialHistoryCapacity,
        cooldownMs = config.cooldownMs,
    )
    private val inferenceWindowSamples = frameSizeSamples * config.inputFramesPerInference
    private val frameQueue = AudioRingBuffer(
        capacityFrames = config.queueCapacityFrames,
        frameSizeSamples = frameSizeSamples,
    )
    private val workerFrame = ShortArray(frameSizeSamples)
    private val inferenceWindow = ShortArray(inferenceWindowSamples)

    @Volatile
    private var running = false
    private var sessionActive = false
    private var runtimeInitialized = false
    private var tensorContractVerified = false
    private var workerThread: Thread? = null
    private var startupLatch: CountDownLatch? = null
    private var assembledFrameCount = 0
    private var framesOffered = 0L
    private var framesConsumed = 0L
    private var inferenceCount = 0L
    private var inferenceDurationNanos = 0L
    private var maximumInferenceDurationNanos = 0L
    private var queueHighWaterMarkFrames = 0
    private var droppedFrameCount = 0L
    private var malformedFrameCount = 0L
    private var runtimeErrorCount = 0L
    private var lastErrorCode: String? = null
    private var lastErrorMessage: String? = null
    private var currentAecEnabled = false
    private var currentNoiseSuppressionEnabled = false
    private var workerGeneration = 0L
    private val manualTrialDiagnostics = WakeWordManualTrialDiagnostics(
        detectionThreshold = config.detectionThreshold,
        cooldownDurationMs = config.cooldownMs,
    )

    init {
        require(frameDurationMs > 0) { "frameDurationMs must be positive" }
        require(frameSizeSamples > 0) { "frameSizeSamples must be positive" }
        require(
            (frameDurationMs * config.inputFramesPerInference) %
                OPENWAKEWORD_CHUNK_QUANTUM_MS == 0,
        ) {
            "openWakeWord inference windows must be multiples of 80 ms"
        }
    }

    /** Starts one model worker or reports an explicit unavailable/error state. */
    fun startSession(microphoneSessionId: Int = -1): StartResult {
        var startupError: Pair<String, String>? = null
        var workerStartException: RuntimeException? = null
        var readyLatch: CountDownLatch? = null

        synchronized(lock) {
            if (sessionActive || workerThread?.isAlive == true) {
                val message = "Wake-word session is already active."
                recordErrorLocked(ERROR_ALREADY_RUNNING, message, fatal = false)
                startupError = ERROR_ALREADY_RUNNING to message
            } else {
                resetForNewSessionLocked()
                manualTrialDiagnostics.begin(
                    microphoneSessionId = microphoneSessionId,
                    timestampMs = wallClockMs(),
                    workerGeneration = workerGeneration,
                )
                sessionActive = true
                startupError = when {
                    !config.enabled -> {
                        stateMachine.stop()
                        "E_WAKE_DISABLED" to "Wake-word processing is disabled by configuration."
                    }

                    !modelAssets.present -> {
                        val message = "Approved openWakeWord model bundle is missing: " +
                            modelAssets.missingAssetPaths.joinToString()
                        recordErrorLocked(ERROR_MODEL_MISSING, message, fatal = true)
                        ERROR_MODEL_MISSING to message
                    }

                    !modelAssets.hashVerified -> {
                        val message = modelAssets.validationError
                            ?: "Approved openWakeWord model integrity validation failed."
                        recordErrorLocked(ERROR_MODEL_INVALID, message, fatal = true)
                        ERROR_MODEL_INVALID to message
                    }

                    !runtimeAvailable -> {
                        val message = "$runtimeName is selected but is not packaged."
                        recordErrorLocked(ERROR_RUNTIME_UNAVAILABLE, message, fatal = true)
                        ERROR_RUNTIME_UNAVAILABLE to message
                    }

                    else -> null
                }

                if (startupError == null) {
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
                        val message = "Wake-word worker could not start: ${exception.message}"
                        recordErrorLocked(ERROR_WORKER_START, message, fatal = true)
                        startupError = ERROR_WORKER_START to message
                        workerStartException = exception
                    }
                }
            }
        }

        if (startupError != null) {
            val (code, message) = startupError!!
            Log.w(AudioEngine.TAG, "$code: $message", workerStartException)
            listener?.onEngineError(getStatus())
            return StartResult(false, code, message)
        }

        Log.i(
            AudioEngine.TAG,
            "Wake-word worker starting: runtime=$runtimeName, model=${config.modelName}, " +
                "threshold=${config.detectionThreshold}, cooldownMs=${config.cooldownMs}, " +
                "inferenceWindowMs=${frameDurationMs * config.inputFramesPerInference}",
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
            val message = "Wake-word runtime did not initialize within " +
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
                    ?: "Wake-word runtime initialization failed.",
            )
        }
        return StartResult(true)
    }

    /**
     * Non-blocking producer call from VoiceAI-PcmConsumer. One bounded native
     * copy decouples model latency; the source frame is never retained.
     */
    fun offerPcmFrame(pcmSamples: ShortArray, samplesRead: Int): Boolean {
        if (!running) {
            return false
        }
        if (samplesRead != frameSizeSamples || samplesRead > pcmSamples.size) {
            val errorCount = synchronized(lock) {
                malformedFrameCount += 1L
                lastErrorCode = ERROR_MALFORMED_PCM
                lastErrorMessage =
                    "Malformed wake-word PCM frame: samplesRead=$samplesRead, expected=$frameSizeSamples."
                malformedFrameCount
            }
            if (errorCount == 1L || errorCount % MALFORMED_LOG_INTERVAL == 0L) {
                Log.e(AudioEngine.TAG, "$ERROR_MALFORMED_PCM: count=$errorCount")
            }
            if (errorCount == 1L) {
                listener?.onEngineError(getStatus())
            }
            return false
        }

        val writeResult = frameQueue.write(pcmSamples, sampleCount = samplesRead)
        val overflowCount = synchronized(lock) {
            framesOffered += 1L
            queueHighWaterMarkFrames = max(
                queueHighWaterMarkFrames,
                frameQueue.currentBufferedFrames(),
            )
            if (writeResult == AudioRingBuffer.WriteResult.WROTE_AFTER_DROPPING_OLDEST) {
                droppedFrameCount += 1L
            }
            droppedFrameCount
        }
        if (
            writeResult == AudioRingBuffer.WriteResult.WROTE_AFTER_DROPPING_OLDEST &&
            (overflowCount == 1L || overflowCount % OVERFLOW_LOG_INTERVAL == 0L)
        ) {
            Log.w(
                AudioEngine.TAG,
                "Wake-word queue overflow: count=$overflowCount, policy=DROP_OLDEST",
            )
        }
        return true
    }

    /** Starts one bounded, metadata-only human calibration observation. */
    fun beginCalibrationTrial(expectedPositive: Boolean, condition: String): StartResult {
        synchronized(lock) {
            if (!running || !runtimeInitialized) {
                return StartResult(
                    succeeded = false,
                    errorCode = ERROR_DIAGNOSTIC_TRIAL,
                    errorMessage = "Wake-word inference must be running before a trial begins.",
                )
            }
            val label = try {
                acousticDiagnostics.beginTrial(
                    expectedPositive = expectedPositive,
                    condition = condition,
                    timestampMs = wallClockMs(),
                    aecEnabled = currentAecEnabled,
                    noiseSuppressionEnabled = currentNoiseSuppressionEnabled,
                )
            } catch (exception: IllegalArgumentException) {
                return StartResult(
                    succeeded = false,
                    errorCode = ERROR_DIAGNOSTIC_TRIAL,
                    errorMessage = exception.message,
                )
            }
                ?: return StartResult(
                    succeeded = false,
                    errorCode = ERROR_DIAGNOSTIC_TRIAL,
                    errorMessage = "A wake-word calibration trial is already active or disabled.",
                )
            Log.i(
                AudioEngine.TAG,
                "Wake calibration trial started: label=$label, " +
                    "durationMs=${config.calibrationTrialDurationMs}",
            )
        }
        return StartResult(succeeded = true)
    }

    fun setAcousticCalibrationEnabled(enabled: Boolean): StartResult {
        if (!config.acousticDiagnosticsEnabled && enabled) {
            return StartResult(
                false,
                ERROR_DIAGNOSTIC_TRIAL,
                "Wake-word acoustic diagnostics are unavailable.",
            )
        }
        acousticDiagnostics.setEnabled(enabled, wallClockMs())?.let(::logCalibrationTrial)
        Log.i(AudioEngine.TAG, "Wake acoustic calibration mode enabled=$enabled")
        return StartResult(true)
    }

    fun setAudioProcessingState(aecEnabled: Boolean, noiseSuppressionEnabled: Boolean) {
        synchronized(lock) {
            currentAecEnabled = aecEnabled
            currentNoiseSuppressionEnabled = noiseSuppressionEnabled
        }
    }

    /** Clears only calibration metadata; model state and inference are unchanged. */
    fun resetAcousticDiagnostics() {
        acousticDiagnostics.reset()
        Log.i(AudioEngine.TAG, "Wake acoustic calibration diagnostics reset")
    }

    /** Starts one explicit, finite app-private PCM capture at wake-hop boundaries. */
    fun startDiagnosticPcmCapture(
        label: String,
        durationMs: Int = WakeWordDiagnosticCapture.DEFAULT_CAPTURE_DURATION_MS,
    ): StartResult {
        synchronized(lock) {
            if (!running || !runtimeInitialized) {
                return StartResult(
                    false,
                    ERROR_DIAGNOSTIC_CAPTURE,
                    "Wake-word inference must be running before PCM capture begins.",
                )
            }
        }
        val capture = diagnosticCapture ?: return StartResult(
            false,
            ERROR_DIAGNOSTIC_CAPTURE,
            "Diagnostic PCM capture is unavailable.",
        )
        val result = capture.start(label, durationMs)
        if (result.succeeded) {
            Log.i(
                AudioEngine.TAG,
                "Diagnostic wake PCM capture started: label=$label, durationMs=$durationMs",
            )
        }
        return StartResult(result.succeeded, result.errorCode, result.errorMessage)
    }

    fun stopDiagnosticPcmCapture() {
        diagnosticCapture?.stop()
        Log.i(AudioEngine.TAG, "Diagnostic wake PCM capture stopped")
    }

    fun getDiagnosticPcmCaptureStatus(): WakeWordDiagnosticCapture.Status? =
        diagnosticCapture?.getStatus()

    /** Stops/joins the worker and leaves no queued or partially assembled PCM. */
    fun stopSession() {
        val thread: Thread?
        val wasActive: Boolean
        synchronized(lock) {
            wasActive = sessionActive || running || workerThread != null
            sessionActive = false
            running = false
            thread = workerThread
        }

        thread?.interrupt()
        if (thread != null && thread !== Thread.currentThread()) {
            try {
                thread.join(config.workerJoinTimeoutMs)
            } catch (interrupted: InterruptedException) {
                Thread.currentThread().interrupt()
                recordNonFatalError(
                    ERROR_WORKER_STOP,
                    "Interrupted while stopping wake-word worker.",
                )
            }
        }

        val stillAlive = thread?.isAlive == true
        synchronized(lock) {
            if (stillAlive) {
                recordErrorLocked(
                    ERROR_WORKER_STOP,
                    "Wake-word worker did not stop within ${config.workerJoinTimeoutMs} ms.",
                    fatal = true,
                )
            } else {
                if (workerThread === thread) {
                    workerThread = null
                }
                runtimeInitialized = false
                tensorContractVerified = false
                stateMachine.stop()
                assembledFrameCount = 0
                workerFrame.fill(0)
                inferenceWindow.fill(0)
            }
            frameQueue.clear()
            val state = stateMachine.getStatus()
            manualTrialDiagnostics.finish(
                timestampMs = wallClockMs(),
                currentWakeState = state.state.name,
                cooldownRemainingMs = state.cooldownRemainingMs,
                queueDepthFrames = frameQueue.currentBufferedFrames(),
                queueHighWaterMarkFrames = queueHighWaterMarkFrames,
                queueDrops = droppedFrameCount,
                runtimeErrors = runtimeErrorCount,
            )
        }
        acousticDiagnostics.finishActiveTrial(wallClockMs())?.let(::logCalibrationTrial)
        diagnosticCapture?.stop()

        if (stillAlive) {
            listener?.onEngineError(getStatus())
        }
        if (wasActive) {
            val status = getStatus()
            Log.i(
                AudioEngine.TAG,
                "Wake-word worker stopped: inferenceCount=${status.inferenceCount}, " +
                    "detections=${status.detectionCount}, droppedFrames=${status.droppedFrameCount}, " +
                    "errors=${status.runtimeErrorCount}",
            )
            listener?.onEngineStopped(status)
        }
    }

    fun getStatus(): Status {
        val stateStatus = stateMachine.getStatus()
        return synchronized(lock) {
            Status(
                enabled = config.enabled,
                available = config.enabled && modelAssets.present &&
                    modelAssets.hashVerified && runtimeAvailable,
                modelPresent = modelAssets.present,
                modelName = config.modelName,
                modelVersion = ApprovedOpenWakeWordModel.ARTIFACT_VERSION,
                modelReleaseTag = ApprovedOpenWakeWordModel.RELEASE_TAG,
                modelGitCommit = ApprovedOpenWakeWordModel.GIT_COMMIT,
                modelLicense = ApprovedOpenWakeWordModel.LICENSE,
                modelFormat = config.modelFormat,
                modelAssetDirectory = config.assetDirectory,
                missingModelAssets = modelAssets.missingAssetPaths.joinToString(),
                modelHashVerified = modelAssets.hashVerified,
                classifierSha256 = modelAssets.classifierSha256,
                runtimeName = runtimeName,
                runtimeVersion = runtimeVersion,
                runtimeAvailable = runtimeAvailable,
                runtimeInitialized = runtimeInitialized,
                tensorContractVerified = tensorContractVerified,
                sessionActive = sessionActive,
                running = running,
                workerThreadAlive = workerThread?.isAlive == true,
                state = stateStatus.state.name,
                detectionThreshold = config.detectionThreshold,
                cooldownMs = config.cooldownMs,
                cooldownRemainingMs = stateStatus.cooldownRemainingMs,
                inputFrameDurationMs = frameDurationMs,
                inputFrameSizeSamples = frameSizeSamples,
                inferenceWindowDurationMs = frameDurationMs * config.inputFramesPerInference,
                inferenceWindowSamples = inferenceWindowSamples,
                queuedFrames = frameQueue.currentBufferedFrames(),
                queueCapacityFrames = frameQueue.capacityFrames,
                queueHighWaterMarkFrames = queueHighWaterMarkFrames,
                framesOffered = framesOffered,
                framesConsumed = framesConsumed,
                inferenceCount = inferenceCount,
                averageInferenceLatencyMs = if (inferenceCount == 0L) {
                    0.0
                } else {
                    inferenceDurationNanos.toDouble() /
                        inferenceCount.toDouble() / NANOS_PER_MILLISECOND.toDouble()
                },
                maximumInferenceLatencyMs =
                    maximumInferenceDurationNanos.toDouble() /
                        NANOS_PER_MILLISECOND.toDouble(),
                detectionCount = stateStatus.detectionCount,
                duplicateSuppressionCount = stateStatus.duplicateSuppressionCount,
                droppedFrameCount = droppedFrameCount,
                malformedFrameCount = malformedFrameCount,
                runtimeErrorCount = runtimeErrorCount,
                lastDetectionTimestampMs = stateStatus.lastDetectionTimestampMs,
                lastConfidence = stateStatus.lastConfidence,
                manualTrial = manualTrialDiagnostics.snapshot(),
                pcmContextSamples = ApprovedOpenWakeWordModel.MEL_CONTEXT_SAMPLES,
                melHistoryFrames = ApprovedOpenWakeWordModel.MEL_HISTORY_FRAMES,
                melBins = ApprovedOpenWakeWordModel.MEL_BINS,
                embeddingHistoryFrames = ApprovedOpenWakeWordModel.CLASSIFIER_HISTORY_FRAMES,
                embeddingFeatureSize = ApprovedOpenWakeWordModel.CLASSIFIER_FEATURE_SIZE,
                classifierOutputSemantics = "RAW_SIGMOID_PROBABILITY",
                acousticDiagnostics = acousticDiagnostics.snapshot(),
                lastErrorCode = lastErrorCode,
                lastErrorMessage = lastErrorMessage,
            )
        }
    }

    fun getManualTrialStatus(): WakeWordManualTrialStatus =
        manualTrialDiagnostics.snapshot()

    fun getManualTrialHistory(): List<WakeWordManualTrialStatus> =
        manualTrialDiagnostics.history()

    private fun workerLoop() {
        var runtime: WakeWordInferenceRuntime? = null
        try {
            runtime = runtimeFactory.create()
            try {
                runtime.initialize()
                runtime.reset()
            } catch (exception: WakeWordRuntimeException) {
                throw exception
            } catch (exception: RuntimeException) {
                throw WakeWordRuntimeException(
                    ERROR_RUNTIME_INITIALIZATION,
                    "Wake-word runtime initialization failed: ${exception.message}",
                    exception,
                )
            }
            synchronized(lock) {
                if (!running) {
                    return
                }
                runtimeInitialized = true
                tensorContractVerified = runtime.tensorContractVerified
                stateMachine.startListening()
            }
            startupLatch?.countDown()
            Log.i(
                AudioEngine.TAG,
                "Wake-word engine started: runtime=${runtime.runtimeName} ${runtime.runtimeVersion}",
            )
            listener?.onEngineStarted(getStatus())

            while (running) {
                val samplesRead = try {
                    frameQueue.read(workerFrame, waitTimeoutMs = QUEUE_WAIT_TIMEOUT_MS)
                } catch (interrupted: InterruptedException) {
                    if (running) {
                        throw WakeWordRuntimeException(
                            ERROR_WORKER_FAILURE,
                            "Wake-word worker was interrupted unexpectedly.",
                            interrupted,
                        )
                    }
                    break
                }
                if (!running || samplesRead == 0) {
                    continue
                }

                val destinationOffset = assembledFrameCount * frameSizeSamples
                System.arraycopy(
                    workerFrame,
                    0,
                    inferenceWindow,
                    destinationOffset,
                    frameSizeSamples,
                )
                assembledFrameCount += 1
                synchronized(lock) { framesConsumed += 1L }

                if (assembledFrameCount == config.inputFramesPerInference) {
                    diagnosticCapture?.offerInferenceWindow(
                        inferenceWindow,
                        inferenceWindowSamples,
                    )
                    val inferenceStartedNanos = System.nanoTime()
                    val confidence = try {
                        runtime.predict(inferenceWindow, inferenceWindowSamples)
                    } catch (exception: WakeWordRuntimeException) {
                        throw exception
                    } catch (exception: RuntimeException) {
                        throw WakeWordRuntimeException(
                            ERROR_INFERENCE,
                            "Wake-word inference failed: ${exception.message}",
                            exception,
                        )
                    }
                    val inferenceElapsedNanos =
                        (System.nanoTime() - inferenceStartedNanos).coerceAtLeast(0L)
                    if (!confidence.isFinite() || confidence !in 0.0f..1.0f) {
                        throw WakeWordRuntimeException(
                            ERROR_INFERENCE,
                            "Wake-word runtime returned invalid confidence $confidence.",
                        )
                    }
                    val currentInferenceIndex = synchronized(lock) {
                        inferenceCount += 1L
                        inferenceDurationNanos += inferenceElapsedNanos
                        maximumInferenceDurationNanos = max(
                            maximumInferenceDurationNanos,
                            inferenceElapsedNanos,
                        )
                        inferenceCount
                    }
                    assembledFrameCount = 0

                    val inferenceTimestampMs = wallClockMs()
                    val stateBeforeInference = stateMachine.getStatus()
                    val detection = stateMachine.onConfidence(
                        confidence = confidence,
                        wallTimestampMs = inferenceTimestampMs,
                    )
                    val stateAfterInference = stateMachine.getStatus()
                    val queueDepthFrames = frameQueue.currentBufferedFrames()
                    val queueHighWaterMarkFrames = synchronized(lock) {
                        this@WakeWordEngine.queueHighWaterMarkFrames
                    }
                    val droppedFrames = synchronized(lock) { droppedFrameCount }
                    val runtimeErrors = synchronized(lock) { runtimeErrorCount }
                    val duplicateSuppressed =
                        stateAfterInference.duplicateSuppressionCount >
                            stateBeforeInference.duplicateSuppressionCount
                    manualTrialDiagnostics.recordInference(
                        inferenceWindowSequence = currentInferenceIndex,
                        inferenceTimestampMs = inferenceTimestampMs,
                        score = confidence,
                        wakeStateBefore = stateBeforeInference.state.name,
                        wakeStateAfter = stateAfterInference.state.name,
                        cooldownRemainingMs = stateAfterInference.cooldownRemainingMs,
                        queueDepthFrames = queueDepthFrames,
                        queueHighWaterMarkFrames = queueHighWaterMarkFrames,
                        queueDrops = droppedFrames,
                        runtimeErrors = runtimeErrors,
                        detection = detection,
                        suppressedByCooldown = duplicateSuppressed,
                    )
                    acousticDiagnostics.recordInference(
                        pcm16 = inferenceWindow,
                        samplesRead = inferenceWindowSamples,
                        inferenceIndex = currentInferenceIndex,
                        score = confidence,
                        timestampMs = inferenceTimestampMs,
                        queueDepthFrames = queueDepthFrames,
                        inferenceDurationNanos = inferenceElapsedNanos,
                        aecEnabled = currentAecEnabled,
                        noiseSuppressionEnabled = currentNoiseSuppressionEnabled,
                        detectionOccurred = detection != null,
                        duplicateSuppressed = duplicateSuppressed,
                    )?.let(::logCalibrationTrial)

                    detection?.let {
                        val event = DetectionEvent(
                            event = EVENT_WAKE_WORD_DETECTED,
                            timestampMs = it.timestampMs,
                            modelName = it.modelName,
                            confidence = it.confidence,
                            detectionCount = it.detectionCount,
                            detectionSequenceNumber = it.detectionCount,
                            inferenceIndex = currentInferenceIndex,
                            inferenceTimestampMs = inferenceTimestampMs,
                            wakeStateBefore = stateBeforeInference.state.name,
                            wakeStateAfter = stateAfterInference.state.name,
                            cooldownRemainingMs = stateAfterInference.cooldownRemainingMs,
                            millisecondsSincePreviousDetection =
                                it.millisecondsSincePreviousDetection,
                            microphoneSessionId = manualTrialDiagnostics.snapshot().microphoneSessionId,
                            workerGeneration = workerGeneration,
                            framesConsumed = synchronized(lock) { framesConsumed },
                            queueDepthFrames = queueDepthFrames,
                            droppedFrameCount = droppedFrames,
                        )
                        Log.i(
                            AudioEngine.TAG,
                            "Wake word detected: model=${event.modelName}, " +
                                "confidence=${event.confidence}, count=${event.detectionCount}",
                        )
                        listener?.onWakeWordDetected(event)
                    }
                }
            }
        } catch (exception: WakeWordRuntimeException) {
            recordWorkerError(exception.errorCode, exception.message ?: "Wake-word runtime failed.")
        } catch (exception: RuntimeException) {
            recordWorkerError(
                ERROR_WORKER_FAILURE,
                "Wake-word worker failed: ${exception.message}",
            )
        } finally {
            startupLatch?.countDown()
            try {
                runtime?.close()
            } catch (exception: RuntimeException) {
                recordWorkerError(
                    ERROR_RUNTIME_RELEASE,
                    "Wake-word runtime release failed: ${exception.message}",
                )
            }
            synchronized(lock) {
                runtimeInitialized = false
                tensorContractVerified = false
                running = false
                if (workerThread === Thread.currentThread()) {
                    workerThread = null
                }
            }
            Log.i(AudioEngine.TAG, "Wake-word worker thread exited")
        }
    }

    private fun recordWorkerError(errorCode: String, message: String) {
        synchronized(lock) {
            recordErrorLocked(errorCode, message, fatal = true)
            running = false
        }
        Log.e(AudioEngine.TAG, "$errorCode: $message")
        listener?.onEngineError(getStatus())
    }

    private fun logCalibrationTrial(trial: WakeWordCalibrationTrial) {
        Log.i(
            AudioEngine.TAG,
            "Wake calibration trial completed: label=${trial.label}, " +
                "expectedPositive=${trial.expectedPositive}, " +
                "windows=${trial.inferenceWindowCount}, " +
                "minScore=${trial.minimumScore}, maxScore=${trial.maximumScore}, " +
                "averageScore=${trial.averageScore}, peakPcmRms=${trial.peakPcmRms}, " +
                "peakPcmDbFs=${trial.peakPcmDbFs}, " +
                "queueMax=${trial.maximumQueueDepthFrames}, " +
                "effects=${trial.audioProcessingMode}, condition=${trial.condition}, " +
                "attempt=${trial.attemptNumber}, peakPcmAmplitude=${trial.peakPcmAmplitude}, " +
                "latencyAverageMs=${trial.averageInferenceLatencyMs}, " +
                "latencyMaximumMs=${trial.maximumInferenceLatencyMs}, " +
                "detections=${trial.detectionCount}, " +
                "duplicates=${trial.duplicateDetectionCount}",
        )
    }

    private fun recordNonFatalError(errorCode: String, message: String) {
        synchronized(lock) {
            recordErrorLocked(errorCode, message, fatal = false)
        }
        Log.e(AudioEngine.TAG, "$errorCode: $message")
        listener?.onEngineError(getStatus())
    }

    private fun recordErrorLocked(errorCode: String, message: String, fatal: Boolean) {
        runtimeErrorCount += 1L
        lastErrorCode = errorCode
        lastErrorMessage = message
        if (fatal) {
            stateMachine.fail()
        }
    }

    private fun resetForNewSessionLocked() {
        workerGeneration += 1L
        frameQueue.clear()
        workerFrame.fill(0)
        inferenceWindow.fill(0)
        assembledFrameCount = 0
        framesOffered = 0L
        framesConsumed = 0L
        inferenceCount = 0L
        inferenceDurationNanos = 0L
        maximumInferenceDurationNanos = 0L
        queueHighWaterMarkFrames = 0
        droppedFrameCount = 0L
        malformedFrameCount = 0L
        runtimeErrorCount = 0L
        lastErrorCode = null
        lastErrorMessage = null
        runtimeInitialized = false
        tensorContractVerified = false
        startupLatch = null
        stateMachine.reset()
        acousticDiagnostics.resetSessionMetrics()
    }
}
