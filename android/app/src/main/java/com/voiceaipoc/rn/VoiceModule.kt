package com.voiceaipoc.rn

import android.os.SystemClock
import android.util.Log
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.WritableMap
import com.facebook.react.module.annotations.ReactModule
import com.facebook.react.modules.core.DeviceEventManagerModule
import com.voiceaipoc.audio.AudioConfig
import com.voiceaipoc.audio.AudioEngine
import com.voiceaipoc.audio.AudioEffectsManager
import com.voiceaipoc.auth.SecureTokenStorage
import com.voiceaipoc.audio.AudioEngine.ManualWakeWordTrialStatus
import com.voiceaipoc.vad.VadEngine
import com.voiceaipoc.vad.silero.SileroVadEngine
import com.voiceaipoc.voice.VoiceWebSocketTransport
import com.voiceaipoc.wakeword.WakeWordEngine
import com.voiceaipoc.wakeword.WakeWordAcousticStatus
import com.voiceaipoc.wakeword.WakeWordCalibrationTrial
import com.voiceaipoc.wakeword.WakeWordDiagnosticCapture
import com.voiceaipoc.wakeword.WakeWordReplayBatchResult
import java.security.GeneralSecurityException
import java.util.concurrent.Executors

/**
 * React Native bridge for the native voice engine.
 *
 * Exposes microphone, platform-effect, PCM-pipeline, energy/Silero VAD, and
 * openWakeWord metadata plus low-frequency semantic transitions. PCM remains
 * inside native reusable buffers and is never sent over the bridge.
 */
@ReactModule(name = VoiceModule.NAME)
class VoiceModule(
    reactContext: ReactApplicationContext,
) : ReactContextBaseJavaModule(reactContext) {

    private val diagnosticExecutor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "VoiceAI-WakeReplay")
    }

    private val authTokenStorage = SecureTokenStorage(reactContext.applicationContext)

    private val voiceGateway = VoiceWebSocketTransport(
        tokenStorage = authTokenStorage,
        listener = object : VoiceWebSocketTransport.Listener {
            override fun onStatus(status: VoiceWebSocketTransport.Status) {
                emitVoiceGatewayStatus(status)
            }

            override fun onServerEvent(
                eventType: String,
                sessionId: String?,
                turnId: String?,
                responseId: String?,
            ) {
                Log.i(
                    TAG,
                    "VOICE server event type=$eventType sessionId=${sessionId ?: "NONE"} " +
                        "turnId=${turnId ?: "NONE"} responseId=${responseId ?: "NONE"} " +
                        "wallMs=${System.currentTimeMillis()}",
                )
                emitVoiceGatewayEvent(eventType, sessionId, turnId, responseId)
            }
        },
    )

    private val audioEngine = AudioEngine(
        context = reactContext.applicationContext,
        config = AudioConfig(),
        pcmDataCallback = AudioEngine.PcmDataCallback { buffer, samplesRead ->
            // The transport copies the reusable frame immediately. PCM stays
            // native and is never sent through the React Native bridge.
            voiceGateway.offerPcmFrame(buffer, samplesRead)
        },
        vadEventListener = object : VadEngine.Listener {
            override fun onSpeechStarted(event: VadEngine.Event) {
                Log.i(
                    TAG,
                    "VAD speech started timestampMs=${event.timestampMs} " +
                        "frameIndex=${event.frameIndex} wallMs=${System.currentTimeMillis()} " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                emitVadEvent(EVENT_VAD_SPEECH_STARTED, event)
            }

            override fun onSpeechStopped(event: VadEngine.Event) {
                Log.i(
                    TAG,
                    "VAD speech stopped timestampMs=${event.timestampMs} " +
                        "frameIndex=${event.frameIndex} reason=${event.reason} " +
                        "wallMs=${System.currentTimeMillis()} " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                emitVadEvent(EVENT_VAD_SPEECH_STOPPED, event)
            }
        },
        sileroVadEventListener = object : SileroVadEngine.Listener {
            override fun onEngineStarted(status: SileroVadEngine.Status) = Unit

            override fun onEngineStopped(status: SileroVadEngine.Status) = Unit

            override fun onSpeechStarted(event: SileroVadEngine.Event) {
                Log.i(
                    TAG,
                    "SILERO VAD speech started timestampMs=${event.timestampMs} " +
                        "inferenceIndex=${event.inferenceIndex} wallMs=${System.currentTimeMillis()} " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                emitSileroVadEvent(EVENT_SILERO_VAD_SPEECH_STARTED, event)
            }

            override fun onSpeechStopped(event: SileroVadEngine.Event) {
                Log.i(
                    TAG,
                    "SILERO VAD speech stopped timestampMs=${event.timestampMs} " +
                        "inferenceIndex=${event.inferenceIndex} reason=${event.reason} " +
                        "wallMs=${System.currentTimeMillis()} " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                emitSileroVadEvent(EVENT_SILERO_VAD_SPEECH_STOPPED, event)
            }

            override fun onEngineError(status: SileroVadEngine.Status) {
                emitSileroVadError(status)
            }
        },
        wakeWordEventListener = object : WakeWordEngine.Listener {
            override fun onEngineStarted(status: WakeWordEngine.Status) {
                emitWakeStatusEvent(EVENT_WAKE_ENGINE_STARTED, status)
            }

            override fun onEngineStopped(status: WakeWordEngine.Status) {
                emitWakeStatusEvent(EVENT_WAKE_ENGINE_STOPPED, status)
            }

            override fun onWakeWordDetected(event: WakeWordEngine.DetectionEvent) {
                emitWakeDetectionEvent(event)
            }

            override fun onEngineError(status: WakeWordEngine.Status) {
                emitWakeStatusEvent(EVENT_WAKE_ENGINE_ERROR, status)
            }
        },
        listener = object : AudioEngine.Listener {
            override fun onStarted(status: AudioEngine.Status) {
                emitEvent(EVENT_AUDIO_ENGINE_STARTED, status)
            }

            override fun onStopped(status: AudioEngine.Status) {
                emitEvent(EVENT_AUDIO_ENGINE_STOPPED, status)
            }

            override fun onError(status: AudioEngine.Status) {
                emitEvent(EVENT_AUDIO_ENGINE_ERROR, status)
            }
        },
    )

    override fun getName(): String = NAME

    @ReactMethod
    fun getDiagnostics(promise: Promise) {
        val diagnostics = Arguments.createMap().apply {
            putString("nativeVoiceEngine", "NOT INITIALIZED")
            putString("audioCapture", "NATIVE_AUDIORECORD_AVAILABLE")
            putString(
                "wakeWord",
                if (audioEngine.getWakeWordStatus().available) {
                    "OPENWAKEWORD_AVAILABLE"
                } else {
                    "OPENWAKEWORD_INTEGRATED_UNAVAILABLE"
                },
            )
            putString("vad", "NATIVE_ENERGY_VAD_AVAILABLE_SILERO_INTEGRATED")
        }

        promise.resolve(diagnostics)
    }

    @ReactMethod
    fun storeAuthTokens(accessToken: String, refreshToken: String, promise: Promise) {
        try {
            authTokenStorage.save(
                accessToken,
                refreshToken,
            )
            promise.resolve(true)
        } catch (exception: IllegalArgumentException) {
            promise.reject("E_AUTH_TOKEN_STORAGE", exception.message, exception)
        } catch (exception: GeneralSecurityException) {
            promise.reject("E_AUTH_TOKEN_STORAGE", exception.message, exception)
        }
    }

    @ReactMethod
    fun clearAuthTokens(promise: Promise) {
        authTokenStorage.clear()
        promise.resolve(true)
    }

    @ReactMethod
    fun connectVoiceGateway(url: String, promise: Promise) {
        resolveVoiceResult(voiceGateway.connect(url), promise)
    }

    @ReactMethod
    fun disconnectVoiceGateway(promise: Promise) {
        voiceGateway.disconnect()
        promise.resolve(toWritableVoiceGatewayMap(voiceGateway.getStatus()))
    }

    @ReactMethod
    fun startVoiceSession(resumeSessionId: String?, promise: Promise) {
        resolveVoiceResult(voiceGateway.startSession(resumeSessionId), promise)
    }

    @ReactMethod
    fun startVoiceTurn(clientTurnId: String?, promise: Promise) {
        resolveVoiceResult(voiceGateway.startTurn(clientTurnId), promise)
    }

    @ReactMethod
    fun commitVoiceAudio(durationMs: Int, promise: Promise) {
        resolveVoiceResult(voiceGateway.commitAudio(durationMs), promise)
    }

    @ReactMethod
    fun cancelVoiceResponse(reason: String?, promise: Promise) {
        resolveVoiceResult(voiceGateway.cancelResponse(reason ?: "client_requested"), promise)
    }

    @ReactMethod
    fun endVoiceSession(reason: String?, promise: Promise) {
        resolveVoiceResult(voiceGateway.endSession(reason ?: "client_requested"), promise)
    }

    @ReactMethod
    fun getVoiceGatewayStatus(promise: Promise) {
        promise.resolve(toWritableVoiceGatewayMap(voiceGateway.getStatus()))
    }

    @ReactMethod
    fun startMicrophone(promise: Promise) {
        val result = audioEngine.startRecording()
        if (result.succeeded) {
            promise.resolve(toWritableMap(audioEngine.getStatus()))
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
    }

    @ReactMethod
    fun stopMicrophone(promise: Promise) {
        val result = audioEngine.stopRecording()
        if (result.succeeded) {
            promise.resolve(toWritableMap(audioEngine.getStatus()))
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
    }

    @ReactMethod
    fun getMicrophoneStatus(promise: Promise) {
        promise.resolve(toWritableMap(audioEngine.getStatus()))
    }

    @ReactMethod
    fun getAudioProcessingStatus(promise: Promise) {
        promise.resolve(toWritableAudioProcessingMap(audioEngine.getAudioProcessingStatus()))
    }

    @ReactMethod
    fun getAudioPipelineStatus(promise: Promise) {
        promise.resolve(toWritableAudioPipelineMap(audioEngine.getAudioPipelineStatus()))
    }

    @ReactMethod
    fun getWakeWordStatus(promise: Promise) {
        promise.resolve(toWritableWakeWordMap(audioEngine.getWakeWordStatus()))
    }

    @ReactMethod
    fun getManualWakeWordTrialStatus(promise: Promise) {
        promise.resolve(
            toWritableManualWakeWordTrialMap(audioEngine.getManualWakeWordTrialStatus()),
        )
    }

    @ReactMethod
    fun setWakeWordCalibrationMode(enabled: Boolean, promise: Promise) {
        val result = audioEngine.setWakeWordCalibrationMode(enabled)
        if (result.succeeded) {
            promise.resolve(toWritableWakeWordMap(audioEngine.getWakeWordStatus()))
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
    }

    @ReactMethod
    fun beginWakeWordCalibrationTrial(
        expectedPositive: Boolean,
        condition: String,
        promise: Promise,
    ) {
        val result = audioEngine.beginWakeWordCalibrationTrial(expectedPositive, condition)
        if (result.succeeded) {
            promise.resolve(toWritableWakeWordMap(audioEngine.getWakeWordStatus()))
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
    }

    @ReactMethod
    fun resetWakeWordAcousticDiagnostics(promise: Promise) {
        audioEngine.resetWakeWordAcousticDiagnostics()
        promise.resolve(toWritableWakeWordMap(audioEngine.getWakeWordStatus()))
    }

    @ReactMethod
    fun startWakeWordDiagnosticPcmCapture(
        label: String,
        durationMs: Int,
        promise: Promise,
    ) {
        val result = audioEngine.startWakeWordDiagnosticPcmCapture(label, durationMs)
        if (result.succeeded) {
            promise.resolve(
                toWritableDiagnosticCaptureMap(
                    audioEngine.getWakeWordDiagnosticPcmCaptureStatus(),
                ),
            )
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
    }

    @ReactMethod
    fun stopWakeWordDiagnosticPcmCapture(promise: Promise) {
        promise.resolve(
            toWritableDiagnosticCaptureMap(audioEngine.stopWakeWordDiagnosticPcmCapture()),
        )
    }

    @ReactMethod
    fun getWakeWordDiagnosticPcmCaptureStatus(promise: Promise) {
        promise.resolve(
            toWritableDiagnosticCaptureMap(
                audioEngine.getWakeWordDiagnosticPcmCaptureStatus(),
            ),
        )
    }

    @ReactMethod
    fun replayWakeWordDiagnosticPcm(repetitions: Int, promise: Promise) {
        diagnosticExecutor.execute {
            try {
                promise.resolve(
                    toWritableReplayBatchMap(
                        audioEngine.replayWakeWordDiagnosticPcm(repetitions),
                    ),
                )
            } catch (exception: Exception) {
                promise.reject(
                    "E_WAKE_DIAGNOSTIC_REPLAY",
                    exception.message ?: "Wake-word diagnostic replay failed.",
                    exception,
                )
            }
        }
    }

    @ReactMethod
    fun deleteWakeWordDiagnosticData(promise: Promise) {
        try {
            val deleted = audioEngine.deleteWakeWordDiagnosticData()
            promise.resolve(
                Arguments.createMap().apply {
                    putBoolean("diagnosticOnly", true)
                    putInt("deletedFileCount", deleted)
                },
            )
        } catch (exception: IllegalStateException) {
            promise.reject(
                "E_WAKE_DIAGNOSTIC_DELETE",
                exception.message,
                exception,
            )
        }
    }

    @ReactMethod
    fun setAudioProcessingCalibrationMode(mode: String, promise: Promise) {
        val requestedEffects = when (mode) {
            "AEC_NS" -> true to true
            "AEC_ONLY" -> true to false
            "NS_ONLY" -> false to true
            "DISABLED" -> false to false
            else -> {
                promise.reject(
                    "E_AUDIO_CALIBRATION_MODE",
                    "Unknown audio calibration mode: $mode",
                )
                return
            }
        }
        val result = audioEngine.setAudioProcessingCalibrationMode(
            enableAcousticEchoCancellation = requestedEffects.first,
            enableNoiseSuppression = requestedEffects.second,
        )
        if (result.succeeded) {
            promise.resolve(toWritableAudioProcessingMap(audioEngine.getAudioProcessingStatus()))
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
    }

    override fun invalidate() {
        audioEngine.release()
        voiceGateway.shutdown()
        diagnosticExecutor.shutdownNow()
        super.invalidate()
    }

    private fun toWritableDiagnosticCaptureMap(
        status: WakeWordDiagnosticCapture.Status,
    ): WritableMap = Arguments.createMap().apply {
        putBoolean("diagnosticOnly", status.diagnosticOnly)
        putBoolean("active", status.active)
        if (status.captureId == null) putNull("captureId") else putString(
            "captureId",
            status.captureId,
        )
        if (status.label == null) putNull("label") else putString("label", status.label)
        putInt("targetDurationMs", status.targetDurationMs)
        putInt("targetInferenceWindows", status.targetInferenceWindows)
        putInt("inferenceWindowsAccepted", status.inferenceWindowsAccepted)
        putInt("inferenceWindowsWritten", status.inferenceWindowsWritten)
        putInt("queueDepthWindows", status.queueDepthWindows)
        putInt("queueCapacityWindows", status.queueCapacityWindows)
        putInt("queueHighWaterMarkWindows", status.queueHighWaterMarkWindows)
        putDouble("droppedWindows", status.droppedWindows.toDouble())
        putInt("completedCaptureCount", status.completedCaptureCount)
        if (status.lastError == null) putNull("lastError") else putString(
            "lastError",
            status.lastError,
        )
        val recordArray = Arguments.createArray()
        status.records.forEach { record ->
            recordArray.pushMap(
                Arguments.createMap().apply {
                    putString("captureId", record.captureId)
                    putString("label", record.label)
                    putString("fileName", record.fileName)
                    putDouble("startedAtTimestampMs", record.startedAtTimestampMs.toDouble())
                    putDouble("completedAtTimestampMs", record.completedAtTimestampMs.toDouble())
                    putInt("durationMs", record.durationMs)
                    putInt("inferenceWindowsWritten", record.inferenceWindowsWritten)
                    putDouble("samplesWritten", record.samplesWritten.toDouble())
                    putDouble("bytesWritten", record.bytesWritten.toDouble())
                    putDouble("droppedWindows", record.droppedWindows.toDouble())
                    if (record.sha256 == null) putNull("sha256") else putString(
                        "sha256",
                        record.sha256,
                    )
                    putBoolean("valid", record.valid)
                    if (record.error == null) putNull("error") else putString(
                        "error",
                        record.error,
                    )
                },
            )
        }
        putArray("records", recordArray)
    }

    private fun toWritableReplayBatchMap(
        result: WakeWordReplayBatchResult,
    ): WritableMap = Arguments.createMap().apply {
        putBoolean("diagnosticOnly", result.diagnosticOnly)
        putString("runtimeName", result.runtimeName)
        putString("runtimeVersion", result.runtimeVersion)
        putInt("repetitionCount", result.repetitionCount)
        putInt("captureCount", result.captureCount)
        putInt("replayCount", result.replayCount)
        val replayArray = Arguments.createArray()
        result.results.forEach { replay ->
            replayArray.pushMap(
                Arguments.createMap().apply {
                    putString("captureId", replay.captureId)
                    putString("pcmFileName", replay.pcmFileName)
                    putString("pcmSha256", replay.pcmSha256)
                    putInt("repetition", replay.repetition)
                    putString("traceFileName", replay.traceFileName)
                    putInt("inferenceCount", replay.inferenceCount)
                    putDouble(
                        "maximumEffectiveScore",
                        replay.maximumEffectiveScore.toDouble(),
                    )
                    putDouble("maximumRawScore", replay.maximumRawScore.toDouble())
                    putInt("runtimeErrorCount", replay.runtimeErrorCount)
                    putDouble("elapsedMs", replay.elapsedMs)
                },
            )
        }
        putArray("results", replayArray)
    }

    private fun emitEvent(eventName: String, status: AudioEngine.Status) {
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(eventName, toWritableMap(status))
    }

    private fun emitVoiceGatewayStatus(status: VoiceWebSocketTransport.Status) {
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(EVENT_VOICE_GATEWAY_STATUS, toWritableVoiceGatewayMap(status))
    }

    private fun emitVoiceGatewayEvent(
        eventType: String,
        sessionId: String?,
        turnId: String?,
        responseId: String?,
    ) {
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        val payload = Arguments.createMap().apply {
            putString("event", eventType)
            if (sessionId == null) putNull("sessionId") else putString("sessionId", sessionId)
            if (turnId == null) putNull("turnId") else putString("turnId", turnId)
            if (responseId == null) putNull("responseId") else putString("responseId", responseId)
        }
        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(EVENT_VOICE_GATEWAY_EVENT, payload)
    }

    private fun resolveVoiceResult(result: VoiceWebSocketTransport.Result, promise: Promise) {
        if (result.succeeded) {
            promise.resolve(toWritableVoiceGatewayMap(voiceGateway.getStatus()))
        } else {
            promise.reject(
                result.errorCode ?: "E_VOICE_GATEWAY",
                result.errorMessage ?: "Voice gateway operation failed.",
            )
        }
    }

    private fun emitVadEvent(eventName: String, event: VadEngine.Event) {
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        val payload = Arguments.createMap().apply {
            putString("event", event.event)
            putDouble("timestampMs", event.timestampMs.toDouble())
            putDouble("frameIndex", event.frameIndex.toDouble())
            putDouble("energyDbFs", event.energyDbFs)
            putDouble("speechDurationMs", event.speechDurationMs.toDouble())
            putDouble("speechSegmentCount", event.speechSegmentCount.toDouble())
            putString("reason", event.reason)
        }
        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(eventName, payload)
    }

    private fun emitWakeStatusEvent(eventName: String, status: WakeWordEngine.Status) {
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(eventName, toWritableWakeWordMap(status).apply { putString("event", eventName) })
    }

    private fun emitSileroVadEvent(eventName: String, event: SileroVadEngine.Event) {
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        val payload = Arguments.createMap().apply {
            putString("event", event.event)
            putDouble("timestampMs", event.timestampMs.toDouble())
            putDouble("probability", event.probability.toDouble())
            putDouble("inferenceIndex", event.inferenceIndex.toDouble())
            putDouble("speechDurationMs", event.speechDurationMs.toDouble())
            putString("reason", event.reason)
        }
        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(eventName, payload)
    }

    private fun emitSileroVadError(status: SileroVadEngine.Status) {
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(
                EVENT_SILERO_VAD_ERROR,
                toWritableSileroVadMap(status).apply {
                    putString("event", EVENT_SILERO_VAD_ERROR)
                    putDouble("timestampMs", System.currentTimeMillis().toDouble())
                },
            )
    }

    private fun emitWakeDetectionEvent(event: WakeWordEngine.DetectionEvent) {
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        val payload = Arguments.createMap().apply {
            putString("event", event.event)
            putDouble("timestampMs", event.timestampMs.toDouble())
            putString("modelName", event.modelName)
            putDouble("confidence", event.confidence.toDouble())
            putDouble("detectionCount", event.detectionCount.toDouble())
            putDouble("inferenceIndex", event.inferenceIndex.toDouble())
            putDouble("framesConsumed", event.framesConsumed.toDouble())
            putInt("queueDepthFrames", event.queueDepthFrames)
            putDouble("droppedFrameCount", event.droppedFrameCount.toDouble())
        }
        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(EVENT_WAKE_WORD_DETECTED, payload)
    }

    private fun toWritableMap(status: AudioEngine.Status): WritableMap = Arguments.createMap().apply {
        putBoolean("permissionGranted", status.permissionGranted)
        putString("permissionStatus", if (status.permissionGranted) "GRANTED" else "DENIED")
        putString("state", status.state)
        putBoolean("isRecording", status.state == "RECORDING")
        putBoolean("audioRecordInitialized", status.audioRecordInitialized)
        putInt("sampleRateHz", status.sampleRateHz)
        putInt("channelCount", status.channelCount)
        putString("encoding", status.encoding)
        putInt("bufferSizeBytes", status.bufferSizeBytes)
        putInt("minBufferSizeBytes", status.minBufferSizeBytes)
        putInt("audioSessionId", status.audioSessionId)
        putDouble("pcmFramesCaptured", status.pcmFramesCaptured.toDouble())
        putDouble("captureDurationMs", status.captureDurationMs.toDouble())
        putInt("microphoneErrorCount", status.microphoneErrorCount)
        if (status.lastError == null) {
            putNull("lastError")
        } else {
            putString("lastError", status.lastError)
        }
    }

    private fun toWritableVoiceGatewayMap(
        status: VoiceWebSocketTransport.Status,
    ): WritableMap = Arguments.createMap().apply {
        putString("state", status.state.name)
        putBoolean("connected", status.connected)
        putBoolean("sessionStarted", status.sessionStarted)
        putBoolean("turnActive", status.turnActive)
        if (status.sessionId == null) putNull("sessionId") else putString("sessionId", status.sessionId)
        if (status.turnId == null) putNull("turnId") else putString("turnId", status.turnId)
        if (status.responseId == null) putNull("responseId") else putString("responseId", status.responseId)
        putInt("framesQueued", status.framesQueued)
        putInt("queueHighWaterMark", status.queueHighWaterMark)
        putDouble("droppedFrames", status.droppedFrames.toDouble())
        putDouble("invalidFrames", status.invalidFrames.toDouble())
        putDouble("framesSent", status.framesSent.toDouble())
        putDouble("bytesSent", status.bytesSent.toDouble())
        putDouble("websocketErrorCount", status.websocketErrorCount.toDouble())
        if (status.lastServerEvent == null) {
            putNull("lastServerEvent")
        } else {
            putString("lastServerEvent", status.lastServerEvent)
        }
        putDouble("lastServerEventTimestampMs", status.lastServerEventTimestampMs.toDouble())
        if (status.lastError == null) putNull("lastError") else putString("lastError", status.lastError)
    }

    private fun toWritableAudioProcessingMap(
        status: AudioEffectsManager.Status,
    ): WritableMap = Arguments.createMap().apply {
        putInt("audioSessionId", status.audioSessionId)
        putMap("aec", toWritableEffectMap(status.aec))
        putMap("noiseSuppression", toWritableEffectMap(status.noiseSuppression))
        putString("manufacturer", status.manufacturer)
        putString("model", status.model)
        putInt("androidSdk", status.androidSdk)
    }

    private fun toWritableEffectMap(
        status: AudioEffectsManager.EffectStatus,
    ): WritableMap = Arguments.createMap().apply {
        putBoolean("supported", status.supported)
        putBoolean("available", status.available)
        putBoolean("requested", status.requested)
        putBoolean("created", status.created)
        putBoolean("enabled", status.enabled)
        if (status.lastError == null) {
            putNull("lastError")
        } else {
            putString("lastError", status.lastError)
        }
    }

    private fun toWritableAudioPipelineMap(
        status: AudioEngine.AudioPipelineStatus,
    ): WritableMap = Arguments.createMap().apply {
        putBoolean("recording", status.recording)
        putString("state", status.state)
        putBoolean("captureStarted", status.captureStarted)
        putBoolean("captureStopped", status.captureStopped)
        putInt("sampleRateHz", status.sampleRateHz)
        putInt("channelCount", status.channelCount)
        putString("pcmFormat", status.pcmFormat)
        putInt("frameDurationMs", status.frameDurationMs)
        putInt("frameSizeSamples", status.frameSizeSamples)
        putInt("frameSizeBytes", status.frameSizeBytes)
        putInt("bufferedFrames", status.bufferedFrames)
        putInt("bufferedBytes", status.bufferedBytes)
        putInt("bufferCapacityFrames", status.bufferCapacityFrames)
        putInt("bufferCapacityBytes", status.bufferCapacityBytes)
        putInt("maxBufferedDurationMs", status.maxBufferedDurationMs)
        putInt("maxObservedBufferedFrames", status.maxObservedBufferedFrames)
        putDouble("totalPcmFramesCaptured", status.totalPcmFramesCaptured.toDouble())
        putDouble("totalPcmBytesProcessed", status.totalPcmBytesProcessed.toDouble())
        putDouble("framesWrittenToRingBuffer", status.framesWrittenToRingBuffer.toDouble())
        putDouble("framesConsumedFromRingBuffer", status.framesConsumedFromRingBuffer.toDouble())
        putDouble("totalFramesProcessed", status.totalFramesProcessed.toDouble())
        putDouble("overflowCount", status.overflowCount.toDouble())
        putDouble("invalidReadCount", status.invalidReadCount.toDouble())
        putDouble("readErrorCount", status.readErrorCount.toDouble())
        putDouble("pipelineErrorCount", status.pipelineErrorCount.toDouble())
        putInt("partialFrameSamples", status.partialFrameSamples)
        putMap("vad", toWritableVadMap(status.vad))
        putMap("sileroVad", toWritableSileroVadMap(status.sileroVad))
    }

    private fun toWritableVadMap(status: VadEngine.Status): WritableMap =
        Arguments.createMap().apply {
            putBoolean("enabled", status.enabled)
            putBoolean("sessionActive", status.sessionActive)
            putString("state", status.state)
            putDouble("thresholdDbFs", status.thresholdDbFs)
            putDouble("lastEnergyDbFs", status.lastEnergyDbFs)
            putString("lastFrameClassification", status.lastFrameClassification)
            putInt("frameDurationMs", status.frameDurationMs)
            putInt("frameSizeSamples", status.frameSizeSamples)
            putInt("minimumSpeechDurationMs", status.minimumSpeechDurationMs)
            putInt("minimumSilenceDurationMs", status.minimumSilenceDurationMs)
            putInt(
                "configuredSpeechStartConfirmationFrames",
                status.configuredSpeechStartConfirmationFrames,
            )
            putInt(
                "configuredSpeechEndConfirmationFrames",
                status.configuredSpeechEndConfirmationFrames,
            )
            putInt(
                "effectiveSpeechStartConfirmationFrames",
                status.effectiveSpeechStartConfirmationFrames,
            )
            putInt(
                "effectiveSpeechEndConfirmationFrames",
                status.effectiveSpeechEndConfirmationFrames,
            )
            putInt("consecutiveSpeechFrames", status.consecutiveSpeechFrames)
            putInt("consecutiveSilenceFrames", status.consecutiveSilenceFrames)
            putDouble("vadFramesProcessed", status.vadFramesProcessed.toDouble())
            putDouble("speechFrames", status.speechFrames.toDouble())
            putDouble("nonSpeechFrames", status.nonSpeechFrames.toDouble())
            putDouble("speechSegments", status.speechSegments.toDouble())
            putDouble("speechStartCount", status.speechStartCount.toDouble())
            putDouble("speechStopCount", status.speechStopCount.toDouble())
            putDouble("currentSpeechDurationMs", status.currentSpeechDurationMs.toDouble())
            putDouble("currentSilenceDurationMs", status.currentSilenceDurationMs.toDouble())
            putDouble(
                "lastSpeechStartedFrameIndex",
                status.lastSpeechStartedFrameIndex.toDouble(),
            )
            putDouble(
                "lastSpeechStoppedFrameIndex",
                status.lastSpeechStoppedFrameIndex.toDouble(),
            )
            putDouble("vadErrorCount", status.vadErrorCount.toDouble())
        }

    private fun toWritableManualWakeWordTrialMap(
        status: ManualWakeWordTrialStatus,
    ): WritableMap = Arguments.createMap().apply {
        val trial = status.wake
        putBoolean("active", trial.active)
        if (trial.trialId == null) putNull("trialId") else putString("trialId", trial.trialId)
        putInt("microphoneSessionId", trial.microphoneSessionId)
        putDouble("startTimestampMs", trial.startTimestampMs.toDouble())
        putDouble("stopTimestampMs", trial.stopTimestampMs.toDouble())
        putDouble("wakeDetectionCount", trial.wakeDetectionCount.toDouble())
        putDouble("inferenceWindowCount", trial.inferenceWindowCount.toDouble())
        putDouble("aboveThresholdWindowCount", trial.aboveThresholdWindowCount.toDouble())
        if (trial.maximumScore == null) putNull("maximumScore") else {
            putDouble("maximumScore", trial.maximumScore.toDouble())
        }
        putDouble("maximumScoreTimestampMs", trial.maximumScoreTimestampMs.toDouble())
        putDouble("lastDetectionTimestampMs", trial.lastDetectionTimestampMs.toDouble())
        if (trial.lastDetectionIntervalMs == null) putNull("lastDetectionIntervalMs") else {
            putDouble("lastDetectionIntervalMs", trial.lastDetectionIntervalMs.toDouble())
        }
        putString("currentWakeState", trial.currentWakeState)
        putBoolean("cooldownActive", trial.cooldownActive)
        putDouble("cooldownRemainingMs", trial.cooldownRemainingMs.toDouble())
        putDouble("cooldownDurationMs", trial.cooldownDurationMs.toDouble())
        putInt("queueDepthFrames", trial.queueDepthFrames)
        putInt("queueHighWaterMarkFrames", trial.queueHighWaterMarkFrames)
        putDouble("queueDrops", trial.queueDrops.toDouble())
        putDouble("runtimeErrors", trial.runtimeErrors.toDouble())
        putDouble("workerGeneration", trial.workerGeneration.toDouble())
        putBoolean("aecEnabled", status.aecEnabled)
        putBoolean("noiseSuppressionEnabled", status.noiseSuppressionEnabled)
        putDouble("pcmOverflowCount", status.pcmOverflowCount.toDouble())
        putDouble("wakeWorkerDropCount", status.wakeWorkerDropCount.toDouble())
        putInt("audioRecordErrorCount", status.audioRecordErrorCount)
        putDouble("audioRecordReadErrorCount", status.audioRecordReadErrorCount.toDouble())
        putDouble("pcmPipelineErrorCount", status.pcmPipelineErrorCount.toDouble())
        putDouble("wakeRuntimeErrorCount", status.wakeRuntimeErrorCount.toDouble())
        putDouble("sileroRuntimeErrorCount", status.sileroRuntimeErrorCount.toDouble())
        putString("energyVadState", status.energyVadState)
        putString("sileroVadState", status.sileroVadState)
        putDouble("energyVadSpeechStartCount", status.energyVadSpeechStartCount.toDouble())
        putDouble("energyVadSpeechStopCount", status.energyVadSpeechStopCount.toDouble())
        putDouble("sileroVadSpeechStartCount", status.sileroVadSpeechStartCount.toDouble())
        putDouble("sileroVadSpeechStopCount", status.sileroVadSpeechStopCount.toDouble())

        val detections = Arguments.createArray()
        trial.detections.forEach { detection ->
            detections.pushMap(Arguments.createMap().apply {
                putDouble("detectionSequenceNumber", detection.detectionSequenceNumber.toDouble())
                putDouble("classifierScore", detection.classifierScore.toDouble())
                putDouble("inferenceWindowSequence", detection.inferenceWindowSequence.toDouble())
                putDouble("inferenceTimestampMs", detection.inferenceTimestampMs.toDouble())
                putString("wakeStateBefore", detection.wakeStateBefore)
                putString("wakeStateAfter", detection.wakeStateAfter)
                putDouble("cooldownRemainingMs", detection.cooldownRemainingMs.toDouble())
                if (detection.millisecondsSincePreviousDetection == null) {
                    putNull("millisecondsSincePreviousDetection")
                } else {
                    putDouble(
                        "millisecondsSincePreviousDetection",
                        detection.millisecondsSincePreviousDetection.toDouble(),
                    )
                }
                putDouble("workerGeneration", detection.workerGeneration.toDouble())
            })
        }
        putArray("detections", detections)

        val thresholdCrossings = Arguments.createArray()
        trial.thresholdCrossings.forEach { crossing ->
            thresholdCrossings.pushMap(Arguments.createMap().apply {
                putDouble("inferenceWindowSequence", crossing.inferenceWindowSequence.toDouble())
                putDouble("inferenceTimestampMs", crossing.inferenceTimestampMs.toDouble())
                putDouble("score", crossing.score.toDouble())
                putString("wakeStateBefore", crossing.wakeStateBefore)
                putString("wakeStateAfter", crossing.wakeStateAfter)
                putDouble("cooldownRemainingMs", crossing.cooldownRemainingMs.toDouble())
                putBoolean("generatedWakeEvent", crossing.generatedWakeEvent)
                putBoolean("suppressedByCooldown", crossing.suppressedByCooldown)
            })
        }
        putArray("thresholdCrossings", thresholdCrossings)

        val history = Arguments.createArray()
        status.history.forEach { record ->
            history.pushMap(toWritableManualWakeWordTrialRecord(record))
        }
        putArray("history", history)
    }

    private fun toWritableManualWakeWordTrialRecord(
        trial: com.voiceaipoc.wakeword.WakeWordManualTrialStatus,
    ): WritableMap = Arguments.createMap().apply {
        putBoolean("active", false)
        if (trial.trialId == null) putNull("trialId") else putString("trialId", trial.trialId)
        putInt("microphoneSessionId", trial.microphoneSessionId)
        putDouble("startTimestampMs", trial.startTimestampMs.toDouble())
        putDouble("stopTimestampMs", trial.stopTimestampMs.toDouble())
        putDouble("wakeDetectionCount", trial.wakeDetectionCount.toDouble())
        putDouble("inferenceWindowCount", trial.inferenceWindowCount.toDouble())
        putDouble("aboveThresholdWindowCount", trial.aboveThresholdWindowCount.toDouble())
        if (trial.maximumScore == null) putNull("maximumScore") else {
            putDouble("maximumScore", trial.maximumScore.toDouble())
        }
        putDouble("maximumScoreTimestampMs", trial.maximumScoreTimestampMs.toDouble())
        putDouble("lastDetectionTimestampMs", trial.lastDetectionTimestampMs.toDouble())
        if (trial.lastDetectionIntervalMs == null) putNull("lastDetectionIntervalMs") else {
            putDouble("lastDetectionIntervalMs", trial.lastDetectionIntervalMs.toDouble())
        }
        putString("currentWakeState", trial.currentWakeState)
        putBoolean("cooldownActive", trial.cooldownActive)
        putDouble("cooldownRemainingMs", trial.cooldownRemainingMs.toDouble())
        putDouble("cooldownDurationMs", trial.cooldownDurationMs.toDouble())
        putInt("queueDepthFrames", trial.queueDepthFrames)
        putInt("queueHighWaterMarkFrames", trial.queueHighWaterMarkFrames)
        putDouble("queueDrops", trial.queueDrops.toDouble())
        putDouble("runtimeErrors", trial.runtimeErrors.toDouble())
        putDouble("workerGeneration", trial.workerGeneration.toDouble())
        val detections = Arguments.createArray()
        trial.detections.forEach { detection ->
            detections.pushMap(Arguments.createMap().apply {
                putDouble("detectionSequenceNumber", detection.detectionSequenceNumber.toDouble())
                putDouble("classifierScore", detection.classifierScore.toDouble())
                putDouble("inferenceWindowSequence", detection.inferenceWindowSequence.toDouble())
                putDouble("inferenceTimestampMs", detection.inferenceTimestampMs.toDouble())
                putString("wakeStateBefore", detection.wakeStateBefore)
                putString("wakeStateAfter", detection.wakeStateAfter)
                putDouble("cooldownRemainingMs", detection.cooldownRemainingMs.toDouble())
                if (detection.millisecondsSincePreviousDetection == null) {
                    putNull("millisecondsSincePreviousDetection")
                } else {
                    putDouble(
                        "millisecondsSincePreviousDetection",
                        detection.millisecondsSincePreviousDetection.toDouble(),
                    )
                }
                putDouble("workerGeneration", detection.workerGeneration.toDouble())
            })
        }
        putArray("detections", detections)
        val thresholdCrossings = Arguments.createArray()
        trial.thresholdCrossings.forEach { crossing ->
            thresholdCrossings.pushMap(Arguments.createMap().apply {
                putDouble("inferenceWindowSequence", crossing.inferenceWindowSequence.toDouble())
                putDouble("inferenceTimestampMs", crossing.inferenceTimestampMs.toDouble())
                putDouble("score", crossing.score.toDouble())
                putString("wakeStateBefore", crossing.wakeStateBefore)
                putString("wakeStateAfter", crossing.wakeStateAfter)
                putDouble("cooldownRemainingMs", crossing.cooldownRemainingMs.toDouble())
                putBoolean("generatedWakeEvent", crossing.generatedWakeEvent)
                putBoolean("suppressedByCooldown", crossing.suppressedByCooldown)
            })
        }
        putArray("thresholdCrossings", thresholdCrossings)
    }

    private fun toWritableWakeWordMap(status: WakeWordEngine.Status): WritableMap =
        Arguments.createMap().apply {
            putBoolean("enabled", status.enabled)
            putBoolean("available", status.available)
            putBoolean("modelPresent", status.modelPresent)
            putString("modelName", status.modelName)
            putString("modelVersion", status.modelVersion)
            putString("modelReleaseTag", status.modelReleaseTag)
            putString("modelGitCommit", status.modelGitCommit)
            putString("modelLicense", status.modelLicense)
            putString("modelFormat", status.modelFormat)
            putString("modelAssetDirectory", status.modelAssetDirectory)
            putString("missingModelAssets", status.missingModelAssets)
            putBoolean("modelHashVerified", status.modelHashVerified)
            if (status.classifierSha256 == null) {
                putNull("classifierSha256")
            } else {
                putString("classifierSha256", status.classifierSha256)
            }
            putString("runtimeName", status.runtimeName)
            putString("runtimeVersion", status.runtimeVersion)
            putBoolean("runtimeAvailable", status.runtimeAvailable)
            putBoolean("runtimeInitialized", status.runtimeInitialized)
            putBoolean("tensorContractVerified", status.tensorContractVerified)
            putBoolean("sessionActive", status.sessionActive)
            putBoolean("running", status.running)
            putBoolean("workerThreadAlive", status.workerThreadAlive)
            putString("state", status.state)
            putDouble("detectionThreshold", status.detectionThreshold.toDouble())
            putDouble("cooldownMs", status.cooldownMs.toDouble())
            putDouble("cooldownRemainingMs", status.cooldownRemainingMs.toDouble())
            putInt("inputFrameDurationMs", status.inputFrameDurationMs)
            putInt("inputFrameSizeSamples", status.inputFrameSizeSamples)
            putInt("inferenceWindowDurationMs", status.inferenceWindowDurationMs)
            putInt("inferenceWindowSamples", status.inferenceWindowSamples)
            putInt("queuedFrames", status.queuedFrames)
            putInt("queueCapacityFrames", status.queueCapacityFrames)
            putInt("queueHighWaterMarkFrames", status.queueHighWaterMarkFrames)
            putDouble("framesOffered", status.framesOffered.toDouble())
            putDouble("framesConsumed", status.framesConsumed.toDouble())
            putDouble("inferenceCount", status.inferenceCount.toDouble())
            putDouble("averageInferenceLatencyMs", status.averageInferenceLatencyMs)
            putDouble("maximumInferenceLatencyMs", status.maximumInferenceLatencyMs)
            putDouble("detectionCount", status.detectionCount.toDouble())
            putDouble(
                "duplicateSuppressionCount",
                status.duplicateSuppressionCount.toDouble(),
            )
            putDouble("droppedFrameCount", status.droppedFrameCount.toDouble())
            putDouble("malformedFrameCount", status.malformedFrameCount.toDouble())
            putDouble("runtimeErrorCount", status.runtimeErrorCount.toDouble())
            putDouble(
                "lastDetectionTimestampMs",
                status.lastDetectionTimestampMs.toDouble(),
            )
            if (status.lastConfidence == null) {
                putNull("lastConfidence")
            } else {
                putDouble("lastConfidence", status.lastConfidence.toDouble())
            }
            putInt("pcmContextSamples", status.pcmContextSamples)
            putInt("melHistoryFrames", status.melHistoryFrames)
            putInt("melBins", status.melBins)
            putInt("embeddingHistoryFrames", status.embeddingHistoryFrames)
            putInt("embeddingFeatureSize", status.embeddingFeatureSize)
            putString("classifierOutputSemantics", status.classifierOutputSemantics)
            putMap(
                "acousticDiagnostics",
                toWritableWakeAcousticDiagnosticsMap(status.acousticDiagnostics),
            )
            if (status.lastErrorCode == null) {
                putNull("lastErrorCode")
            } else {
                putString("lastErrorCode", status.lastErrorCode)
            }
            if (status.lastErrorMessage == null) {
                putNull("lastErrorMessage")
            } else {
                putString("lastErrorMessage", status.lastErrorMessage)
            }
        }

    private fun toWritableWakeAcousticDiagnosticsMap(
        status: WakeWordAcousticStatus,
    ): WritableMap = Arguments.createMap().apply {
        putBoolean("available", status.available)
        putBoolean("enabled", status.enabled)
        putString("pcmByteOrder", status.pcmByteOrder)
        putString("pcmScaling", status.pcmScaling)
        putBoolean("byteSwapApplied", status.byteSwapApplied)
        putBoolean("normalizationApplied", status.normalizationApplied)
        putDouble("inferenceWindowCount", status.inferenceWindowCount.toDouble())
        putNullableDouble("scoreMinimum", status.scoreMinimum?.toDouble())
        putNullableDouble("scoreMaximum", status.scoreMaximum?.toDouble())
        putDouble("scoreAverage", status.scoreAverage)
        putNullableDouble("scoreP50", status.scoreP50?.toDouble())
        putNullableDouble("scoreP90", status.scoreP90?.toDouble())
        putNullableDouble("scoreP95", status.scoreP95?.toDouble())
        putNullableDouble("scoreP99", status.scoreP99?.toDouble())
        putDouble("lastInferenceTimestampMs", status.lastInferenceTimestampMs.toDouble())
        putDouble("lastInferenceIndex", status.lastInferenceIndex.toDouble())
        putNullableDouble("lastClassifierScore", status.lastClassifierScore?.toDouble())
        putNullableDouble("peakClassifierScore", status.peakClassifierScore?.toDouble())
        putNullableDouble("lastPcmMinimum", status.lastPcmMinimum?.toDouble())
        putNullableDouble("lastPcmMaximum", status.lastPcmMaximum?.toDouble())
        putInt("lastPcmPeak", status.lastPcmPeak)
        putDouble("lastPcmRms", status.lastPcmRms)
        putDouble("lastPcmDbFs", status.lastPcmDbFs)
        putDouble("maximumObservedPcmRms", status.maximumObservedPcmRms)
        putDouble("maximumObservedPcmDbFs", status.maximumObservedPcmDbFs)
        putDouble("clippedSampleCount", status.clippedSampleCount.toDouble())
        putInt("lastQueueDepthFrames", status.lastQueueDepthFrames)
        putDouble("lastInferenceLatencyMs", status.lastInferenceLatencyMs)
        putBoolean("lastAecEnabled", status.lastAecEnabled)
        putBoolean("lastNoiseSuppressionEnabled", status.lastNoiseSuppressionEnabled)
        if (status.activeTrialLabel == null) {
            putNull("activeTrialLabel")
        } else {
            putString("activeTrialLabel", status.activeTrialLabel)
        }
        if (status.activeTrialCondition == null) {
            putNull("activeTrialCondition")
        } else {
            putString("activeTrialCondition", status.activeTrialCondition)
        }
        if (status.activeTrialAttemptNumber == null) {
            putNull("activeTrialAttemptNumber")
        } else {
            putInt("activeTrialAttemptNumber", status.activeTrialAttemptNumber)
        }
        if (status.activeTrialExpectedPositive == null) {
            putNull("activeTrialExpectedPositive")
        } else {
            putBoolean("activeTrialExpectedPositive", status.activeTrialExpectedPositive)
        }
        putInt("completedPositiveTrials", status.completedPositiveTrials)
        putInt("completedNegativeTrials", status.completedNegativeTrials)
        putNullableDouble("positiveScoreMedian", status.positiveScoreMedian?.toDouble())
        putNullableDouble("positiveScoreMaximum", status.positiveScoreMaximum?.toDouble())
        putNullableDouble("negativeScoreMedian", status.negativeScoreMedian?.toDouble())
        putNullableDouble("negativeScoreMaximum", status.negativeScoreMaximum?.toDouble())
        putNullableDouble("medianDetectionLatencyMs", status.medianDetectionLatencyMs)
        putNullableDouble(
            "maximumDetectionLatencyMs",
            status.maximumDetectionLatencyMs?.toDouble(),
        )

        val thresholdArray = Arguments.createArray()
        status.thresholdCounts.forEach { thresholdCount ->
            thresholdArray.pushMap(
                Arguments.createMap().apply {
                    putDouble("threshold", thresholdCount.threshold.toDouble())
                    putDouble("count", thresholdCount.count.toDouble())
                },
            )
        }
        putArray("thresholdCounts", thresholdArray)

        val analysisArray = Arguments.createArray()
        status.thresholdAnalysis.forEach { analysis ->
            analysisArray.pushMap(
                Arguments.createMap().apply {
                    putDouble("threshold", analysis.threshold.toDouble())
                    putInt("positiveTrials", analysis.positiveTrials)
                    putInt("negativeTrials", analysis.negativeTrials)
                    putInt("trueAccepts", analysis.trueAccepts)
                    putInt("falseRejects", analysis.falseRejects)
                    putInt("falseAccepts", analysis.falseAccepts)
                    putInt("trueNegatives", analysis.trueNegatives)
                    putDouble("duplicateDetections", analysis.duplicateDetections.toDouble())
                    putDouble("trueAcceptRate", analysis.trueAcceptRate)
                    putDouble("falseRejectRate", analysis.falseRejectRate)
                    putDouble("falseAcceptRate", analysis.falseAcceptRate)
                    putDouble("duplicateRate", analysis.duplicateRate)
                    putNullableDouble(
                        "medianDetectionLatencyMs",
                        analysis.medianDetectionLatencyMs,
                    )
                    putNullableDouble(
                        "maximumDetectionLatencyMs",
                        analysis.maximumDetectionLatencyMs?.toDouble(),
                    )
                },
            )
        }
        putArray("thresholdAnalysis", analysisArray)

        val trialsArray = Arguments.createArray()
        status.calibrationTrials.forEach { trial ->
            trialsArray.pushMap(toWritableWakeCalibrationTrialMap(trial))
        }
        putArray("calibrationTrials", trialsArray)
    }

    private fun toWritableWakeCalibrationTrialMap(
        trial: WakeWordCalibrationTrial,
    ): WritableMap = Arguments.createMap().apply {
        putString("label", trial.label)
        putString("condition", trial.condition)
        putInt("attemptNumber", trial.attemptNumber)
        putBoolean("expectedPositive", trial.expectedPositive)
        putString("audioProcessingMode", trial.audioProcessingMode)
        putBoolean("aecEnabled", trial.aecEnabled)
        putBoolean("noiseSuppressionEnabled", trial.noiseSuppressionEnabled)
        putDouble("startedAtTimestampMs", trial.startedAtTimestampMs.toDouble())
        putDouble("completedAtTimestampMs", trial.completedAtTimestampMs.toDouble())
        putDouble("firstInferenceIndex", trial.firstInferenceIndex.toDouble())
        putDouble("lastInferenceIndex", trial.lastInferenceIndex.toDouble())
        putDouble("inferenceWindowCount", trial.inferenceWindowCount.toDouble())
        putNullableDouble("minimumScore", trial.minimumScore?.toDouble())
        putNullableDouble("maximumScore", trial.maximumScore?.toDouble())
        putDouble("averageScore", trial.averageScore)
        putInt("peakPcmAmplitude", trial.peakPcmAmplitude)
        putDouble("peakPcmRms", trial.peakPcmRms)
        putDouble("peakPcmDbFs", trial.peakPcmDbFs)
        putInt("maximumQueueDepthFrames", trial.maximumQueueDepthFrames)
        putDouble("averageInferenceLatencyMs", trial.averageInferenceLatencyMs)
        putDouble("maximumInferenceLatencyMs", trial.maximumInferenceLatencyMs)
        putDouble("detectionCount", trial.detectionCount.toDouble())
        putDouble("duplicateDetectionCount", trial.duplicateDetectionCount.toDouble())
        putNullableDouble(
            "firstDetectionTimestampMs",
            trial.firstDetectionTimestampMs?.toDouble(),
        )
        putNullableDouble(
            "firstDetectionLatencyMs",
            trial.firstDetectionLatencyMs?.toDouble(),
        )
        val thresholdResults = Arguments.createArray()
        trial.thresholdResults.forEach { result ->
            thresholdResults.pushMap(
                Arguments.createMap().apply {
                    putDouble("threshold", result.threshold.toDouble())
                    putDouble("detectionCount", result.detectionCount.toDouble())
                    putDouble(
                        "duplicateSuppressionCount",
                        result.duplicateSuppressionCount.toDouble(),
                    )
                    putNullableDouble(
                        "firstDetectionLatencyMs",
                        result.firstDetectionLatencyMs?.toDouble(),
                    )
                },
            )
        }
        putArray("thresholdResults", thresholdResults)
    }

    private fun WritableMap.putNullableDouble(name: String, value: Double?) {
        if (value == null) putNull(name) else putDouble(name, value)
    }

    private fun toWritableSileroVadMap(status: SileroVadEngine.Status): WritableMap =
        Arguments.createMap().apply {
            putBoolean("enabled", status.enabled)
            putBoolean("available", status.available)
            putBoolean("modelPresent", status.modelPresent)
            putBoolean("modelLoaded", status.modelLoaded)
            putString("modelName", status.modelName)
            putString("modelVersion", status.modelVersion)
            putString("modelGitTag", status.modelGitTag)
            putString("modelGitCommit", status.modelGitCommit)
            putString("modelAssetPath", status.modelAssetPath)
            putString("modelFormat", status.modelFormat)
            putDouble("modelSizeBytes", status.modelSizeBytes.toDouble())
            if (status.modelSha256 == null) {
                putNull("modelSha256")
            } else {
                putString("modelSha256", status.modelSha256)
            }
            putBoolean("modelSha256Verified", status.modelSha256Verified)
            putInt("modelOnnxOpset", status.modelOnnxOpset)
            if (status.modelError == null) putNull("modelError") else putString(
                "modelError",
                status.modelError,
            )
            putString("runtimeName", status.runtimeName)
            putString("runtimeVersion", status.runtimeVersion)
            putBoolean("runtimeAvailable", status.runtimeAvailable)
            putBoolean("runtimeInitialized", status.runtimeInitialized)
            putBoolean("inferenceAvailable", status.inferenceAvailable)
            putBoolean("sessionActive", status.sessionActive)
            putBoolean("running", status.running)
            putBoolean("workerThreadAlive", status.workerThreadAlive)
            putString("lifecycleState", status.lifecycleState)
            putString("state", status.state)
            putDouble(
                "speechProbabilityThreshold",
                status.speechProbabilityThreshold.toDouble(),
            )
            putInt("speechStartConfirmationMs", status.speechStartConfirmationMs)
            putInt("speechStartConfirmationChunks", status.speechStartConfirmationChunks)
            putInt("speechStopHangoverMs", status.speechStopHangoverMs)
            putInt("speechStopConfirmationChunks", status.speechStopConfirmationChunks)
            putInt("inputFrameDurationMs", status.inputFrameDurationMs)
            putInt("inputFrameSizeSamples", status.inputFrameSizeSamples)
            putInt("inferenceChunkDurationMs", status.inferenceChunkDurationMs)
            putInt("inferenceChunkSamples", status.inferenceChunkSamples)
            putInt("modelContextSamples", status.modelContextSamples)
            putInt("queueDepthFrames", status.queueDepthFrames)
            putInt("queueCapacityFrames", status.queueCapacityFrames)
            putInt("queueHighWaterMarkFrames", status.queueHighWaterMarkFrames)
            putDouble("framesOffered", status.framesOffered.toDouble())
            putDouble("framesConsumed", status.framesConsumed.toDouble())
            putDouble("droppedFrames", status.droppedFrames.toDouble())
            putDouble("malformedFrames", status.malformedFrames.toDouble())
            putDouble("inferenceCount", status.inferenceCount.toDouble())
            putDouble(
                "successfulInferenceCount",
                status.successfulInferenceCount.toDouble(),
            )
            putDouble("failedInferenceCount", status.failedInferenceCount.toDouble())
            putDouble("averageInferenceDurationMs", status.averageInferenceDurationMs)
            putDouble("maximumInferenceDurationMs", status.maximumInferenceDurationMs)
            putDouble("lastInferenceTimestampMs", status.lastInferenceTimestampMs.toDouble())
            if (status.currentProbability == null) {
                putNull("currentProbability")
            } else {
                putDouble("currentProbability", status.currentProbability.toDouble())
            }
            putDouble("speechStartCount", status.speechStartCount.toDouble())
            putDouble("speechStopCount", status.speechStopCount.toDouble())
            putDouble("resetCount", status.resetCount.toDouble())
            putDouble("errorCount", status.errorCount.toDouble())
            if (status.lastErrorCode == null) {
                putNull("lastErrorCode")
            } else {
                putString("lastErrorCode", status.lastErrorCode)
            }
            if (status.lastErrorMessage == null) {
                putNull("lastErrorMessage")
            } else {
                putString("lastErrorMessage", status.lastErrorMessage)
            }
        }

    companion object {
        private const val TAG = "VoiceAI-Bridge"
        const val NAME = "VoiceModule"

        const val EVENT_AUDIO_ENGINE_STARTED = "AUDIO_ENGINE_STARTED"
        const val EVENT_AUDIO_ENGINE_STOPPED = "AUDIO_ENGINE_STOPPED"
        const val EVENT_AUDIO_ENGINE_ERROR = "AUDIO_ENGINE_ERROR"
        const val EVENT_VAD_SPEECH_STARTED = VadEngine.EVENT_SPEECH_STARTED
        const val EVENT_VAD_SPEECH_STOPPED = VadEngine.EVENT_SPEECH_STOPPED
        const val EVENT_SILERO_VAD_SPEECH_STARTED = SileroVadEngine.EVENT_SPEECH_STARTED
        const val EVENT_SILERO_VAD_SPEECH_STOPPED = SileroVadEngine.EVENT_SPEECH_STOPPED
        const val EVENT_SILERO_VAD_ERROR = SileroVadEngine.EVENT_ERROR
        const val EVENT_WAKE_WORD_DETECTED = WakeWordEngine.EVENT_WAKE_WORD_DETECTED
        const val EVENT_WAKE_ENGINE_STARTED = WakeWordEngine.EVENT_ENGINE_STARTED
        const val EVENT_WAKE_ENGINE_STOPPED = WakeWordEngine.EVENT_ENGINE_STOPPED
        const val EVENT_WAKE_ENGINE_ERROR = WakeWordEngine.EVENT_ENGINE_ERROR
        const val EVENT_VOICE_GATEWAY_STATUS = "VOICE_GATEWAY_STATUS"
        const val EVENT_VOICE_GATEWAY_EVENT = "VOICE_GATEWAY_EVENT"
    }
}
