package com.voiceaipoc.audio

import android.util.Log
import kotlin.math.sqrt

/**
 * JNI-owned WebRTC AudioProcessing/AEC3 adapter.
 *
 * The JNI library is always safe to load, but reports unavailable until a
 * pinned WebRTC APM backend is supplied through the CMake integration hook.
 * This class never substitutes a home-grown subtraction filter for AEC3.
 */
class WebRtcAec3EchoCanceller : EchoCanceller {
    private val lock = Any()

    private var nativeHandle = 0L
    private var currentConfig: EchoCanceller.Config? = null
    private var state = EchoCanceller.State.STOPPED
    private var implementation = IMPLEMENTATION_UNAVAILABLE
    private var renderFrames = 0L
    private var captureFrames = 0L
    private var processedFrames = 0L
    private var bypassedFrames = 0L
    private var renderDropCount = 0L
    private var processingErrorCount = 0L
    private var lastInputRms = 0.0
    private var lastOutputRms = 0.0
    private var lastError: String? = null

    override fun start(config: EchoCanceller.Config): Boolean = synchronized(lock) {
        stopLocked()
        validateConfig(config)
        currentConfig = config
        state = EchoCanceller.State.STARTING

        if (!ensureNativeLibraryLoadedLocked() || !nativeBackendAvailable()) {
            state = EchoCanceller.State.DEGRADED
            implementation = IMPLEMENTATION_UNAVAILABLE
            lastError = nativeLoadError ?: "Pinned WebRTC AEC3 backend is not packaged"
            Log.w(TAG, "WebRTC AEC3 unavailable; capture will use conservative bypass",)
            return@synchronized false
        }

        nativeHandle = runCatching {
            nativeCreate(
                config.sampleRateHz,
                config.channelCount,
                config.frameSizeSamples,
                config.streamDelayMs,
                config.enableAec,
                config.enableNoiseSuppression,
            )
        }.getOrElse { exception ->
            lastError = "Native AEC3 create failed: ${exception.message}"
            0L
        }

        if (nativeHandle == 0L) {
            state = EchoCanceller.State.DEGRADED
            implementation = IMPLEMENTATION_UNAVAILABLE
            if (lastError == null) lastError = "Native AEC3 create returned null"
            return@synchronized false
        }

        state = EchoCanceller.State.ACTIVE
        implementation = nativeBackendName()
        lastError = null
        true
    }

    override fun processRender(
        pcm: ShortArray,
        offset: Int,
        count: Int,
        presentationTimestampNs: Long,
        referenceReady: Boolean,
    ): Int = synchronized(lock) {
        val config = currentConfig
        if (state != EchoCanceller.State.ACTIVE || nativeHandle == 0L || config == null) {
            renderDropCount += 1
            return@synchronized EchoCanceller.PROCESS_BYPASSED
        }
        if (!validFrame(pcm, offset, count, config.frameSizeSamples)) {
            renderDropCount += 1
            processingErrorCount += 1
            lastError = "Invalid render frame bounds"
            degradeLocked(lastError!!)
            return@synchronized EchoCanceller.PROCESS_ERROR
        }

        val result = runCatching {
            nativeProcessRender(nativeHandle, pcm, offset, count, presentationTimestampNs, referenceReady)
        }.getOrElse { exception ->
            lastError = "Native AEC3 render failed: ${exception.message}"
            -1
        }
        renderFrames += 1
        if (result != 0) {
            renderDropCount += 1
            degradeLocked(lastError ?: "Native AEC3 render returned $result")
            EchoCanceller.PROCESS_ERROR
        } else {
            EchoCanceller.PROCESS_OK
        }
    }

    override fun processCapture(
        input: ShortArray,
        inputOffset: Int,
        output: ShortArray,
        outputOffset: Int,
        count: Int,
        captureTimestampNs: Long,
    ): Int = synchronized(lock) {
        val config = currentConfig
        if (config == null || !validFrame(input, inputOffset, count, config.frameSizeSamples) ||
            !validFrame(output, outputOffset, count, config.frameSizeSamples)
        ) {
            processingErrorCount += 1
            lastError = "Invalid capture frame bounds"
            copyIfPossible(input, inputOffset, output, outputOffset, count)
            degradeLocked(lastError!!)
            return@synchronized EchoCanceller.PROCESS_ERROR
        }

        lastInputRms = rms(input, inputOffset, count)
        if (state != EchoCanceller.State.ACTIVE || nativeHandle == 0L) {
            input.copyInto(output, outputOffset, inputOffset, inputOffset + count)
            lastOutputRms = lastInputRms
            bypassedFrames += 1
            captureFrames += 1
            return@synchronized EchoCanceller.PROCESS_BYPASSED
        }

        val result = runCatching {
            nativeProcessCapture(
                nativeHandle,
                input,
                inputOffset,
                output,
                outputOffset,
                count,
                captureTimestampNs,
            )
        }.getOrElse { exception ->
            lastError = "Native AEC3 capture failed: ${exception.message}"
            -1
        }
        captureFrames += 1
        if (result != 0) {
            copyIfPossible(input, inputOffset, output, outputOffset, count)
            lastOutputRms = lastInputRms
            processingErrorCount += 1
            degradeLocked(lastError ?: "Native AEC3 capture returned $result")
            EchoCanceller.PROCESS_ERROR
        } else {
            lastOutputRms = rms(output, outputOffset, count)
            processedFrames += 1
            EchoCanceller.PROCESS_OK
        }
    }

    override fun reset() = synchronized(lock) {
        if (nativeHandle != 0L) runCatching { nativeReset(nativeHandle) }
        if (state == EchoCanceller.State.ACTIVE) {
            renderFrames = 0L
            captureFrames = 0L
            processedFrames = 0L
            bypassedFrames = 0L
            renderDropCount = 0L
            processingErrorCount = 0L
            lastInputRms = 0.0
            lastOutputRms = 0.0
            lastError = null
        }
    }

    override fun stop() = synchronized(lock) {
        stopLocked()
    }

    override fun metrics(): EchoCanceller.Metrics = synchronized(lock) {
        val config = currentConfig
        EchoCanceller.Metrics(
            state = state,
            implementation = implementation,
            sampleRateHz = config?.sampleRateHz ?: 0,
            frameSizeSamples = config?.frameSizeSamples ?: 0,
            streamDelayMs = config?.streamDelayMs ?: 0,
            aecRequested = config?.enableAec == true,
            noiseSuppressionRequested = config?.enableNoiseSuppression == true,
            renderFrames = renderFrames,
            captureFrames = captureFrames,
            processedFrames = processedFrames,
            bypassedFrames = bypassedFrames,
            renderDropCount = renderDropCount,
            processingErrorCount = processingErrorCount,
            lastInputRms = lastInputRms,
            lastOutputRms = lastOutputRms,
            lastError = lastError,
        )
    }

    private fun stopLocked() {
        if (nativeHandle != 0L) runCatching { nativeDestroy(nativeHandle) }
        nativeHandle = 0L
        currentConfig = null
        if (state != EchoCanceller.State.DISABLED) state = EchoCanceller.State.STOPPED
    }

    private fun degradeLocked(message: String) {
        lastError = message
        state = EchoCanceller.State.DEGRADED
        if (nativeHandle != 0L) runCatching { nativeReset(nativeHandle) }
    }

    private fun validateConfig(config: EchoCanceller.Config) {
        require(config.sampleRateHz in SUPPORTED_SAMPLE_RATES) { "Unsupported AEC3 sample rate" }
        require(config.channelCount == 1) { "AEC3 adapter currently requires mono PCM" }
        require(config.frameSizeSamples == config.sampleRateHz / 100) {
            "AEC3 adapter requires 10 ms frames"
        }
        require(config.streamDelayMs in 0..500) { "AEC3 stream delay must be 0..500 ms" }
    }

    private fun validFrame(array: ShortArray, offset: Int, count: Int, expectedCount: Int): Boolean =
        count == expectedCount && offset >= 0 && offset + count <= array.size

    private fun copyIfPossible(
        input: ShortArray,
        inputOffset: Int,
        output: ShortArray,
        outputOffset: Int,
        count: Int,
    ) {
        if (inputOffset !in 0..input.size || outputOffset !in 0..output.size) return
        val safeCount = minOf(
            count.coerceAtLeast(0),
            input.size - inputOffset,
            output.size - outputOffset,
        )
        if (safeCount > 0) {
            input.copyInto(output, outputOffset, inputOffset, inputOffset + safeCount)
        }
    }

    private fun rms(array: ShortArray, offset: Int, count: Int): Double {
        if (count <= 0) return 0.0
        var energy = 0.0
        for (index in offset until offset + count) {
            val sample = array[index].toDouble()
            energy += sample * sample
        }
        return sqrt(energy / count.toDouble())
    }

    private fun ensureNativeLibraryLoadedLocked(): Boolean {
        if (nativeLoadAttempted) return nativeLoadSucceeded
        nativeLoadAttempted = true
        return try {
            System.loadLibrary(LIBRARY_NAME)
            nativeLoadSucceeded = true
            true
        } catch (error: UnsatisfiedLinkError) {
            nativeLoadError = "Unable to load $LIBRARY_NAME: ${error.message}"
            nativeLoadSucceeded = false
            false
        }
    }

    private external fun nativeBackendAvailable(): Boolean
    private external fun nativeBackendName(): String
    private external fun nativeCreate(
        sampleRateHz: Int,
        channelCount: Int,
        frameSizeSamples: Int,
        streamDelayMs: Int,
        enableAec: Boolean,
        enableNoiseSuppression: Boolean,
    ): Long
    private external fun nativeProcessRender(
        handle: Long,
        pcm: ShortArray,
        offset: Int,
        count: Int,
        presentationTimestampNs: Long,
        referenceReady: Boolean,
    ): Int
    private external fun nativeProcessCapture(
        handle: Long,
        input: ShortArray,
        inputOffset: Int,
        output: ShortArray,
        outputOffset: Int,
        count: Int,
        captureTimestampNs: Long,
    ): Int
    private external fun nativeReset(handle: Long)
    private external fun nativeDestroy(handle: Long)

    companion object {
        const val TAG = "VoiceAI-AEC3"
        const val LIBRARY_NAME = "voice_aec3_jni"
        const val IMPLEMENTATION_UNAVAILABLE = "WEBRTC_AEC3_UNAVAILABLE"
        val SUPPORTED_SAMPLE_RATES = setOf(8_000, 16_000, 32_000, 48_000)
        @Volatile var nativeLoadAttempted = false
        @Volatile var nativeLoadSucceeded = false
        @Volatile var nativeLoadError: String? = null

        fun isNativeBackendAvailable(): Boolean {
            if (!nativeLoadAttempted) {
                try {
                    System.loadLibrary(LIBRARY_NAME)
                    nativeLoadSucceeded = true
                } catch (error: UnsatisfiedLinkError) {
                    nativeLoadError = "Unable to load $LIBRARY_NAME: ${error.message}"
                    nativeLoadSucceeded = false
                } finally {
                    nativeLoadAttempted = true
                }
            }
            if (!nativeLoadSucceeded) return false
            return runCatching { nativeBackendAvailableStatic() }.getOrDefault(false)
        }

        private external fun nativeBackendAvailableStatic(): Boolean
    }
}
