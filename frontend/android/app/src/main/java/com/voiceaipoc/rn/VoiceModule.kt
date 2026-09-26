package com.voiceaipoc.rn

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
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
import com.voiceaipoc.BuildConfig
import com.voiceaipoc.audio.AudioConfig
import com.voiceaipoc.audio.AudioEngine
import com.voiceaipoc.audio.AudioEffectsManager
import com.voiceaipoc.audio.AudioRouteController
import com.voiceaipoc.audio.FarEndReferenceBuffer
import com.voiceaipoc.auth.SecureTokenStorage
import com.voiceaipoc.audio.AudioEngine.ManualWakeWordTrialStatus
import com.voiceaipoc.diagnostics.DiagnosticSessionContext
import com.voiceaipoc.diagnostics.DiagnosticEvidenceLedger
import com.voiceaipoc.rollout.VoiceRolloutConfig
import com.voiceaipoc.vad.BargeInRouteHealth
import com.voiceaipoc.vad.BargeInConfig
import com.voiceaipoc.vad.PlaybackAwareBargeInDetector
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
    private val voicePreferences = reactContext.applicationContext.getSharedPreferences(
        "voice_output_preferences",
        Context.MODE_PRIVATE,
    )
    @Volatile
    private var voiceOutputEnabled = voicePreferences.getBoolean("enabled", true)
    private val rolloutConfig = VoiceRolloutConfig.fromBuildConfig()
    private val audioConfig = AudioConfig(
        enableAcousticEchoCancellation = rolloutConfig.platformAecEnabled,
        enableNoiseSuppression = rolloutConfig.platformNsEnabled,
        captureSource = if (rolloutConfig.duplexCommunicationRouteEnabled) {
            AudioConfig.CaptureSource.VOICE_COMMUNICATION
        } else {
            AudioConfig.CaptureSource.MIC
        },
        softwareAecMode = rolloutConfig.softwareAecMode,
        bargeInConfig = BargeInConfig(
            earlyBargeInEnabled = rolloutConfig.earlyBargeInEnabled,
        ),
    )
    private val diagnosticSession = DiagnosticSessionContext()
    private val diagnosticEvidenceLedger = DiagnosticEvidenceLedger(diagnosticSession)
    private val farEndReferenceBuffer = FarEndReferenceBuffer(
        tailSuppressionMs = audioConfig.playbackTailSuppressionMs,
        presentationTimingEnabled = rolloutConfig.presentationTimedReferenceEnabled,
    )
    private val audioRouteController = AudioRouteController(
        reactContext.applicationContext,
        AudioRouteController.Config(
            // Debug builds use the earpiece for physical baseline capture so
            // phone playback cannot feed the loudspeaker back into the mic.
            devicePreference = if (BuildConfig.DEBUG) {
                AudioRouteController.DevicePreference.EARPIECE
            } else {
                audioConfig.communicationDevicePreference
            },
            communicationRouteEnabled = rolloutConfig.duplexCommunicationRouteEnabled,
        ),
    )
    @Volatile
    private var audioEngineReference: AudioEngine? = null
    @Volatile
    private var lastConnectionGeneration: Long = 0L
    private val bargeInDetector = PlaybackAwareBargeInDetector(audioConfig.bargeInConfig)

    private val voiceGateway = VoiceWebSocketTransport(
        tokenStorage = authTokenStorage,
        isTtsOutputEnabled = { voiceOutputEnabled },
        farEndReferenceBuffer = farEndReferenceBuffer,
        audioRouteController = audioRouteController,
        onTransportFailure = { failedGeneration: Long ->
            stopCaptureForTransportFailure(failedGeneration)
        },
        diagnosticSession = diagnosticSession,
        listener = object : VoiceWebSocketTransport.Listener {
            override fun onStatus(status: VoiceWebSocketTransport.Status) {
                lastConnectionGeneration = status.connectionGeneration
                val microphone = audioEngineReference?.getStatus()
                Log.i(
                    TAG,
                    "VOICE_LIFECYCLE connection_generation=${status.connectionGeneration} " +
                        "connection_state=${status.state} connected=${status.connected} " +
                        "session_id=${status.sessionId ?: "NONE"} session_started=${status.sessionStarted} " +
                        "turn_id=${status.turnId ?: "NONE"} turn_active=${status.turnActive} " +
                        "response_id=${status.responseId ?: "NONE"} " +
                        "mic_state=${microphone?.state ?: "unavailable"} " +
                        "mic_frames=${microphone?.pcmFramesCaptured ?: 0L} " +
                        "frames_sent=${status.framesSent} last_server_event=${status.lastServerEvent ?: "NONE"} " +
                        "wallMs=${System.currentTimeMillis()}",
                )
                emitVoiceGatewayStatus(status)
            }

            override fun onTtsPlayback(
                eventType: String,
                sessionId: String?,
                turnId: String?,
                responseId: String?,
                timestampMs: Long,
                stopReason: String?,
            ) {
                when (eventType) {
                    "tts.playback.started" -> bargeInDetector.reset("playback_started")
                    "tts.playback.completed",
                    "tts.playback.stopped",
                    "tts.cancelled",
                    "tts.failed" -> bargeInDetector.reset("playback_terminal")
                }
                emitVoiceGatewayEvent(
                    eventType,
                    sessionId,
                    turnId,
                    responseId,
                    null,
                    timestampMs,
                    VoiceWebSocketTransport.ServerEventPayload(stopReason = stopReason),
                    lastConnectionGeneration,
                )
            }

            override fun onServerEvent(
                eventType: String,
                sessionId: String?,
                turnId: String?,
                responseId: String?,
            ) {
                onServerEvent(eventType, sessionId, turnId, responseId, null, null)
            }

            override fun onServerEvent(
                eventType: String,
                sessionId: String?,
                turnId: String?,
                responseId: String?,
                eventId: String?,
                timestampMs: Long?,
            ) {
                resetBargeInForServerEvent(eventType)
                Log.i(
                    TAG,
                    "VOICE server event type=$eventType sessionId=${sessionId ?: "NONE"} " +
                        "turnId=${turnId ?: "NONE"} responseId=${responseId ?: "NONE"} " +
                        "wallMs=${System.currentTimeMillis()}",
                )
                emitVoiceGatewayEvent(
                    eventType,
                    sessionId,
                    turnId,
                    responseId,
                    eventId,
                    timestampMs,
                    null,
                    lastConnectionGeneration,
                )
            }

            override fun onServerEvent(
                eventType: String,
                sessionId: String?,
                turnId: String?,
                responseId: String?,
                eventId: String?,
                timestampMs: Long?,
                payload: VoiceWebSocketTransport.ServerEventPayload?,
            ) {
                resetBargeInForServerEvent(eventType)
                Log.i(
                    TAG,
                    "VOICE server event type=$eventType sessionId=${sessionId ?: "NONE"} " +
                        "turnId=${turnId ?: "NONE"} responseId=${responseId ?: "NONE"} " +
                        "wallMs=${System.currentTimeMillis()}",
                )
                emitVoiceGatewayEvent(
                    eventType,
                    sessionId,
                    turnId,
                    responseId,
                    eventId,
                    timestampMs,
                    payload,
                    lastConnectionGeneration,
                )
            }

            override fun onServerEvent(
                eventType: String,
                sessionId: String?,
                turnId: String?,
                responseId: String?,
                eventId: String?,
                timestampMs: Long?,
                payload: VoiceWebSocketTransport.ServerEventPayload?,
                connectionGeneration: Long,
            ) {
                resetBargeInForServerEvent(eventType)
                emitVoiceGatewayEvent(
                    eventType,
                    sessionId,
                    turnId,
                    responseId,
                    eventId,
                    timestampMs,
                    payload,
                    connectionGeneration,
                )
            }
        },
    )

    private fun stopCaptureForTransportFailure(failedGeneration: Long) {
        val currentGeneration = voiceGateway.getStatus().connectionGeneration
        if (currentGeneration != failedGeneration) {
            Log.i(
                TAG,
                "STALE_TRANSPORT_CLEANUP_IGNORED connection_generation=$failedGeneration " +
                    "current_generation=$currentGeneration",
            )
            return
        }
        bargeInDetector.reset("transport_teardown")
        val result = audioEngineReference?.stopRecording()
        val microphone = audioEngineReference?.getStatus()
        Log.i(
            TAG,
            "VOICE_MIC_TRANSPORT_FAILURE connection_generation=$failedGeneration " +
                "capture_state=${microphone?.state ?: "unavailable"} " +
                "pcm_frames=${microphone?.pcmFramesCaptured ?: 0L} " +
                "stop_succeeded=${result?.succeeded == true}",
        )
    }

    private val audioEngine = AudioEngine(
        context = reactContext.applicationContext,
        config = audioConfig,
        audioRouteController = audioRouteController,
        farEndReferenceBuffer = farEndReferenceBuffer,
        diagnosticSession = diagnosticSession,
        pcmDataCallback = AudioEngine.PcmDataCallback { buffer, samplesRead, _, _, _ ->
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

            override fun onEngineStopped(status: SileroVadEngine.Status) {
                bargeInDetector.reset("microphone_restart")
            }

            override fun onSpeechStarted(event: SileroVadEngine.Event) {
                Log.i(
                    TAG,
                    "SILERO VAD speech started timestampMs=${event.timestampMs} " +
                        "inferenceIndex=${event.inferenceIndex} wallMs=${System.currentTimeMillis()} " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                emitSileroVadEvent(
                    EVENT_SILERO_VAD_SPEECH_STARTED,
                    event,
                )
            }

            override fun onSpeechStopped(event: SileroVadEngine.Event) {
                Log.i(
                    TAG,
                    "SILERO VAD speech stopped timestampMs=${event.timestampMs} " +
                        "inferenceIndex=${event.inferenceIndex} reason=${event.reason} " +
                        "wallMs=${System.currentTimeMillis()} " +
                        "elapsedMs=${SystemClock.elapsedRealtime()}",
                )
                emitSileroVadEvent(
                    EVENT_SILERO_VAD_SPEECH_STOPPED,
                    event,
                )
            }

            override fun onSpeechActivity(event: SileroVadEngine.Event) {
                emitSileroVadEvent(
                    EVENT_SILERO_VAD_SPEECH_ACTIVITY,
                    event,
                )
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
                bargeInDetector.reset("microphone_started")
                emitEvent(EVENT_AUDIO_ENGINE_STARTED, status)
            }

            override fun onStopped(status: AudioEngine.Status) {
                bargeInDetector.reset("microphone_stopped")
                emitEvent(EVENT_AUDIO_ENGINE_STOPPED, status)
            }

            override fun onError(status: AudioEngine.Status) {
                bargeInDetector.reset("microphone_error")
                emitEvent(EVENT_AUDIO_ENGINE_ERROR, status)
            }
        },
    ).also { audioEngineReference = it }

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
    fun getVoiceRolloutConfig(promise: Promise) {
        promise.resolve(toWritableRolloutConfigMap())
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
    fun readAuthTokens(promise: Promise) {
        try {
            val tokens = authTokenStorage.read()
            if (tokens == null) {
                promise.resolve(null)
            } else {
                promise.resolve(
                    Arguments.createMap().apply {
                        putString("accessToken", tokens.accessToken)
                        putString("refreshToken", tokens.refreshToken)
                    },
                )
            }
        } catch (exception: GeneralSecurityException) {
            promise.reject("E_AUTH_TOKEN_READ", "Unable to read authentication state.", exception)
        } catch (exception: RuntimeException) {
            promise.reject("E_AUTH_TOKEN_READ", "Unable to read authentication state.", exception)
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
        bargeInDetector.reset("transport_teardown")
        voiceGateway.disconnect()
        if (!audioEngine.isRecording()) diagnosticSession.end()
        promise.resolve(toWritableVoiceGatewayMap(voiceGateway.getStatus()))
    }

    @ReactMethod
    fun startVoiceSession(resumeSessionId: String?, promise: Promise) {
        diagnosticSession.ensureActive()
        resolveVoiceResult(voiceGateway.startSession(resumeSessionId), promise)
    }

    @ReactMethod
    fun startVoiceTurn(
        clientTurnId: String?,
        includePreRoll: Boolean,
        promise: Promise,
    ) {
        resolveVoiceResult(voiceGateway.startTurn(clientTurnId, includePreRoll), promise)
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
    fun abortAllVoiceResponses(reason: String?, promise: Promise) {
        resolveVoiceResult(voiceGateway.abortAllResponses(reason ?: "abort_all"), promise)
    }

    @ReactMethod
    fun resetVoiceConversation(promise: Promise) {
        resolveVoiceResult(voiceGateway.resetConversation(), promise)
    }

    @ReactMethod
    fun stopVoicePlayback(promise: Promise) {
        bargeInDetector.reset("playback_stopped")
        voiceGateway.stopTtsPlayback()
        promise.resolve(toWritableVoiceGatewayMap(voiceGateway.getStatus()))
    }

    @ReactMethod
    fun getVoiceOutputPreferences(promise: Promise) {
        promise.resolve(
            Arguments.createMap().apply {
                putBoolean("enabled", voiceOutputEnabled)
            },
        )
    }

    @ReactMethod
    fun setVoiceOutputEnabled(enabled: Boolean, promise: Promise) {
        voiceOutputEnabled = enabled
        voicePreferences.edit().putBoolean("enabled", enabled).apply()
        if (!enabled) {
            bargeInDetector.reset("playback_stopped")
            voiceGateway.stopTtsPlayback()
        }
        promise.resolve(
            Arguments.createMap().apply {
                putBoolean("enabled", voiceOutputEnabled)
            },
        )
    }

    @ReactMethod
    fun retryVoiceResponse(
        turnId: String,
        originalResponseId: String,
        transcript: String,
        promise: Promise,
    ) {
        resolveVoiceResult(
            voiceGateway.retryResponse(turnId, originalResponseId, transcript),
            promise,
        )
    }

    @ReactMethod
    fun resolveVoiceConfirmation(
        confirmationId: String,
        toolCallId: String,
        decision: String,
        promise: Promise,
    ) {
        resolveVoiceResult(
            voiceGateway.resolveConfirmation(confirmationId, toolCallId, decision),
            promise,
        )
    }

    @ReactMethod
    fun endVoiceSession(reason: String?, promise: Promise) {
        bargeInDetector.reset("transport_teardown")
        val result = voiceGateway.endSession(reason ?: "client_requested")
        resolveVoiceResult(result, promise)
        if (result.succeeded && !audioEngine.isRecording()) diagnosticSession.end()
    }

    @ReactMethod
    fun getVoiceGatewayStatus(promise: Promise) {
        promise.resolve(toWritableVoiceGatewayMap(voiceGateway.getStatus()))
    }

    @ReactMethod
    fun copyTextToClipboard(text: String, promise: Promise) {
        val clipboard = reactApplicationContext.getSystemService(Context.CLIPBOARD_SERVICE)
            as? ClipboardManager
        if (clipboard == null) {
            promise.reject("E_CLIPBOARD", "Clipboard is unavailable.")
            return
        }
        clipboard.setPrimaryClip(ClipData.newPlainText("Voice Assistant", text))
        promise.resolve(true)
    }

    @ReactMethod
    fun startMicrophone(promise: Promise) {
        diagnosticSession.ensureActive()
        bargeInDetector.reset("microphone_restart")
        val result = audioEngine.startRecording()
        val gateway = voiceGateway.getStatus()
        Log.i(
            TAG,
            "VOICE_MIC_START connection_generation=${gateway.connectionGeneration} " +
                "session_id=${gateway.sessionId ?: "NONE"} turn_id=${gateway.turnId ?: "NONE"} " +
                "capture_state=${audioEngine.getStatus().state} " +
                "pcm_frames=${audioEngine.getStatus().pcmFramesCaptured} succeeded=${result.succeeded}",
        )
        if (result.succeeded) {
            promise.resolve(toWritableMap(audioEngine.getStatus()))
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
    }

    @ReactMethod
    fun stopMicrophone(promise: Promise) {
        bargeInDetector.reset("microphone_stopped")
        val result = audioEngine.stopRecording()
        val gateway = voiceGateway.getStatus()
        Log.i(
            TAG,
            "VOICE_MIC_STOP connection_generation=${gateway.connectionGeneration} " +
                "session_id=${gateway.sessionId ?: "NONE"} turn_id=${gateway.turnId ?: "NONE"} " +
                "capture_state=${audioEngine.getStatus().state} " +
                "pcm_frames=${audioEngine.getStatus().pcmFramesCaptured} succeeded=${result.succeeded}",
        )
        if (result.succeeded) {
            promise.resolve(toWritableMap(audioEngine.getStatus()))
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
        if (result.succeeded && !voiceGateway.getStatus().sessionStarted) diagnosticSession.end()
    }

    @ReactMethod
    fun getMicrophoneStatus(promise: Promise) {
        promise.resolve(toWritableMap(audioEngine.getStatus()))
    }

    /** Returns bounded acoustic evidence; transcript, PCM, tokens, and secrets are excluded. */
    @ReactMethod
    fun getDiagnosticEvidence(promise: Promise) {
        try {
            val microphone = audioEngine.getStatus()
            val processing = audioEngine.getAudioProcessingStatus()
            val pipeline = audioEngine.getAudioPipelineStatus()
            val gateway = voiceGateway.getStatus()
            val decisions = Arguments.createArray()
            diagnosticEvidenceLedger.snapshot().forEach { record ->
                decisions.pushMap(toWritableDecisionRecordMap(record))
            }
            promise.resolve(
                Arguments.createMap().apply {
                    putBoolean("metadataOnly", true)
                    putString("diagnosticSessionId", microphone.diagnosticSessionId)
                    putDouble("exportedAtTimestampMs", System.currentTimeMillis().toDouble())
                    putArray(
                        "redactedFields",
                        Arguments.fromList(
                            listOf("transcript", "pcm", "tokens", "provider_secrets"),
                        ),
                    )
                    putMap("microphone", toWritableMap(microphone))
                    putMap(
                        "audioProcessing",
                        toWritableAudioProcessingMap(processing, audioEngine.getSoftwareAecStatus()),
                    )
                    putMap("audioPipeline", toWritableAudioPipelineMap(pipeline))
                    putMap("voiceGateway", toWritableVoiceGatewayMap(gateway))
                    putMap("rollout", toWritableRolloutConfigMap())
                    putArray("decisions", decisions)
                },
            )
        } catch (exception: RuntimeException) {
            promise.reject("E_DIAGNOSTIC_EVIDENCE", "Unable to assemble diagnostic evidence.", exception)
        }
    }

    @ReactMethod
    fun getAudioProcessingStatus(promise: Promise) {
        promise.resolve(
            toWritableAudioProcessingMap(
                audioEngine.getAudioProcessingStatus(),
                audioEngine.getSoftwareAecStatus(),
            ),
        )
    }

    @ReactMethod
    fun setAudioCaptureSource(source: String, promise: Promise) {
        val captureSource = when (source) {
            "VOICE_COMMUNICATION" -> AudioConfig.CaptureSource.VOICE_COMMUNICATION
            "MIC" -> AudioConfig.CaptureSource.MIC
            else -> {
                promise.reject("E_AUDIO_CAPTURE_SOURCE", "Unknown audio capture source: $source")
                return
            }
        }
        val result = audioEngine.setCaptureSource(captureSource)
        if (result.succeeded) {
            promise.resolve(toWritableMap(audioEngine.getStatus()))
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
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
        consentGranted: Boolean,
        promise: Promise,
    ) {
        if (!consentGranted) {
            promise.reject(
                "E_WAKE_DIAGNOSTIC_CONSENT_REQUIRED",
                "Explicit diagnostic PCM capture consent is required.",
            )
            return
        }
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
            promise.resolve(
                toWritableAudioProcessingMap(
                    audioEngine.getAudioProcessingStatus(),
                    audioEngine.getSoftwareAecStatus(),
                ),
            )
        } else {
            promise.reject(result.errorCode, result.errorMessage)
        }
    }

    override fun invalidate() {
        bargeInDetector.reset("module_invalidation")
        audioEngine.release()
        voiceGateway.shutdown()
        diagnosticSession.end()
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
        eventId: String?,
        timestampMs: Long?,
        eventPayload: VoiceWebSocketTransport.ServerEventPayload?,
        connectionGeneration: Long,
    ) {
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        val eventText = eventPayload?.text
        val eventDelta = eventPayload?.delta
        val eventFinal = eventPayload?.isFinal
        val eventSequence = eventPayload?.sequence
        val eventAttempt = eventPayload?.attempt
        val transcriptSequence = eventPayload?.transcriptSequence
        val eventLanguage = eventPayload?.language
        val audioDurationMs = eventPayload?.audioDurationMs
        val sampleRateHz = eventPayload?.sampleRateHz
        val toolCallId = eventPayload?.toolCallId
        val toolName = eventPayload?.toolName
        val toolStatus = eventPayload?.toolStatus
        val confirmationId = eventPayload?.confirmationId
        val status = eventPayload?.status
        val errorCode = eventPayload?.errorCode
        val retryable = eventPayload?.retryable
        val stopReason = eventPayload?.stopReason
        val metrics = eventPayload?.metrics
        val usage = eventPayload?.usage
        val payload = Arguments.createMap().apply {
            putDouble("connectionGeneration", connectionGeneration.toDouble())
            putString("event", eventType)
            if (sessionId == null) putNull("sessionId") else putString("sessionId", sessionId)
            if (turnId == null) putNull("turnId") else putString("turnId", turnId)
            if (responseId == null) putNull("responseId") else putString("responseId", responseId)
            if (eventId == null) putNull("eventId") else putString("eventId", eventId)
            if (timestampMs == null) putNull("timestampMs") else putDouble("timestampMs", timestampMs.toDouble())
            if (eventText == null) putNull("text") else putString("text", eventText)
            if (eventDelta == null) putNull("delta") else putString("delta", eventDelta)
            if (eventFinal == null) putNull("final") else putBoolean("final", eventFinal)
            if (eventSequence == null) putNull("sequence") else putDouble("sequence", eventSequence.toDouble())
            if (eventAttempt == null) putNull("attempt") else putDouble("attempt", eventAttempt.toDouble())
            if (transcriptSequence == null) {
                putNull("transcriptSequence")
            } else {
                putDouble("transcriptSequence", transcriptSequence.toDouble())
            }
            if (eventLanguage == null) putNull("language") else putString("language", eventLanguage)
            if (audioDurationMs == null) {
                putNull("audioDurationMs")
            } else {
                putDouble("audioDurationMs", audioDurationMs.toDouble())
            }
            if (sampleRateHz == null) putNull("sampleRateHz") else putDouble("sampleRateHz", sampleRateHz.toDouble())
            if (toolCallId == null) putNull("toolCallId") else putString("toolCallId", toolCallId)
            if (toolName == null) putNull("toolName") else putString("toolName", toolName)
            if (toolStatus == null) putNull("toolStatus") else putString("toolStatus", toolStatus)
            if (confirmationId == null) {
                putNull("confirmationId")
            } else {
                putString("confirmationId", confirmationId)
            }
            if (status == null) putNull("status") else putString("status", status)
            if (errorCode == null) putNull("code") else putString("code", errorCode)
            if (retryable == null) putNull("retryable") else putBoolean("retryable", retryable)
            if (stopReason == null) putNull("stopReason") else putString("stopReason", stopReason)
            if (metrics == null) {
                putNull("metrics")
            } else {
                putMap(
                    "metrics",
                    Arguments.createMap().apply {
                        metrics.forEach { (key, value) ->
                            if (value == null) putNull(key) else putDouble(key, value)
                        }
                    },
                )
            }
            if (usage == null) {
                putNull("usage")
            } else {
                putMap(
                    "usage",
                    Arguments.createMap().apply {
                        usage.forEach { (key, value) ->
                            if (value == null) putNull(key) else putDouble(key, value)
                        }
                    },
                )
            }
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
            putString("monotonicNs", SystemClock.elapsedRealtimeNanos().toString())
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

    private fun emitSileroVadEvent(
        eventName: String,
        event: SileroVadEngine.Event,
    ) {
        // The detector runs before the bridge guard so React lifecycle state
        // cannot change the native decision path.
        if (rolloutConfig.nativeBargeInDetectorEnabled) {
            bargeInDetector.onSileroEvent(event, currentBargeInRouteHealth())?.let { decision ->
                // Native confirmation owns the local stop. This call is synchronous
                // and response-scoped so the bridge cannot race a replacement turn.
                val playbackStopAck = if (
                    rolloutConfig.nativeBargeInAuthoritative &&
                        decision.event == PlaybackAwareBargeInDetector.EVENT_CONFIRMED &&
                        decision.responseId != null
                ) {
                    voiceGateway.stopTtsPlaybackForBargeIn(
                        responseId = decision.responseId,
                        detectionMonotonicNs = decision.input.monotonicTimestampNs,
                    )
                } else {
                    null
                }
                playbackStopAck?.let {
                    bargeInDetector.recordLocalStopLatency(it.responseId, it.localStopLatencyMs)
                }
                diagnosticEvidenceLedger.record(decision, playbackStopAck)
                emitBargeInDecision(decision, playbackStopAck)
            }
        }
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }

        val gatewayStatus = voiceGateway.getStatus()
        if (event.playbackActive && eventName == EVENT_SILERO_VAD_SPEECH_STARTED) {
            Log.i(
                TAG,
                "SILERO_PLAYBACK_CANDIDATE_STARTED session_id=${gatewayStatus.sessionId ?: "NONE"} " +
                    "turn_id=${gatewayStatus.turnId ?: "NONE"} " +
                    "response_id=${event.playbackResponseId ?: gatewayStatus.responseId ?: "NONE"} " +
                    "probability=${event.probability} duration_ms=${event.speechDurationMs} " +
                    "playback_position_ms=${event.playbackPositionMs} " +
                    "echo_similarity=${event.echoSimilarity ?: -1.0} " +
                    "lag_ms=${event.echoLagMs ?: -1} " +
                    "source_frames=${event.sourceFrameSequenceStart}-${event.sourceFrameSequenceEnd} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
        } else if (event.playbackActive && eventName == EVENT_SILERO_VAD_SPEECH_STOPPED) {
            Log.i(
                TAG,
                "SILERO_PLAYBACK_CANDIDATE_ENDED session_id=${gatewayStatus.sessionId ?: "NONE"} " +
                    "turn_id=${gatewayStatus.turnId ?: "NONE"} " +
                    "response_id=${event.playbackResponseId ?: gatewayStatus.responseId ?: "NONE"} " +
                    "probability=${event.probability} duration_ms=${event.speechDurationMs} " +
                    "playback_position_ms=${event.playbackPositionMs} " +
                    "echo_similarity=${event.echoSimilarity ?: -1.0} " +
                    "lag_ms=${event.echoLagMs ?: -1} " +
                    "source_frames=${event.sourceFrameSequenceStart}-${event.sourceFrameSequenceEnd} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
        }
        if (event.echoLikely) {
            Log.i(
                TAG,
                "PLAYBACK_REFERENCE_MATCH session_id=${gatewayStatus.sessionId ?: "NONE"} " +
                    "turn_id=${gatewayStatus.turnId ?: "NONE"} " +
                    "response_id=${event.playbackResponseId ?: gatewayStatus.responseId ?: "NONE"} " +
                    "echo_similarity=${event.echoSimilarity ?: -1.0} " +
                    "lag_ms=${event.echoLagMs ?: -1} " +
                    "playback_position_ms=${event.playbackPositionMs} " +
                    "source_frames=${event.sourceFrameSequenceStart}-${event.sourceFrameSequenceEnd} " +
                    "elapsedMs=${SystemClock.elapsedRealtime()}",
            )
        }

        val payload = Arguments.createMap().apply {
            putString("event", event.event)
            putDouble("timestampMs", event.timestampMs.toDouble())
            putString("monotonicNs", event.monotonicTimestampNs.toString())
            putDouble("probability", event.probability.toDouble())
            putDouble("inferenceIndex", event.inferenceIndex.toDouble())
            putDouble("speechDurationMs", event.speechDurationMs.toDouble())
            putString("reason", event.reason)
            putDouble("sourceFrameSequenceStart", event.sourceFrameSequenceStart.toDouble())
            putDouble("sourceFrameSequenceEnd", event.sourceFrameSequenceEnd.toDouble())
            putString("captureStartNs", event.captureStartNs.toString())
            putString("captureEndNs", event.captureEndNs.toString())
            putBoolean("discontinuous", event.discontinuous)
            putString("playbackState", event.playbackState)
            putBoolean("playbackActive", event.playbackActive)
            putBoolean("playbackReferenceAvailable", event.referenceReady)
            putBoolean("echoLikely", event.echoLikely)
            if (event.playbackResponseId == null) putNull("playbackResponseId") else {
                putString("playbackResponseId", event.playbackResponseId)
            }
            if (event.echoSimilarity == null) putNull("echoSimilarity") else {
                putDouble("echoSimilarity", event.echoSimilarity)
            }
            if (event.echoLagMs == null) putNull("echoLagMs") else {
                putInt("echoLagMs", event.echoLagMs)
            }
            putDouble("playbackPositionMs", event.playbackPositionMs.toDouble())
            putString("timestampConfidence", event.referenceConfidence)
            if (event.estimatedEchoDelayMs == null) putNull("estimatedDelayMs") else {
                putInt("estimatedDelayMs", event.estimatedEchoDelayMs)
            }
            if (event.echoCoherence == null) putNull("echoCoherence") else {
                putDouble("echoCoherence", event.echoCoherence)
            }
            if (event.farEndRms == null) putNull("farEndRms") else {
                putDouble("farEndRms", event.farEndRms)
            }
            if (event.micRms == null) putNull("micRms") else {
                putDouble("micRms", event.micRms)
            }
            if (event.nearEndResidualRatio == null) putNull("nearEndResidualRatio") else {
                putDouble("nearEndResidualRatio", event.nearEndResidualRatio)
            }
            if (event.nearEndFarEndEnergyRatio == null) putNull("nearEndFarEndEnergyRatio") else {
                putDouble("nearEndFarEndEnergyRatio", event.nearEndFarEndEnergyRatio)
            }
        }
        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(eventName, payload)
    }

    private fun currentBargeInRouteHealth(): BargeInRouteHealth {
        return runCatching {
            val audioStatus = audioEngine.getStatus()
            val processingStatus = audioEngine.getAudioProcessingStatus()
            val route = audioStatus.route
            val softwareAec = audioStatus.softwareAec
            val communicationModeActive = rolloutConfig.duplexCommunicationRouteEnabled &&
                route.modeAcquired &&
                route.actualMode == AudioRouteController.MODE_IN_COMMUNICATION
            val platformAecHealthy = rolloutConfig.platformAecEnabled &&
                processingStatus.aec.available &&
                processingStatus.aec.enabled
            val softwareAecHealthy = softwareAec.state ==
                com.voiceaipoc.audio.SoftwareAecController.State.ACTIVE
            val aecHealthy = platformAecHealthy || softwareAecHealthy
            val loudspeakerRoute = route.outputDeviceType.contains("SPEAKER") ||
                route.playbackRoute.contains("SPEAKER", ignoreCase = true)
            val automaticLoudspeakerBargeInAllowed =
                rolloutConfig.automaticLoudspeakerBargeInEnabled &&
                    (!loudspeakerRoute || (communicationModeActive && aecHealthy))
            BargeInRouteHealth(
                communicationModeActive = communicationModeActive,
                aecAvailable = platformAecHealthy || softwareAecHealthy,
                aecEnabled = aecHealthy,
                aecEffectiveness = if (softwareAecHealthy) {
                    "SOFTWARE_ACTIVE"
                } else {
                    processingStatus.aec.effectiveness
                },
                automaticLoudspeakerBargeInAllowed = automaticLoudspeakerBargeInAllowed,
            )
        }.getOrElse {
            BargeInRouteHealth(
                communicationModeActive = false,
                aecAvailable = false,
                aecEnabled = false,
                aecEffectiveness = "UNAVAILABLE",
                automaticLoudspeakerBargeInAllowed = false,
            )
        }
    }

    private fun resetBargeInForServerEvent(eventType: String) {
        if (eventType == "response.cancelled" ||
            eventType == "tts.cancelled" ||
            eventType == "tts.failed" ||
            eventType == "server.session.ended"
        ) {
            bargeInDetector.reset("transport_or_playback_terminal")
        }
    }

    private fun emitBargeInDecision(
        decision: PlaybackAwareBargeInDetector.Decision,
        playbackStopAck: VoiceWebSocketTransport.BargeInPlaybackStopAck? = null,
    ) {
        val input = decision.input
        Log.i(
            TAG,
            diagnosticSession.tag(
                "BARGE_IN_DECISION event=${decision.event} state=${decision.state.name} " +
                    "reason=${decision.reason} response_id=${decision.responseId ?: "NONE"} " +
                    "source_frames=${input.sourceFrameSequenceStart}-${input.sourceFrameSequenceEnd} " +
                    "inference=${input.inferenceIndex} capture=${input.captureStartNs}-${input.captureEndNs} " +
                    "reference_ready=${input.referenceReady} " +
                    "timestamp_confidence=${decision.timestampConfidence} " +
                    "local_stop_latency_ms=${playbackStopAck?.localStopLatencyMs ?: "NONE"}",
            ),
        )
        if (decision.event == EVENT_BARGE_IN_CONFIRMED ||
            decision.event == EVENT_BARGE_IN_REJECTED_ECHO ||
            decision.event == EVENT_BARGE_IN_DEGRADED
        ) {
            voiceGateway.recordBargeInDecision(
                eventType = decision.event,
                responseId = decision.responseId,
                monotonicNs = input.monotonicTimestampNs,
                metadata = mapOf(
                    "state" to decision.state.name,
                    "reason" to decision.reason,
                    "segment_duration_ms" to decision.segmentDurationMs,
                    "probability" to input.probability,
                    "playback_state" to input.playbackState,
                    "playback_active" to input.playbackActive,
                    "playback_position_ms" to input.playbackPositionMs,
                    "reference_ready" to input.referenceReady,
                    "reference_usable" to decision.referenceUsable,
                    "timestamp_confidence" to decision.timestampConfidence,
                    "aec_healthy" to decision.aecHealthy,
                    "communication_mode_active" to input.routeHealth.communicationModeActive,
                    "aec_available" to input.routeHealth.aecAvailable,
                    "aec_enabled" to input.routeHealth.aecEnabled,
                    "aec_effectiveness" to input.routeHealth.aecEffectiveness,
                    "automatic_loudspeaker_barge_in_allowed" to
                        input.routeHealth.automaticLoudspeakerBargeInAllowed,
                    "echo_similarity" to input.echoSimilarity,
                    "echo_coherence" to input.echoCoherence,
                    "estimated_delay_ms" to input.estimatedDelayMs,
                    "far_end_rms" to input.farEndRms,
                    "mic_rms" to input.micRms,
                    "near_end_residual_ratio" to input.nearEndResidualRatio,
                    "near_end_far_end_energy_ratio" to input.nearEndFarEndEnergyRatio,
                    "discontinuous" to input.discontinuous,
                    "local_stop_requested" to playbackStopAck?.localStopRequested,
                    "local_stop_completed" to playbackStopAck?.localStopCompleted,
                    "local_stop_latency_ms" to playbackStopAck?.localStopLatencyMs,
                ),
            )
        }
        if (!reactApplicationContext.hasActiveReactInstance()) {
            return
        }
        val payload = Arguments.createMap().apply {
            putString("event", decision.event)
            putString("state", decision.state.name)
            putString("reason", decision.reason)
            if (decision.responseId == null) putNull("responseId") else {
                putString("responseId", decision.responseId)
            }
            putString("monotonicNs", input.monotonicTimestampNs.toString())
            putString("captureStartNs", input.captureStartNs.toString())
            putString("captureEndNs", input.captureEndNs.toString())
            putDouble("segmentDurationMs", decision.segmentDurationMs.toDouble())
            putDouble("probability", input.probability.toDouble())
            putString("playbackState", input.playbackState)
            putBoolean("playbackActive", input.playbackActive)
            putDouble("playbackPositionMs", input.playbackPositionMs.toDouble())
            putBoolean("referenceReady", input.referenceReady)
            putBoolean("referenceUsable", decision.referenceUsable)
            putString("timestampConfidence", decision.timestampConfidence)
            putBoolean("aecHealthy", decision.aecHealthy)
            putBoolean("communicationModeActive", input.routeHealth.communicationModeActive)
            putBoolean("aecAvailable", input.routeHealth.aecAvailable)
            putBoolean("aecEnabled", input.routeHealth.aecEnabled)
            putString("aecEffectiveness", input.routeHealth.aecEffectiveness)
            putBoolean(
                "automaticLoudspeakerBargeInAllowed",
                input.routeHealth.automaticLoudspeakerBargeInAllowed,
            )
            putDouble("sourceFrameSequenceStart", input.sourceFrameSequenceStart.toDouble())
            putDouble("sourceFrameSequenceEnd", input.sourceFrameSequenceEnd.toDouble())
            putDouble("inferenceIndex", input.inferenceIndex.toDouble())
            putBoolean("discontinuous", input.discontinuous)
            putNullableDouble("echoSimilarity", input.echoSimilarity)
            putNullableDouble("echoCoherence", input.echoCoherence)
            putNullableDouble("estimatedDelayMs", input.estimatedDelayMs?.toDouble())
            putNullableDouble("farEndRms", input.farEndRms)
            putNullableDouble("micRms", input.micRms)
            putNullableDouble("nearEndResidualRatio", input.nearEndResidualRatio)
            putNullableDouble("nearEndFarEndEnergyRatio", input.nearEndFarEndEnergyRatio)
            putBoolean("localStopRequested", playbackStopAck?.localStopRequested == true)
            putBoolean("localStopCompleted", playbackStopAck?.localStopCompleted == true)
            putBoolean("audioTrackStopped", playbackStopAck?.audioTrackStopped == true)
            putBoolean("audioTrackFlushed", playbackStopAck?.audioTrackFlushed == true)
            putBoolean("audioTrackReleased", playbackStopAck?.audioTrackReleased == true)
            putBoolean("localStopReleasePending", playbackStopAck?.releasePending == true)
            if (playbackStopAck == null) {
                putNull("stopReason")
                putNull("stopRequestedMonotonicNs")
                putNull("localStopLatencyMs")
            } else {
                putString("stopReason", playbackStopAck.reason)
                putString(
                    "stopRequestedMonotonicNs",
                    playbackStopAck.stopRequestedMonotonicNs.toString(),
                )
                putDouble("localStopLatencyMs", playbackStopAck.localStopLatencyMs.toDouble())
            }
        }
        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(decision.event, payload)
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
        putString("diagnosticSessionId", status.diagnosticSessionId)
        putDouble("pcmFramesCaptured", status.pcmFramesCaptured.toDouble())
        putDouble("captureDurationMs", status.captureDurationMs.toDouble())
        putInt("microphoneErrorCount", status.microphoneErrorCount)
        putString("requestedCaptureSource", status.requestedCaptureSource)
        putString("actualCaptureSource", status.actualCaptureSource)
        putString("playbackUsage", status.playbackUsage)
        putString("playbackContentType", status.playbackContentType)
        putMap("playback", toWritablePlaybackStatusMap(status.playback))
        putMap("detector", toWritableBargeInDetectorStatusMap(bargeInDetector.getStatus()))
        putMap("softwareAec", toWritableSoftwareAecMap(status.softwareAec))
        putMap("route", toWritableAudioRouteMap(status.route))
        putMap("rollout", toWritableRolloutConfigMap())
        if (status.lastError == null) {
            putNull("lastError")
        } else {
            putString("lastError", status.lastError)
        }
    }

    private fun toWritableVoiceGatewayMap(
        status: VoiceWebSocketTransport.Status,
    ): WritableMap = Arguments.createMap().apply {
        putDouble("connectionGeneration", status.connectionGeneration.toDouble())
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
        putString("diagnosticSessionId", status.diagnosticSessionId)
        if (status.lastError == null) putNull("lastError") else putString("lastError", status.lastError)
    }

    private fun toWritableAudioProcessingMap(
        status: AudioEffectsManager.Status,
        softwareAec: com.voiceaipoc.audio.SoftwareAecController.Status,
    ): WritableMap = Arguments.createMap().apply {
        putInt("audioSessionId", status.audioSessionId)
        putMap("aec", toWritableEffectMap(status.aec))
        putMap("noiseSuppression", toWritableEffectMap(status.noiseSuppression))
        putString("manufacturer", status.manufacturer)
        putString("model", status.model)
        putInt("androidSdk", status.androidSdk)
        val softwareAecActive = softwareAec.state == com.voiceaipoc.audio.SoftwareAecController.State.ACTIVE ||
            softwareAec.state == com.voiceaipoc.audio.SoftwareAecController.State.DEGRADED
        putString(
            "aecSelection",
            when {
                softwareAecActive && softwareAec.aecRequested -> "SOFTWARE"
                status.aec.requested -> "PLATFORM"
                else -> "DISABLED"
            },
        )
        putString(
            "aecHealth",
            if (softwareAecActive && softwareAec.aecRequested) softwareAec.state.name
            else status.aec.effectiveness,
        )
        putString(
            "noiseSuppressionSelection",
            when {
                softwareAecActive && softwareAec.noiseSuppressionRequested -> "SOFTWARE"
                status.noiseSuppression.requested -> "PLATFORM"
                else -> "DISABLED"
            },
        )
        putString(
            "noiseSuppressionHealth",
            if (softwareAecActive && softwareAec.noiseSuppressionRequested) softwareAec.state.name
            else status.noiseSuppression.effectiveness,
        )
        putMap("softwareAec", toWritableSoftwareAecMap(softwareAec))
    }

    private fun toWritableSoftwareAecMap(
        status: com.voiceaipoc.audio.SoftwareAecController.Status,
    ): WritableMap = Arguments.createMap().apply {
        putString("requestedMode", status.requestedMode)
        putString("state", status.state.name)
        putString("implementation", status.implementation)
        putInt("sampleRateHz", status.sampleRateHz)
        putInt("frameDurationMs", status.frameDurationMs)
        putInt("frameSizeSamples", status.frameSizeSamples)
        putInt("renderToCaptureDelayMs", status.renderToCaptureDelayMs)
        putBoolean("aecRequested", status.aecRequested)
        putBoolean("noiseSuppressionRequested", status.noiseSuppressionRequested)
        putBoolean("platformAecDisabled", status.platformAecDisabled)
        putBoolean("platformNoiseSuppressionDisabled", status.platformNoiseSuppressionDisabled)
        putDouble("referenceReadyFrames", status.referenceReadyFrames.toDouble())
        putDouble("referenceMissingFrames", status.referenceMissingFrames.toDouble())
        putDouble("captureFrames", status.captureFrames.toDouble())
        putDouble("renderFrames", status.renderFrames.toDouble())
        putDouble("processedFrames", status.processedFrames.toDouble())
        putDouble("bypassedFrames", status.bypassedFrames.toDouble())
        putDouble("droppedFrames", status.droppedFrames.toDouble())
        putDouble("processingErrorCount", status.processingErrorCount.toDouble())
        putString("lastReferenceConfidence", status.lastReferenceConfidence)
        putDouble("lastFarEndRms", status.lastFarEndRms)
        putDouble("lastInputRms", status.lastInputRms)
        putDouble("lastOutputRms", status.lastOutputRms)
        if (status.lastError == null) putNull("lastError") else putString("lastError", status.lastError)
    }

    private fun toWritableEffectMap(
        status: AudioEffectsManager.EffectStatus,
    ): WritableMap = Arguments.createMap().apply {
        putBoolean("supported", status.supported)
        putBoolean("available", status.available)
        putBoolean("requested", status.requested)
        putBoolean("created", status.created)
        putBoolean("enabled", status.enabled)
        putBoolean("platformEnabledBeforeAttach", status.platformEnabledBeforeAttach)
        putString("effectiveness", status.effectiveness)
        if (status.lastError == null) {
            putNull("lastError")
        } else {
            putString("lastError", status.lastError)
        }
    }

    private fun toWritableAudioRouteMap(
        status: AudioRouteController.Status,
    ): WritableMap = Arguments.createMap().apply {
        putBoolean("communicationRouteEnabled", status.communicationRouteEnabled)
        putInt("activeLeaseCount", status.activeLeaseCount)
        putInt("captureLeaseCount", status.captureLeaseCount)
        putInt("playbackLeaseCount", status.playbackLeaseCount)
        putInt("requestedMode", status.requestedMode)
        putInt("actualMode", status.actualMode)
        if (status.priorMode == null) putNull("priorMode") else putInt("priorMode", status.priorMode)
        putBoolean("modeAcquired", status.modeAcquired)
        putBoolean("modeRestored", status.modeRestored)
        putString("requestedCommunicationDevice", status.requestedCommunicationDevice)
        putString("actualCommunicationDevice", status.actualCommunicationDevice)
        putString("inputDeviceType", status.inputDeviceType)
        putString("outputDeviceType", status.outputDeviceType)
        putBoolean("communicationDeviceSelected", status.communicationDeviceSelected)
        putString("playbackRoute", status.playbackRoute)
        putBoolean("audioFocusRequested", status.audioFocusRequested)
        putBoolean("audioFocusGranted", status.audioFocusGranted)
        putString("audioFocusState", status.audioFocusState)
        putBoolean("audioFocusRestored", status.audioFocusRestored)
        putInt("restorationCount", status.restorationCount)
        putBoolean("api31CommunicationDeviceSupported", status.api31CommunicationDeviceSupported)
        if (status.lastError == null) putNull("lastError") else putString("lastError", status.lastError)
    }

    private fun toWritableRolloutConfigMap(): WritableMap = Arguments.createMap().apply {
        rolloutConfig.toMap().forEach { (key, value) ->
            when (value) {
                is Boolean -> putBoolean(key, value)
                is String -> putString(key, value)
            }
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

    private fun toWritablePlaybackStatusMap(
        status: FarEndReferenceBuffer.Status,
    ): WritableMap = Arguments.createMap().apply {
        putString("state", status.state.name)
        if (status.responseId == null) putNull("responseId") else putString("responseId", status.responseId)
        putDouble("writtenPlaybackFrames", status.writtenPlaybackFrames.toDouble())
        putDouble("presentedPlaybackFrames", status.presentedPlaybackFrames.toDouble())
        putInt("referenceBufferedFrames", status.referenceBufferedFrames)
        putBoolean("referenceReady", status.referenceReady)
        putString("timestampConfidence", status.timestampConfidence)
        putNullableDouble("estimatedDelayMs", status.estimatedDelayMs?.toDouble())
        putNullableDouble("echoSimilarity", status.echoSimilarity)
        putNullableDouble("echoCoherence", status.echoCoherence)
        putNullableDouble("farEndRms", status.farEndRms)
        putNullableDouble("micRms", status.micRms)
        putNullableDouble("nearEndFarEndEnergyRatio", status.nearEndFarEndEnergyRatio)
        putString("lastAssessmentTimestampNs", status.lastAssessmentTimestampNs.toString())
    }

    private fun toWritableBargeInDetectorStatusMap(
        status: PlaybackAwareBargeInDetector.Status,
    ): WritableMap = Arguments.createMap().apply {
        putString("state", status.state.name)
        putString("lastEvent", status.lastEvent)
        putString("lastReason", status.lastReason)
        putDouble("candidateCount", status.candidateCount.toDouble())
        putDouble("rejectedEchoCount", status.rejectedEchoCount.toDouble())
        putDouble("confirmedCount", status.confirmedCount.toDouble())
        putDouble("degradedCount", status.degradedCount.toDouble())
        if (status.lastResponseId == null) putNull("lastResponseId") else putString("lastResponseId", status.lastResponseId)
        putDouble("lastSourceFrameSequenceStart", status.lastSourceFrameSequenceStart.toDouble())
        putDouble("lastSourceFrameSequenceEnd", status.lastSourceFrameSequenceEnd.toDouble())
        putDouble("lastInferenceIndex", status.lastInferenceIndex.toDouble())
        putNullableDouble("lastLocalStopLatencyMs", status.lastLocalStopLatencyMs?.toDouble())
        if (status.lastLocalStopResponseId == null) putNull("lastLocalStopResponseId") else putString("lastLocalStopResponseId", status.lastLocalStopResponseId)
    }

    private fun toWritableDecisionRecordMap(
        record: DiagnosticEvidenceLedger.DecisionRecord,
    ): WritableMap = Arguments.createMap().apply {
        putString("diagnosticSessionId", record.diagnosticSessionId)
        putString("event", record.event)
        putString("state", record.state)
        putString("reason", record.reason)
        if (record.responseId == null) putNull("responseId") else putString("responseId", record.responseId)
        putString("monotonicNs", record.monotonicNs.toString())
        putString("captureStartNs", record.captureStartNs.toString())
        putString("captureEndNs", record.captureEndNs.toString())
        putDouble("sourceFrameSequenceStart", record.sourceFrameSequenceStart.toDouble())
        putDouble("sourceFrameSequenceEnd", record.sourceFrameSequenceEnd.toDouble())
        putDouble("inferenceIndex", record.inferenceIndex.toDouble())
        putDouble("probability", record.probability)
        putString("playbackState", record.playbackState)
        putDouble("playbackPositionMs", record.playbackPositionMs.toDouble())
        putBoolean("referenceReady", record.referenceReady)
        putBoolean("referenceUsable", record.referenceUsable)
        putString("timestampConfidence", record.timestampConfidence)
        putBoolean("aecHealthy", record.aecHealthy)
        putBoolean("communicationModeActive", record.communicationModeActive)
        putBoolean("aecAvailable", record.aecAvailable)
        putBoolean("aecEnabled", record.aecEnabled)
        putString("aecEffectiveness", record.aecEffectiveness)
        putNullableDouble("echoSimilarity", record.echoSimilarity)
        putNullableDouble("echoCoherence", record.echoCoherence)
        putNullableDouble("estimatedDelayMs", record.estimatedDelayMs?.toDouble())
        putNullableDouble("farEndRms", record.farEndRms)
        putNullableDouble("micRms", record.micRms)
        putNullableDouble("nearEndFarEndEnergyRatio", record.nearEndFarEndEnergyRatio)
        putBoolean("discontinuous", record.discontinuous)
        putNullableDouble("localStopLatencyMs", record.localStopLatencyMs?.toDouble())
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
            putString("lastInferenceMonotonicNs", status.lastInferenceMonotonicNs.toString())
            putDouble(
                "lastObservationSourceFrameSequenceStart",
                status.lastObservationSourceFrameSequenceStart.toDouble(),
            )
            putDouble(
                "lastObservationSourceFrameSequenceEnd",
                status.lastObservationSourceFrameSequenceEnd.toDouble(),
            )
            putString("lastObservationCaptureStartNs", status.lastObservationCaptureStartNs.toString())
            putString("lastObservationCaptureEndNs", status.lastObservationCaptureEndNs.toString())
            putBoolean("lastObservationDiscontinuous", status.lastObservationDiscontinuous)
            putDouble("discontinuityCount", status.discontinuityCount.toDouble())
            putBoolean("discontinuityPending", status.discontinuityPending)
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
        const val EVENT_SILERO_VAD_SPEECH_ACTIVITY = SileroVadEngine.EVENT_SPEECH_ACTIVITY
        const val EVENT_SILERO_VAD_ERROR = SileroVadEngine.EVENT_ERROR
        const val EVENT_BARGE_IN_CANDIDATE = PlaybackAwareBargeInDetector.EVENT_CANDIDATE
        const val EVENT_BARGE_IN_REJECTED_ECHO = PlaybackAwareBargeInDetector.EVENT_REJECTED_ECHO
        const val EVENT_BARGE_IN_CONFIRMED = PlaybackAwareBargeInDetector.EVENT_CONFIRMED
        const val EVENT_BARGE_IN_DEGRADED = PlaybackAwareBargeInDetector.EVENT_DEGRADED
        const val EVENT_WAKE_WORD_DETECTED = WakeWordEngine.EVENT_WAKE_WORD_DETECTED
        const val EVENT_WAKE_ENGINE_STARTED = WakeWordEngine.EVENT_ENGINE_STARTED
        const val EVENT_WAKE_ENGINE_STOPPED = WakeWordEngine.EVENT_ENGINE_STOPPED
        const val EVENT_WAKE_ENGINE_ERROR = WakeWordEngine.EVENT_ENGINE_ERROR
        const val EVENT_VOICE_GATEWAY_STATUS = "VOICE_GATEWAY_STATUS"
        const val EVENT_VOICE_GATEWAY_EVENT = "VOICE_GATEWAY_EVENT"
    }
}
