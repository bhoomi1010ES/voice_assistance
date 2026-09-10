package com.voiceaipoc.audio

import android.media.AudioRecord
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AudioEffect
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import android.util.Log

/**
 * Owns Android platform audio effects for one AudioRecord session at a time.
 *
 * Capability detection is always safe to query. Effect creation and
 * enablement happen only after AudioEngine has opened a real AudioRecord and
 * supplied its audio session ID. No PCM crosses this component.
 */
class AudioEffectsManager(
    private val config: Config = Config(),
) {
    data class Config(
        val enableAcousticEchoCancellation: Boolean = true,
        val enableNoiseSuppression: Boolean = true,
    )

    data class EffectStatus(
        /** Android reports that this effect type is implemented on the device. */
        val supported: Boolean,
        /** The effect is supported and the most recent session attach did not fail. */
        val available: Boolean,
        /** Configuration requested enablement for the current/next session. */
        val requested: Boolean,
        /** A live effect instance is currently attached to an AudioRecord session. */
        val created: Boolean,
        /** The attached effect reports itself enabled. */
        val enabled: Boolean,
        val lastError: String?,
    )

    data class Status(
        val audioSessionId: Int,
        val aec: EffectStatus,
        val noiseSuppression: EffectStatus,
        val manufacturer: String,
        val model: String,
        val androidSdk: Int,
    )

    companion object {
        private const val TAG = AudioEngine.TAG
        private const val EFFECT_SUCCESS = AudioEffect.SUCCESS
    }

    private val stateLock = Any()
    private val aecSupported = detectAecSupport()
    private val noiseSuppressionSupported = detectNoiseSuppressionSupport()

    private var audioSessionId = AudioRecord.ERROR_BAD_VALUE
    private var attached = false
    private var acousticEchoCancellationRequested = config.enableAcousticEchoCancellation
    private var noiseSuppressionRequested = config.enableNoiseSuppression
    private var acousticEchoCanceler: AcousticEchoCanceler? = null
    private var noiseSuppressor: NoiseSuppressor? = null
    private var aecCreationSucceeded: Boolean? = null
    private var noiseSuppressionCreationSucceeded: Boolean? = null
    private var aecEnabled = false
    private var noiseSuppressionEnabled = false
    private var aecLastError: String? = null
    private var noiseSuppressionLastError: String? = null

    fun getStatus(): Status = synchronized(stateLock) {
        statusLocked()
    }

    /** Reversible calibration override. Effects can only change between sessions. */
    fun setRequestedEffects(
        enableAcousticEchoCancellation: Boolean,
        enableNoiseSuppression: Boolean,
    ): Status = synchronized(stateLock) {
        check(!attached) { "Audio effects cannot change while attached to AudioRecord" }
        acousticEchoCancellationRequested = enableAcousticEchoCancellation
        noiseSuppressionRequested = enableNoiseSuppression
        resetAttachDiagnosticsLocked()
        Log.i(
            TAG,
            "Audio calibration effects configured: " +
                "AEC requested=$acousticEchoCancellationRequested, " +
                "NS requested=$noiseSuppressionRequested",
        )
        statusLocked()
    }

    /** Attaches configured effects to the existing AudioRecord session. */
    fun attachToAudioSession(sessionId: Int): Status = synchronized(stateLock) {
        if (attached) {
            if (audioSessionId == sessionId) {
                Log.w(TAG, "Audio effects are already attached to audioSessionId=$sessionId")
                return@synchronized statusLocked()
            }
            releaseEffectsLocked()
        }

        audioSessionId = sessionId
        resetAttachDiagnosticsLocked()

        if (sessionId <= 0) {
            val message = "Cannot attach audio effects to invalid audioSessionId=$sessionId"
            if (aecSupported && acousticEchoCancellationRequested) {
                aecCreationSucceeded = false
                aecLastError = message
            }
            if (noiseSuppressionSupported && noiseSuppressionRequested) {
                noiseSuppressionCreationSucceeded = false
                noiseSuppressionLastError = message
            }
            Log.e(TAG, message)
            logStatusLocked()
            return@synchronized statusLocked()
        }

        attached = true
        attachAecLocked(sessionId)
        attachNoiseSuppressorLocked(sessionId)
        logStatusLocked()
        statusLocked()
    }

    /** Releases effects only if they still belong to the expected recorder session. */
    fun releaseForAudioSession(expectedAudioSessionId: Int) = synchronized(stateLock) {
        if (!attached || audioSessionId != expectedAudioSessionId) {
            return@synchronized
        }
        releaseEffectsLocked()
    }

    /** Idempotent lifecycle cleanup used when the engine/module is invalidated. */
    fun release() = synchronized(stateLock) {
        releaseEffectsLocked()
    }

    private fun attachAecLocked(sessionId: Int) {
        if (!aecSupported || !acousticEchoCancellationRequested) {
            return
        }

        val effect = try {
            AcousticEchoCanceler.create(sessionId)
        } catch (exception: RuntimeException) {
            aecCreationSucceeded = false
            aecLastError = "AEC creation failed: ${exception.message}"
            Log.e(TAG, aecLastError!!, exception)
            null
        }

        if (effect == null) {
            aecCreationSucceeded = false
            if (aecLastError == null) {
                aecLastError = "AEC creation returned null for audioSessionId=$sessionId"
                Log.e(TAG, aecLastError!!)
            }
            return
        }

        acousticEchoCanceler = effect
        aecCreationSucceeded = true
        val enableResult = enableEffect("AEC", effect)
        aecEnabled = enableResult.enabled
        aecLastError = enableResult.error
    }

    private fun attachNoiseSuppressorLocked(sessionId: Int) {
        if (!noiseSuppressionSupported || !noiseSuppressionRequested) {
            return
        }

        val effect = try {
            NoiseSuppressor.create(sessionId)
        } catch (exception: RuntimeException) {
            noiseSuppressionCreationSucceeded = false
            noiseSuppressionLastError = "NS creation failed: ${exception.message}"
            Log.e(TAG, noiseSuppressionLastError!!, exception)
            null
        }

        if (effect == null) {
            noiseSuppressionCreationSucceeded = false
            if (noiseSuppressionLastError == null) {
                noiseSuppressionLastError = "NS creation returned null for audioSessionId=$sessionId"
                Log.e(TAG, noiseSuppressionLastError!!)
            }
            return
        }

        noiseSuppressor = effect
        noiseSuppressionCreationSucceeded = true
        val enableResult = enableEffect("NS", effect)
        noiseSuppressionEnabled = enableResult.enabled
        noiseSuppressionLastError = enableResult.error
    }

    private fun enableEffect(name: String, effect: AudioEffect): EnableResult {
        return try {
            val resultCode = effect.setEnabled(true)
            val enabled = effect.enabled
            if (resultCode == EFFECT_SUCCESS && enabled) {
                EnableResult(enabled = true, error = null)
            } else {
                val message = "$name enable failed: resultCode=$resultCode, enabled=$enabled"
                Log.e(TAG, message)
                EnableResult(enabled = enabled, error = message)
            }
        } catch (exception: RuntimeException) {
            val message = "$name enable failed: ${exception.message}"
            Log.e(TAG, message, exception)
            EnableResult(enabled = false, error = message)
        }
    }

    private fun releaseEffectsLocked() {
        val releasedSessionId = audioSessionId
        releaseAecLocked(releasedSessionId)
        releaseNoiseSuppressorLocked(releasedSessionId)
        if (attached) {
            Log.i(TAG, "Audio effects released for audioSessionId=$releasedSessionId")
        }
        attached = false
        aecEnabled = false
        noiseSuppressionEnabled = false
    }

    private fun releaseAecLocked(sessionId: Int) {
        val effect = acousticEchoCanceler ?: return
        acousticEchoCanceler = null
        try {
            if (effect.enabled) {
                effect.setEnabled(false)
            }
        } catch (exception: RuntimeException) {
            aecLastError = "AEC disable during release failed: ${exception.message}"
            Log.e(TAG, aecLastError!!, exception)
        }
        try {
            effect.release()
            Log.i(TAG, "AEC released for audioSessionId=$sessionId")
        } catch (exception: RuntimeException) {
            aecLastError = "AEC release failed: ${exception.message}"
            Log.e(TAG, aecLastError!!, exception)
        }
    }

    private fun releaseNoiseSuppressorLocked(sessionId: Int) {
        val effect = noiseSuppressor ?: return
        noiseSuppressor = null
        try {
            if (effect.enabled) {
                effect.setEnabled(false)
            }
        } catch (exception: RuntimeException) {
            noiseSuppressionLastError = "NS disable during release failed: ${exception.message}"
            Log.e(TAG, noiseSuppressionLastError!!, exception)
        }
        try {
            effect.release()
            Log.i(TAG, "NS released for audioSessionId=$sessionId")
        } catch (exception: RuntimeException) {
            noiseSuppressionLastError = "NS release failed: ${exception.message}"
            Log.e(TAG, noiseSuppressionLastError!!, exception)
        }
    }

    private fun resetAttachDiagnosticsLocked() {
        aecCreationSucceeded = null
        noiseSuppressionCreationSucceeded = null
        aecEnabled = false
        noiseSuppressionEnabled = false
        aecLastError = null
        noiseSuppressionLastError = null
    }

    private fun statusLocked(): Status = Status(
        audioSessionId = audioSessionId,
        aec = EffectStatus(
            supported = aecSupported,
            available = aecSupported && aecCreationSucceeded != false,
            requested = acousticEchoCancellationRequested,
            created = acousticEchoCanceler != null,
            enabled = aecEnabled,
            lastError = aecLastError,
        ),
        noiseSuppression = EffectStatus(
            supported = noiseSuppressionSupported,
            available = noiseSuppressionSupported && noiseSuppressionCreationSucceeded != false,
            requested = noiseSuppressionRequested,
            created = noiseSuppressor != null,
            enabled = noiseSuppressionEnabled,
            lastError = noiseSuppressionLastError,
        ),
        manufacturer = Build.MANUFACTURER.orEmpty(),
        model = Build.MODEL.orEmpty(),
        androidSdk = Build.VERSION.SDK_INT,
    )

    private fun logStatusLocked() {
        val status = statusLocked()
        Log.i(TAG, "Audio session ID=${status.audioSessionId}")
        Log.i(TAG, "AEC supported=${status.aec.supported}")
        Log.i(TAG, "AEC available=${status.aec.available}")
        Log.i(TAG, "AEC enabled=${status.aec.enabled}")
        Log.i(TAG, "NS supported=${status.noiseSuppression.supported}")
        Log.i(TAG, "NS available=${status.noiseSuppression.available}")
        Log.i(TAG, "NS enabled=${status.noiseSuppression.enabled}")
    }

    private fun detectAecSupport(): Boolean = try {
        AcousticEchoCanceler.isAvailable()
    } catch (exception: RuntimeException) {
        Log.e(TAG, "AEC capability detection failed", exception)
        false
    }

    private fun detectNoiseSuppressionSupport(): Boolean = try {
        NoiseSuppressor.isAvailable()
    } catch (exception: RuntimeException) {
        Log.e(TAG, "NS capability detection failed", exception)
        false
    }

    private data class EnableResult(
        val enabled: Boolean,
        val error: String?,
    )
}
