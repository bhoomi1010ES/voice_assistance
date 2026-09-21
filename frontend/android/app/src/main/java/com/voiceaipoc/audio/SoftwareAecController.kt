package com.voiceaipoc.audio

import android.os.SystemClock
import android.util.Log
import kotlin.math.sqrt

/**
 * Owns the 20 ms capture -> 10 ms APM framing boundary and reference lookup.
 * It is allocation-free during steady-state processing.
 */
class SoftwareAecController(
    private val mode: AudioConfig.SoftwareAecMode,
    private val enabled: Boolean = mode != AudioConfig.SoftwareAecMode.PLATFORM,
    private val sampleRateHz: Int,
    private val renderToCaptureDelayMs: Int,
    private val enableAec: Boolean,
    private val enableNoiseSuppression: Boolean,
    private val cancellerFactory: () -> EchoCanceller = { WebRtcAec3EchoCanceller() },
    private val nanoClock: () -> Long = SystemClock::elapsedRealtimeNanos,
) {
    enum class State { DISABLED, STARTING, ACTIVE, DEGRADED, STOPPED }

    data class Status(
        val requestedMode: String,
        val state: State,
        val implementation: String,
        val sampleRateHz: Int,
        val frameDurationMs: Int,
        val frameSizeSamples: Int,
        val renderToCaptureDelayMs: Int,
        val aecRequested: Boolean,
        val noiseSuppressionRequested: Boolean,
        val platformAecDisabled: Boolean,
        val platformNoiseSuppressionDisabled: Boolean,
        val referenceReadyFrames: Long,
        val referenceMissingFrames: Long,
        val captureFrames: Long,
        val renderFrames: Long,
        val processedFrames: Long,
        val bypassedFrames: Long,
        val droppedFrames: Long,
        val processingErrorCount: Long,
        val lastReferenceConfidence: String,
        val lastFarEndRms: Double,
        val lastInputRms: Double,
        val lastOutputRms: Double,
        val lastError: String?,
    )

    private val frameSizeSamples = sampleRateHz / 100
    private val renderScratch = ShortArray(frameSizeSamples.coerceAtLeast(1))
    private val outputScratch = ShortArray(frameSizeSamples.coerceAtLeast(1))
    private val referenceWindow = FarEndReferenceBuffer.ReferenceWindow()
    private var canceller: EchoCanceller? = null
    private var state = if (!enabled) {
        State.DISABLED
    } else {
        State.STOPPED
    }
    private var implementation = if (!enabled) {
        "PLATFORM_AEC"
    } else {
        "NOT_STARTED"
    }
    private var referenceReadyFrames = 0L
    private var referenceMissingFrames = 0L
    private var captureFrames = 0L
    private var renderFrames = 0L
    private var processedFrames = 0L
    private var bypassedFrames = 0L
    private var droppedFrames = 0L
    private var processingErrorCount = 0L
    private var lastReferenceConfidence = "NONE"
    private var lastFarEndRms = 0.0
    private var lastInputRms = 0.0
    private var lastOutputRms = 0.0
    private var lastError: String? = null

    init {
        require(sampleRateHz > 0 && sampleRateHz % 100 == 0) {
            "Software AEC requires a sample rate that produces a 10 ms frame"
        }
        require(renderToCaptureDelayMs in 0..500) {
            "Software AEC render-to-capture delay must be 0..500 ms"
        }
    }

    fun start(): Status {
        if (!enabled) return status()
        stop()
        state = State.STARTING
        val next = cancellerFactory()
        canceller = next
        val started = runCatching {
            next.start(
                EchoCanceller.Config(
                    sampleRateHz = sampleRateHz,
                    channelCount = 1,
                    frameSizeSamples = frameSizeSamples,
                    streamDelayMs = renderToCaptureDelayMs,
                    enableAec = enableAec,
                    enableNoiseSuppression = enableNoiseSuppression,
                ),
            )
        }.getOrElse { exception ->
            lastError = "AEC3 start failed: ${exception.message}"
            false
        }
        if (started) {
            state = State.ACTIVE
            implementation = next.metrics().implementation
            lastError = null
        } else {
            state = State.DEGRADED
            val metrics = next.metrics()
            implementation = metrics.implementation
            lastError = metrics.lastError ?: lastError ?: "AEC3 backend unavailable"
            Log.w(TAG, "Software AEC degraded: $lastError")
        }
        return status()
    }

    /**
     * Processes one capture frame. The caller owns [pcm] and [output] and may
     * reuse them immediately after this method returns.
     */
    fun processFrame(
        pcm: ShortArray,
        samplesRead: Int,
        captureStartNs: Long,
        captureEndNs: Long,
        referenceBuffer: FarEndReferenceBuffer,
        output: ShortArray,
    ): Boolean {
        if (samplesRead <= 0 || samplesRead > pcm.size || samplesRead > output.size) return false
        if (state != State.ACTIVE) {
            pcm.copyInto(output, 0, 0, samplesRead)
            bypassedFrames += 1
            lastInputRms = rms(pcm, 0, samplesRead)
            lastOutputRms = lastInputRms
            return false
        }
        if (samplesRead % frameSizeSamples != 0) {
            pcm.copyInto(output, 0, 0, samplesRead)
            droppedFrames += 1
            lastError = "Capture frame is not an integer number of 10 ms AEC frames"
            degrade()
            return false
        }

        var offset = 0
        while (offset < samplesRead) {
            val chunkStartNs = captureStartNs +
                offset.toLong() * 1_000_000_000L / sampleRateHz.toLong()
            referenceBuffer.copyPresentationReference(
                output = renderScratch,
                outputOffset = 0,
                sampleCount = frameSizeSamples,
                captureStartNs = chunkStartNs,
                sampleRateHz = sampleRateHz,
                delayMs = renderToCaptureDelayMs,
                result = referenceWindow,
            )
            lastReferenceConfidence = referenceWindow.timestampConfidence
            lastFarEndRms = referenceWindow.farEndRms
            if (referenceWindow.referenceReady) referenceReadyFrames += 1 else referenceMissingFrames += 1

            val renderResult = canceller?.processRender(
                renderScratch,
                0,
                frameSizeSamples,
                referenceWindow.firstSampleTimestampNs,
                referenceWindow.referenceReady,
            ) ?: EchoCanceller.PROCESS_ERROR
            renderFrames += 1
            if (renderResult != EchoCanceller.PROCESS_OK) {
                droppedFrames += 1
                degrade()
                pcm.copyInto(output, 0, 0, samplesRead)
                lastInputRms = rms(pcm, 0, samplesRead)
                lastOutputRms = lastInputRms
                return false
            }

            pcm.copyInto(outputScratch, 0, offset, offset + frameSizeSamples)
            val captureResult = canceller?.processCapture(
                input = outputScratch,
                inputOffset = 0,
                output = outputScratch,
                outputOffset = 0,
                count = frameSizeSamples,
                captureTimestampNs = chunkStartNs,
            ) ?: EchoCanceller.PROCESS_ERROR
            captureFrames += 1
            if (captureResult == EchoCanceller.PROCESS_OK) {
                outputScratch.copyInto(output, offset, 0, frameSizeSamples)
                processedFrames += 1
            } else {
                droppedFrames += 1
                degrade()
                pcm.copyInto(output, 0, 0, samplesRead)
                lastInputRms = rms(pcm, 0, samplesRead)
                lastOutputRms = lastInputRms
                return false
            }
            offset += frameSizeSamples
        }
        lastInputRms = rms(pcm, 0, samplesRead)
        lastOutputRms = rms(output, 0, samplesRead)
        return true
    }

    fun stop() {
        canceller?.stop()
        canceller = null
        if (!enabled) {
            state = State.DISABLED
            implementation = "PLATFORM_AEC"
        } else {
            state = State.STOPPED
            implementation = "NOT_STARTED"
        }
    }

    fun status(): Status {
        val metrics = canceller?.metrics()
        return Status(
            requestedMode = mode.name,
            state = state,
            implementation = implementation,
            sampleRateHz = sampleRateHz,
            frameDurationMs = 10,
            frameSizeSamples = frameSizeSamples,
            renderToCaptureDelayMs = renderToCaptureDelayMs,
            aecRequested = enableAec,
            noiseSuppressionRequested = enableNoiseSuppression,
            platformAecDisabled = enabled,
            platformNoiseSuppressionDisabled = enabled && enableNoiseSuppression,
            referenceReadyFrames = referenceReadyFrames,
            referenceMissingFrames = referenceMissingFrames,
            captureFrames = metrics?.captureFrames ?: captureFrames,
            renderFrames = metrics?.renderFrames ?: renderFrames,
            processedFrames = metrics?.processedFrames ?: processedFrames,
            bypassedFrames = metrics?.bypassedFrames ?: bypassedFrames,
            droppedFrames = droppedFrames + (metrics?.renderDropCount ?: 0L),
            processingErrorCount = processingErrorCount + (metrics?.processingErrorCount ?: 0L),
            lastReferenceConfidence = lastReferenceConfidence,
            lastFarEndRms = lastFarEndRms,
            lastInputRms = metrics?.lastInputRms ?: lastInputRms,
            lastOutputRms = metrics?.lastOutputRms ?: lastOutputRms,
            lastError = metrics?.lastError ?: lastError,
        )
    }

    private fun degrade() {
        state = State.DEGRADED
        val error = canceller?.metrics()?.lastError
        if (error != null) lastError = error
        implementation = canceller?.metrics()?.implementation ?: implementation
        Log.w(TAG, "Software AEC watchdog entered degraded bypass: ${lastError ?: "unknown"}")
    }

    private fun rms(samples: ShortArray, offset: Int, count: Int): Double {
        var energy = 0.0
        for (index in offset until offset + count) {
            val sample = samples[index].toDouble()
            energy += sample * sample
        }
        return sqrt(energy / count.toDouble())
    }

    private companion object {
        const val TAG = "VoiceAI-SoftwareAEC"
    }
}
