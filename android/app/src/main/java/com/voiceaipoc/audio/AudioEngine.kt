package com.voiceaipoc.audio

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.SystemClock
import android.util.Log
import com.voiceaipoc.vad.VadEngine
import com.voiceaipoc.vad.silero.AndroidSileroVadModelAsset
import com.voiceaipoc.vad.silero.AndroidSileroVadRuntimeFactory
import com.voiceaipoc.vad.silero.OnnxSileroVadRuntime
import com.voiceaipoc.vad.silero.SileroVadEngine
import com.voiceaipoc.wakeword.AndroidWakeWordModelAssets
import com.voiceaipoc.wakeword.AndroidWakeWordRuntimeFactory
import com.voiceaipoc.wakeword.OnnxWakeWordRuntime
import com.voiceaipoc.wakeword.WakeWordDiagnosticCapture
import com.voiceaipoc.wakeword.WakeWordDiagnosticReplay
import com.voiceaipoc.wakeword.WakeWordEngine
import com.voiceaipoc.wakeword.WakeWordReplayBatchResult
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.max

/**
 * Native microphone engine and host for the PCM, VAD, and wake-word stages.
 *
 * AudioRecord.read() runs only on [captureThread]. Reads remain signed PCM16 in
 * native memory, are framed into a bounded [AudioRingBuffer], and are drained
 * by a dedicated native consumer. No PCM crosses the React Native bridge.
 */
class AudioEngine(
    private val context: Context,
    val config: AudioConfig = AudioConfig(),
    private val pcmDataCallback: PcmDataCallback = PcmDataCallback { _, _ -> },
    private val vadEventListener: VadEngine.Listener? = null,
    private val sileroVadEventListener: SileroVadEngine.Listener? = null,
    private val wakeWordEventListener: WakeWordEngine.Listener? = null,
    private val listener: Listener? = null,
) {
    fun interface PcmDataCallback {
        /**
         * Receives one complete, reusable PCM frame on the native consumer
         * thread. Implementations must not retain [buffer].
         */
        fun onPcmData(buffer: ShortArray, samplesRead: Int)
    }

    interface Listener {
        fun onStarted(status: Status)
        fun onStopped(status: Status)
        fun onError(status: Status)
    }

    data class Status(
        val permissionGranted: Boolean,
        val state: String,
        val audioRecordInitialized: Boolean,
        val sampleRateHz: Int,
        val channelCount: Int,
        val encoding: String,
        val bufferSizeBytes: Int,
        val minBufferSizeBytes: Int,
        val audioSessionId: Int,
        val pcmFramesCaptured: Long,
        val captureDurationMs: Long,
        val microphoneErrorCount: Int,
        val lastError: String?,
    )

    data class AudioPipelineStatus(
        val recording: Boolean,
        val state: String,
        val captureStarted: Boolean,
        val captureStopped: Boolean,
        val sampleRateHz: Int,
        val channelCount: Int,
        val pcmFormat: String,
        val frameDurationMs: Int,
        val frameSizeSamples: Int,
        val frameSizeBytes: Int,
        val bufferedFrames: Int,
        val bufferedBytes: Int,
        val bufferCapacityFrames: Int,
        val bufferCapacityBytes: Int,
        val maxBufferedDurationMs: Int,
        val maxObservedBufferedFrames: Int,
        val totalPcmFramesCaptured: Long,
        val totalPcmBytesProcessed: Long,
        val framesWrittenToRingBuffer: Long,
        val framesConsumedFromRingBuffer: Long,
        val totalFramesProcessed: Long,
        val overflowCount: Long,
        val invalidReadCount: Long,
        val readErrorCount: Long,
        val pipelineErrorCount: Long,
        val partialFrameSamples: Int,
        val vad: VadEngine.Status,
        val sileroVad: SileroVadEngine.Status,
    )

    data class OperationResult(
        val succeeded: Boolean,
        val errorCode: String? = null,
        val errorMessage: String? = null,
    )

    private class CaptureSession(
        val recorder: AudioRecord,
        val buffer: ShortArray,
        val audioSessionId: Int,
    ) {
        val stopRequested = AtomicBoolean(false)
        val released = AtomicBoolean(false)
        val errorReported = AtomicBoolean(false)
        val stoppedNotified = AtomicBoolean(false)
    }

    companion object {
        const val TAG = "VoiceAI-Audio"

        private const val THREAD_NAME = "VoiceAI-AudioCapture"
        private const val BYTES_PER_PCM_SAMPLE = 2
        private const val STOP_JOIN_TIMEOUT_MS = 2_000L
        private const val ZERO_READ_LOG_INTERVAL = 100L
        private const val STATE_IDLE = "IDLE"
        private const val STATE_RECORDING = "RECORDING"
        private const val STATE_STOPPING = "STOPPING"
        private const val STATE_STOPPED = "STOPPED"
        private const val STATE_ERROR = "ERROR"
        private const val STATE_PERMISSION_DENIED = "PERMISSION_DENIED"

        private const val ERROR_PERMISSION_DENIED = "E_MICROPHONE_PERMISSION"
        private const val ERROR_ALREADY_RECORDING = "E_ALREADY_RECORDING"
        private const val ERROR_NOT_RECORDING = "E_NOT_RECORDING"
        private const val ERROR_AUDIO_RECORD_INIT = "E_AUDIO_RECORD_INIT"
        private const val ERROR_AUDIO_RECORD_START = "E_AUDIO_RECORD_START"
        private const val ERROR_AUDIO_RECORD_READ = "E_AUDIO_RECORD_READ"
        private const val ERROR_AUDIO_RECORD_STATE = "E_AUDIO_RECORD_STATE"
        private const val ERROR_AUDIO_RECORD_RELEASE = "E_AUDIO_RECORD_RELEASE"
        private const val ERROR_AUDIO_PIPELINE = "E_AUDIO_PIPELINE"
        private const val ERROR_AUDIO_EFFECTS_ACTIVE = "E_AUDIO_EFFECTS_ACTIVE"
        private const val ERROR_WAKE_REPLAY_ACTIVE = "E_WAKE_REPLAY_ACTIVE"

        private const val PCM_FORMAT_LABEL = "PCM_16BIT_LE_SIGNED"
    }

    private val stateLock = Any()
    private val audioEffectsManager = AudioEffectsManager(
        AudioEffectsManager.Config(
            enableAcousticEchoCancellation = config.enableAcousticEchoCancellation,
            enableNoiseSuppression = config.enableNoiseSuppression,
        ),
    )
    private val vadEngine = VadEngine(
        config = config.vadConfig,
        frameDurationMs = config.frameDurationMs,
        frameSizeSamples = config.frameSizeSamples,
        listener = vadEventListener,
    )
    private val wakeWordRuntimeFactory = AndroidWakeWordRuntimeFactory(
        context,
        config.wakeWordConfig,
    )
    private val wakeWordDiagnosticDirectory = File(
        context.filesDir,
        "openwakeword_diagnostics",
    )
    private val wakeWordDiagnosticCapture = WakeWordDiagnosticCapture(
        directory = wakeWordDiagnosticDirectory,
        inferenceSamples = config.frameSizeSamples * config.wakeWordConfig.inputFramesPerInference,
        sampleRateHz = config.sampleRateHz,
    )
    private val wakeWordDiagnosticReplay = WakeWordDiagnosticReplay(
        capture = wakeWordDiagnosticCapture,
        runtimeFactory = wakeWordRuntimeFactory,
        sampleRateHz = config.sampleRateHz,
        inferenceSamples = config.frameSizeSamples * config.wakeWordConfig.inputFramesPerInference,
    )
    private val wakeWordEngine = WakeWordEngine(
        config = config.wakeWordConfig,
        frameDurationMs = config.frameDurationMs,
        frameSizeSamples = config.frameSizeSamples,
        modelAssets = AndroidWakeWordModelAssets.inspect(context, config.wakeWordConfig),
        runtimeName = OnnxWakeWordRuntime.RUNTIME_NAME,
        runtimeVersion = OnnxWakeWordRuntime.RUNTIME_VERSION,
        runtimeAvailable = true,
        runtimeFactory = wakeWordRuntimeFactory,
        diagnosticCapture = wakeWordDiagnosticCapture,
        listener = wakeWordEventListener,
    )
    private val sileroVadEngine = SileroVadEngine(
        config = config.sileroVadConfig,
        frameDurationMs = config.frameDurationMs,
        frameSizeSamples = config.frameSizeSamples,
        modelAsset = AndroidSileroVadModelAsset.inspect(context, config.sileroVadConfig),
        runtimeName = OnnxSileroVadRuntime.RUNTIME_NAME,
        runtimeVersion = OnnxSileroVadRuntime.RUNTIME_VERSION,
        runtimeAvailable = true,
        runtimeFactory = AndroidSileroVadRuntimeFactory(context, config.sileroVadConfig),
        listener = sileroVadEventListener,
    )
    private val pcmPipeline = PcmAudioPipeline(
        config,
        PcmDataCallback { buffer, samplesRead ->
            // VAD reads synchronously on VoiceAI-PcmConsumer. Wake-word work
            // and Silero work are offered to their own bounded worker queues
            // and never block this callback with inference. No stage forwards
            // PCM to JS.
            vadEngine.processFrame(buffer, samplesRead)
            sileroVadEngine.offerPcmFrame(buffer, samplesRead)
            wakeWordEngine.offerPcmFrame(buffer, samplesRead)
            pcmDataCallback.onPcmData(buffer, samplesRead)
        },
    )

    @Volatile
    private var recording = false

    private var session: CaptureSession? = null
    private var captureThread: Thread? = null
    private var state = STATE_IDLE
    private var actualSampleRateHz = config.sampleRateHz
    private var actualChannelCount = config.channelCount
    private var actualEncoding = PCM_FORMAT_LABEL
    private var minBufferSizeBytes = 0
    private var bufferSizeBytes = 0
    private var audioSessionId = AudioRecord.ERROR_BAD_VALUE
    private var pcmFramesCaptured = 0L
    private var captureStartedAtMs = 0L
    private var captureDurationMs = 0L
    private var microphoneErrorCount = 0
    private var invalidReadCount = 0L
    private var readErrorCount = 0L
    private var lastError: String? = null

    fun startRecording(): OperationResult {
        val result: OperationResult

        synchronized(stateLock) {
            result = startRecordingLocked()
        }

        if (result.succeeded) {
            listener?.onStarted(getStatus())
        } else {
            listener?.onError(getStatus())
        }

        return result
    }

    fun stopRecording(): OperationResult {
        val activeSession: CaptureSession
        val thread: Thread?

        synchronized(stateLock) {
            val currentSession = session
            if (currentSession == null) {
                return OperationResult(
                    succeeded = false,
                    errorCode = ERROR_NOT_RECORDING,
                    errorMessage = "Microphone capture is not running.",
                )
            }

            activeSession = currentSession
            activeSession.stopRequested.set(true)
            recording = false
            state = STATE_STOPPING
            thread = captureThread
        }

        stopRecorder(activeSession.recorder)

        if (thread != null && thread !== Thread.currentThread()) {
            try {
                thread.join(STOP_JOIN_TIMEOUT_MS)
            } catch (interrupted: InterruptedException) {
                Thread.currentThread().interrupt()
                Log.w(TAG, "Interrupted while waiting for capture thread to stop", interrupted)
            }
        }

        if (thread?.isAlive == true) {
            Log.w(TAG, "Capture thread did not stop within ${STOP_JOIN_TIMEOUT_MS}ms; interrupting")
            thread.interrupt()
        }

        // The worker normally performs this cleanup in finally. Calling it
        // here also guarantees release if the worker does not exit promptly.
        finishSession(activeSession, null, notifyError = false)
        val status = getStatus()
        val pipelineStatus = getAudioPipelineStatus()
        Log.i(
            TAG,
            "Microphone capture stopped: pcmFrames=${status.pcmFramesCaptured}, " +
                "durationMs=${status.captureDurationMs}, " +
                "pipelineFrames=${pipelineStatus.framesConsumedFromRingBuffer}, " +
                "bufferedFrames=${pipelineStatus.bufferedFrames}",
        )

        return OperationResult(succeeded = true)
    }

    fun isRecording(): Boolean = recording

    /** Releases the active AudioRecord, effects, pipeline, and worker threads. */
    fun release() {
        val currentSession: CaptureSession?

        synchronized(stateLock) {
            currentSession = session
        }

        if (currentSession != null) {
            stopRecording()
        }

        synchronized(stateLock) {
            session?.stopRequested?.set(true)
            session = null
            captureThread = null
            recording = false
            if (state != STATE_ERROR) {
                state = STATE_STOPPED
            }
        }

        pcmPipeline.stopAndClear(::stopNativeDownstream)
        audioEffectsManager.release()
        currentSession?.let {
            stopRecorder(it.recorder)
            releaseRecorder(it)
        }
        Log.i(TAG, "AudioEngine resources released")
    }

    fun getStatus(): Status = synchronized(stateLock) {
        val activeDuration = if (recording) {
            (SystemClock.elapsedRealtime() - captureStartedAtMs).coerceAtLeast(0L)
        } else {
            captureDurationMs
        }

        Status(
            permissionGranted = hasRecordAudioPermission(),
            state = state,
            audioRecordInitialized = session?.released?.get() == false,
            sampleRateHz = actualSampleRateHz,
            channelCount = actualChannelCount,
            encoding = actualEncoding,
            bufferSizeBytes = bufferSizeBytes,
            minBufferSizeBytes = minBufferSizeBytes,
            audioSessionId = audioSessionId,
            pcmFramesCaptured = pcmFramesCaptured,
            captureDurationMs = activeDuration,
            microphoneErrorCount = microphoneErrorCount,
            lastError = lastError,
        )
    }

    fun getAudioProcessingStatus(): AudioEffectsManager.Status =
        audioEffectsManager.getStatus()

    fun getWakeWordStatus(): WakeWordEngine.Status = wakeWordEngine.getStatus()

    /** Changes AEC/NS requests for the next session only; defaults return after app recreation. */
    fun setAudioProcessingCalibrationMode(
        enableAcousticEchoCancellation: Boolean,
        enableNoiseSuppression: Boolean,
    ): OperationResult = synchronized(stateLock) {
        if (recording || session != null) {
            return@synchronized OperationResult(
                succeeded = false,
                errorCode = ERROR_AUDIO_EFFECTS_ACTIVE,
                errorMessage = "Stop microphone capture before changing AEC/NS calibration mode.",
            )
        }
        try {
            audioEffectsManager.setRequestedEffects(
                enableAcousticEchoCancellation,
                enableNoiseSuppression,
            )
            OperationResult(succeeded = true)
        } catch (exception: IllegalStateException) {
            OperationResult(
                succeeded = false,
                errorCode = ERROR_AUDIO_EFFECTS_ACTIVE,
                errorMessage = exception.message,
            )
        }
    }

    fun setWakeWordCalibrationMode(enabled: Boolean): OperationResult {
        val result = wakeWordEngine.setAcousticCalibrationEnabled(enabled)
        return OperationResult(result.succeeded, result.errorCode, result.errorMessage)
    }

    fun beginWakeWordCalibrationTrial(
        expectedPositive: Boolean,
        condition: String,
    ): OperationResult {
        val result = wakeWordEngine.beginCalibrationTrial(expectedPositive, condition)
        return OperationResult(result.succeeded, result.errorCode, result.errorMessage)
    }

    fun resetWakeWordAcousticDiagnostics() {
        wakeWordEngine.resetAcousticDiagnostics()
    }

    fun startWakeWordDiagnosticPcmCapture(
        label: String,
        durationMs: Int,
    ): OperationResult {
        val result = wakeWordEngine.startDiagnosticPcmCapture(label, durationMs)
        return OperationResult(result.succeeded, result.errorCode, result.errorMessage)
    }

    fun stopWakeWordDiagnosticPcmCapture(): WakeWordDiagnosticCapture.Status {
        wakeWordEngine.stopDiagnosticPcmCapture()
        return wakeWordDiagnosticCapture.getStatus()
    }

    fun getWakeWordDiagnosticPcmCaptureStatus(): WakeWordDiagnosticCapture.Status =
        wakeWordDiagnosticCapture.getStatus()

    fun replayWakeWordDiagnosticPcm(repetitions: Int): WakeWordReplayBatchResult {
        synchronized(stateLock) {
            check(!recording && session == null) {
                "$ERROR_WAKE_REPLAY_ACTIVE: Stop microphone capture before diagnostic replay."
            }
        }
        wakeWordDiagnosticCapture.stop()
        return wakeWordDiagnosticReplay.replayAll(repetitions)
    }

    fun deleteWakeWordDiagnosticData(): Int {
        synchronized(stateLock) {
            check(!recording && session == null) {
                "$ERROR_WAKE_REPLAY_ACTIVE: Stop microphone capture before deleting diagnostics."
            }
        }
        return wakeWordDiagnosticCapture.deleteAll()
    }

    fun getAudioPipelineStatus(): AudioPipelineStatus {
        val pipelineStatus = pcmPipeline.getStatus()
        val vadStatus = vadEngine.getStatus()
        val sileroVadStatus = sileroVadEngine.getStatus()
        return synchronized(stateLock) {
            AudioPipelineStatus(
                recording = recording,
                state = state,
                captureStarted = captureStartedAtMs > 0L,
                captureStopped = !recording && state == STATE_STOPPED,
                sampleRateHz = actualSampleRateHz,
                channelCount = actualChannelCount,
                pcmFormat = actualEncoding,
                frameDurationMs = pipelineStatus.frameDurationMs,
                frameSizeSamples = pipelineStatus.frameSizeSamples,
                frameSizeBytes = pipelineStatus.frameSizeBytes,
                bufferedFrames = pipelineStatus.bufferedFrames,
                bufferedBytes = pipelineStatus.bufferedBytes,
                bufferCapacityFrames = pipelineStatus.bufferCapacityFrames,
                bufferCapacityBytes = pipelineStatus.bufferCapacityBytes,
                maxBufferedDurationMs = pipelineStatus.maxBufferedDurationMs,
                maxObservedBufferedFrames = pipelineStatus.maxObservedBufferedFrames,
                totalPcmFramesCaptured = pcmFramesCaptured,
                totalPcmBytesProcessed = pipelineStatus.totalPcmBytesProcessed,
                framesWrittenToRingBuffer = pipelineStatus.framesWrittenToRingBuffer,
                framesConsumedFromRingBuffer = pipelineStatus.framesConsumedFromRingBuffer,
                totalFramesProcessed = pipelineStatus.framesConsumedFromRingBuffer,
                overflowCount = pipelineStatus.overflowCount,
                invalidReadCount = invalidReadCount + pipelineStatus.invalidInputCount,
                readErrorCount = readErrorCount,
                pipelineErrorCount = pipelineStatus.processingErrorCount,
                partialFrameSamples = pipelineStatus.partialFrameSamples,
                vad = vadStatus,
                sileroVad = sileroVadStatus,
            )
        }
    }

    private fun startRecordingLocked(): OperationResult {
        if (recording || session != null) {
            return OperationResult(
                succeeded = false,
                errorCode = ERROR_ALREADY_RECORDING,
                errorMessage = "Microphone capture is already running.",
            )
        }

        if (!hasRecordAudioPermission()) {
            state = STATE_PERMISSION_DENIED
            lastError = "RECORD_AUDIO permission has not been granted."
            Log.e(TAG, lastError!!)
            return OperationResult(
                succeeded = false,
                errorCode = ERROR_PERMISSION_DENIED,
                errorMessage = lastError,
            )
        }

        val channelConfig = AudioFormat.CHANNEL_IN_MONO
        val encoding = AudioFormat.ENCODING_PCM_16BIT
        val minSize = AudioRecord.getMinBufferSize(
            config.sampleRateHz,
            channelConfig,
            encoding,
        )

        minBufferSizeBytes = minSize
        if (minSize <= 0) {
            return failStartLocked(
                ERROR_AUDIO_RECORD_INIT,
                "16 kHz mono PCM16 is not supported: getMinBufferSize returned $minSize.",
                null,
            )
        }

        val pcmSampleFrameBytes = config.channelCount * BYTES_PER_PCM_SAMPLE
        val safeBufferBytes = safeBufferSizeBytes(minSize, pcmSampleFrameBytes)
        bufferSizeBytes = safeBufferBytes

        Log.i(
            TAG,
            "AudioRecord config: sampleRate=${config.sampleRateHz}, " +
                "channels=${config.channelCount}, channelConfig=$channelConfig, " +
                "encoding=$encoding ($PCM_FORMAT_LABEL), minBufferBytes=$minSize, " +
                "bufferBytes=$safeBufferBytes",
        )

        val recorder = try {
            AudioRecord(
                MediaRecorder.AudioSource.MIC,
                config.sampleRateHz,
                channelConfig,
                encoding,
                safeBufferBytes,
            )
        } catch (securityException: SecurityException) {
            return failStartLocked(
                ERROR_PERMISSION_DENIED,
                "Microphone permission was revoked before AudioRecord initialization.",
                null,
                securityException,
            )
        } catch (exception: IllegalArgumentException) {
            return failStartLocked(
                ERROR_AUDIO_RECORD_INIT,
                "AudioRecord rejected the requested 16 kHz mono PCM16 configuration.",
                null,
                exception,
            )
        } catch (exception: RuntimeException) {
            return failStartLocked(
                ERROR_AUDIO_RECORD_INIT,
                "AudioRecord initialization failed: ${exception.message}",
                null,
                exception,
            )
        }

        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            return failStartLocked(
                ERROR_AUDIO_RECORD_INIT,
                "AudioRecord returned STATE_UNINITIALIZED for 16 kHz mono PCM16.",
                recorder,
            )
        }

        val actualSampleRate = recorder.sampleRate
        val actualChannels = recorder.channelCount
        val actualAudioFormat = recorder.audioFormat
        if (
            actualSampleRate != config.sampleRateHz ||
            actualChannels != config.channelCount ||
            actualAudioFormat != encoding
        ) {
            return failStartLocked(
                ERROR_AUDIO_RECORD_INIT,
                "AudioRecord opened an incompatible format: sampleRate=$actualSampleRate, " +
                    "channels=$actualChannels, encoding=$actualAudioFormat.",
                recorder,
            )
        }

        actualSampleRateHz = actualSampleRate
        actualChannelCount = actualChannels
        actualEncoding = PCM_FORMAT_LABEL
        audioSessionId = recorder.audioSessionId
        val effectStatus = audioEffectsManager.attachToAudioSession(audioSessionId)
        wakeWordEngine.setAudioProcessingState(
            aecEnabled = effectStatus.aec.enabled,
            noiseSuppressionEnabled = effectStatus.noiseSuppression.enabled,
        )
        pcmFramesCaptured = 0L
        captureStartedAtMs = 0L
        captureDurationMs = 0L
        invalidReadCount = 0L
        readErrorCount = 0L
        lastError = null

        val captureSession = CaptureSession(
            recorder = recorder,
            buffer = ShortArray(safeBufferBytes / BYTES_PER_PCM_SAMPLE),
            audioSessionId = audioSessionId,
        )

        try {
            vadEngine.startSession()
            pcmPipeline.start()
            sileroVadEngine.startSession()
            wakeWordEngine.startSession()
        } catch (exception: RuntimeException) {
            return failStartLocked(
                ERROR_AUDIO_PIPELINE,
                "Native PCM consumer could not start: ${exception.message}",
                recorder,
                exception,
            )
        }

        try {
            recorder.startRecording()
        } catch (exception: IllegalStateException) {
            return failStartLocked(
                ERROR_AUDIO_RECORD_START,
                "AudioRecord.startRecording() failed: ${exception.message}",
                recorder,
                exception,
            )
        } catch (exception: RuntimeException) {
            return failStartLocked(
                ERROR_AUDIO_RECORD_START,
                "AudioRecord.startRecording() failed: ${exception.message}",
                recorder,
                exception,
            )
        }

        if (recorder.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            return failStartLocked(
                ERROR_AUDIO_RECORD_STATE,
                "AudioRecord did not enter RECORDSTATE_RECORDING.",
                recorder,
            )
        }

        session = captureSession
        recording = true
        state = STATE_RECORDING
        captureStartedAtMs = SystemClock.elapsedRealtime()

        try {
            captureThread = Thread(
                { captureLoop(captureSession) },
                THREAD_NAME,
            ).also { it.start() }
        } catch (exception: RuntimeException) {
            return failStartLocked(
                ERROR_AUDIO_RECORD_START,
                "Native capture worker could not start: ${exception.message}",
                recorder,
                exception,
            )
        }

        Log.i(TAG, "Microphone capture started: audioSessionId=$audioSessionId")
        return OperationResult(succeeded = true)
    }

    private fun captureLoop(captureSession: CaptureSession) {
        var errorCode: String? = null
        var errorMessage: String? = null

        try {
            while (recording && !captureSession.stopRequested.get()) {
                val samplesRead = captureSession.recorder.read(
                    captureSession.buffer,
                    0,
                    captureSession.buffer.size,
                    AudioRecord.READ_BLOCKING,
                )

                when {
                    samplesRead > 0 -> {
                        if (
                            samplesRead > captureSession.buffer.size ||
                            samplesRead % config.channelCount != 0
                        ) {
                            recordInvalidRead(samplesRead, captureSession.buffer.size)
                            continue
                        }

                        synchronized(stateLock) {
                            if (session === captureSession) {
                                pcmFramesCaptured += samplesRead.toLong() / config.channelCount
                            }
                        }

                        try {
                            if (!pcmPipeline.processSamples(captureSession.buffer, samplesRead)) {
                                recordInvalidRead(samplesRead, captureSession.buffer.size)
                            }
                        } catch (exception: RuntimeException) {
                            errorCode = ERROR_AUDIO_PIPELINE
                            errorMessage = "Native PCM pipeline failed: ${exception.message}"
                            Log.e(TAG, errorMessage, exception)
                            break
                        }
                    }

                    captureSession.stopRequested.get() || !recording -> Unit

                    samplesRead == 0 -> {
                        val zeroReads = synchronized(stateLock) {
                            invalidReadCount += 1L
                            invalidReadCount
                        }
                        if (zeroReads == 1L || zeroReads % ZERO_READ_LOG_INTERVAL == 0L) {
                            Log.w(TAG, "AudioRecord.read() returned 0: count=$zeroReads")
                        }
                        Thread.yield()
                    }

                    else -> {
                        synchronized(stateLock) {
                            readErrorCount += 1L
                        }
                        errorCode = ERROR_AUDIO_RECORD_READ
                        errorMessage = readErrorMessage(samplesRead)
                        Log.e(TAG, errorMessage!!)
                        break
                    }
                }
            }
        } catch (exception: IllegalStateException) {
            if (!captureSession.stopRequested.get()) {
                synchronized(stateLock) {
                    readErrorCount += 1L
                }
                errorCode = ERROR_AUDIO_RECORD_STATE
                errorMessage =
                    "AudioRecord.read() failed because the recorder is in an invalid state: ${exception.message}"
                Log.e(TAG, errorMessage, exception)
            }
        } catch (exception: RuntimeException) {
            if (!captureSession.stopRequested.get()) {
                synchronized(stateLock) {
                    readErrorCount += 1L
                }
                errorCode = ERROR_AUDIO_RECORD_READ
                errorMessage = "AudioRecord.read() failed: ${exception.message}"
                Log.e(TAG, errorMessage, exception)
            }
        } finally {
            finishSession(captureSession, errorCode, notifyError = errorCode != null, errorMessage = errorMessage)
        }
    }

    private fun finishSession(
        captureSession: CaptureSession,
        errorCode: String?,
        notifyError: Boolean,
        errorMessage: String? = null,
    ) {
        val shouldNotifyError = notifyError && captureSession.errorReported.compareAndSet(false, true)
        val shouldNotifyStopped: Boolean

        synchronized(stateLock) {
            val belongsToSession = session === captureSession
            if (errorCode != null && belongsToSession) {
                state = STATE_ERROR
                microphoneErrorCount += 1
                lastError = "$errorCode: ${errorMessage ?: "Unknown microphone error"}"
            } else if (belongsToSession && state != STATE_ERROR) {
                state = STATE_STOPPED
            }

            updateDurationLocked()
            recording = false
            captureSession.stopRequested.set(true)
            if (belongsToSession) {
                session = null
                captureThread = null
            }

            shouldNotifyStopped = belongsToSession &&
                captureSession.stoppedNotified.compareAndSet(false, true)
        }

        // Stop capture and the PCM consumer, then stop/release Silero and
        // wake-word work and reset energy VAD before clearing PCM.
        // AudioEffectsManager preserves the verified AEC -> NS order;
        // AudioRecord is released only afterward.
        stopRecorder(captureSession.recorder)
        pcmPipeline.stopAndClear(::stopNativeDownstream)
        audioEffectsManager.releaseForAudioSession(captureSession.audioSessionId)
        releaseRecorder(captureSession)

        if (shouldNotifyError) {
            listener?.onError(getStatus())
        }
        if (shouldNotifyStopped) {
            listener?.onStopped(getStatus())
        }
    }

    private fun failStartLocked(
        errorCode: String,
        message: String,
        recorder: AudioRecord?,
        exception: Throwable? = null,
    ): OperationResult {
        state = if (errorCode == ERROR_PERMISSION_DENIED) STATE_PERMISSION_DENIED else STATE_ERROR
        microphoneErrorCount += 1
        lastError = "$errorCode: $message"
        recording = false
        session = null
        captureThread = null
        recorder?.let(::stopRecorder)
        pcmPipeline.stopAndClear(::stopNativeDownstream)
        audioEffectsManager.release()
        Log.e(TAG, message, exception)
        recorder?.let {
            try {
                it.release()
                Log.i(TAG, "AudioRecord.release() completed after start failure")
            } catch (releaseException: RuntimeException) {
                Log.e(TAG, "AudioRecord.release() failed after initialization error", releaseException)
            }
        }
        return OperationResult(false, errorCode, message)
    }

    private fun stopNativeDownstream() {
        sileroVadEngine.stopSession()
        wakeWordEngine.stopSession()
        vadEngine.stopSession()
    }

    private fun stopRecorder(recorder: AudioRecord) {
        try {
            if (recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                recorder.stop()
            }
        } catch (exception: IllegalStateException) {
            Log.w(TAG, "AudioRecord.stop() ignored invalid state", exception)
        } catch (exception: RuntimeException) {
            Log.e(TAG, "AudioRecord.stop() failed", exception)
        }
    }

    private fun releaseRecorder(captureSession: CaptureSession) {
        if (!captureSession.released.compareAndSet(false, true)) {
            return
        }

        try {
            captureSession.recorder.release()
            Log.i(TAG, "AudioRecord.release() completed")
        } catch (exception: RuntimeException) {
            synchronized(stateLock) {
                microphoneErrorCount += 1
                lastError = "$ERROR_AUDIO_RECORD_RELEASE: ${exception.message}"
                state = STATE_ERROR
            }
            Log.e(TAG, "AudioRecord.release() failed", exception)
        }
    }

    private fun recordInvalidRead(samplesRead: Int, bufferSizeSamples: Int) {
        val count = synchronized(stateLock) {
            invalidReadCount += 1L
            invalidReadCount
        }
        Log.e(
            TAG,
            "Invalid PCM read: samplesRead=$samplesRead, bufferSamples=$bufferSizeSamples, " +
                "channels=${config.channelCount}, invalidReadCount=$count",
        )
    }

    private fun readErrorMessage(errorCode: Int): String = when (errorCode) {
        AudioRecord.ERROR_DEAD_OBJECT ->
            "AudioRecord.read() returned ERROR_DEAD_OBJECT; microphone became unavailable."

        AudioRecord.ERROR_BAD_VALUE -> "AudioRecord.read() returned ERROR_BAD_VALUE."
        AudioRecord.ERROR_INVALID_OPERATION -> "AudioRecord.read() returned ERROR_INVALID_OPERATION."
        else -> "AudioRecord.read() returned unexpected error code $errorCode."
    }

    private fun updateDurationLocked() {
        if (captureStartedAtMs > 0L) {
            captureDurationMs = (SystemClock.elapsedRealtime() - captureStartedAtMs).coerceAtLeast(0L)
        }
    }

    private fun hasRecordAudioPermission(): Boolean =
        context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun safeBufferSizeBytes(minSizeBytes: Int, pcmSampleFrameBytes: Int): Int {
        val minimumSafeSize = max(
            minSizeBytes.toLong() * 2L,
            pcmSampleFrameBytes.toLong() * 2L,
        )
        val remainder = minimumSafeSize % pcmSampleFrameBytes.toLong()
        val alignedSize = if (remainder == 0L) {
            minimumSafeSize
        } else {
            minimumSafeSize + (pcmSampleFrameBytes - remainder).toLong()
        }
        return alignedSize.coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
    }
}
