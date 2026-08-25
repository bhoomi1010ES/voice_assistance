package com.voiceaipoc.vad

import android.util.Log
import com.voiceaipoc.audio.AudioEngine
import kotlin.math.ceil
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Allocation-free-per-frame energy VAD for signed PCM16 mono frames.
 *
 * RMS is converted to finite dBFS and compared with a configurable threshold.
 * Consecutive-frame confirmation prevents isolated loud/quiet frames from
 * immediately changing the higher-level SILENCE/SPEECH state.
 */
class VadEngine(
    private val config: VadConfig,
    private val frameDurationMs: Int,
    private val frameSizeSamples: Int,
    private val listener: Listener? = null,
) {
    enum class State {
        SILENCE,
        SPEECH,
    }

    enum class FrameClassification {
        NON_SPEECH,
        SPEECH,
    }

    interface Listener {
        fun onSpeechStarted(event: Event)
        fun onSpeechStopped(event: Event)
    }

    data class Event(
        val event: String,
        val timestampMs: Long,
        val frameIndex: Long,
        val energyDbFs: Double,
        val speechDurationMs: Long,
        val speechSegmentCount: Long,
        val reason: String,
    )

    data class Status(
        val enabled: Boolean,
        val sessionActive: Boolean,
        val state: String,
        val thresholdDbFs: Double,
        val lastEnergyDbFs: Double,
        val lastFrameClassification: String,
        val frameDurationMs: Int,
        val frameSizeSamples: Int,
        val minimumSpeechDurationMs: Int,
        val minimumSilenceDurationMs: Int,
        val configuredSpeechStartConfirmationFrames: Int,
        val configuredSpeechEndConfirmationFrames: Int,
        val effectiveSpeechStartConfirmationFrames: Int,
        val effectiveSpeechEndConfirmationFrames: Int,
        val consecutiveSpeechFrames: Int,
        val consecutiveSilenceFrames: Int,
        val vadFramesProcessed: Long,
        val speechFrames: Long,
        val nonSpeechFrames: Long,
        val speechSegments: Long,
        val currentSpeechDurationMs: Long,
        val currentSilenceDurationMs: Long,
        val lastSpeechStartedFrameIndex: Long,
        val lastSpeechStoppedFrameIndex: Long,
        val vadErrorCount: Long,
    )

    companion object {
        const val EVENT_SPEECH_STARTED = "VAD_SPEECH_STARTED"
        const val EVENT_SPEECH_STOPPED = "VAD_SPEECH_STOPPED"

        private const val REASON_SPEECH_CONFIRMED = "SPEECH_CONFIRMED"
        private const val REASON_SILENCE_CONFIRMED = "SILENCE_CONFIRMED"
        private const val REASON_SESSION_STOPPED = "SESSION_STOPPED"
        private const val PCM16_FULL_SCALE = 32_768.0
        private const val SILENCE_FLOOR_DBFS = -120.0
    }

    private val lock = Any()
    private val effectiveSpeechStartFrames: Int
    private val effectiveSpeechEndFrames: Int

    private var sessionActive = false
    private var state = State.SILENCE
    private var lastFrameClassification = FrameClassification.NON_SPEECH
    private var lastEnergyDbFs = SILENCE_FLOOR_DBFS
    private var consecutiveSpeechFrames = 0
    private var consecutiveSilenceFrames = 0
    private var currentSpeechStateFrames = 0L
    private var currentSilenceStateFrames = 0L
    private var vadFramesProcessed = 0L
    private var speechFrames = 0L
    private var nonSpeechFrames = 0L
    private var speechSegments = 0L
    private var lastSpeechStartedFrameIndex = 0L
    private var lastSpeechStoppedFrameIndex = 0L
    private var vadErrorCount = 0L

    init {
        require(frameDurationMs > 0) { "frameDurationMs must be positive" }
        require(frameSizeSamples > 0) { "frameSizeSamples must be positive" }
        effectiveSpeechStartFrames = max(
            config.speechStartConfirmationFrames,
            durationToFrames(config.minimumSpeechDurationMs),
        )
        effectiveSpeechEndFrames = max(
            config.speechEndConfirmationFrames,
            durationToFrames(config.minimumSilenceDurationMs),
        )
    }

    /** Clears all prior-session state and activates VAD for a new capture. */
    fun startSession() {
        synchronized(lock) {
            resetAllLocked()
            sessionActive = true
        }

        Log.i(
            AudioEngine.TAG,
            "VAD initialized: enabled=${config.enabled}, thresholdDbFs=${config.speechThresholdDbFs}, " +
                "frameDurationMs=$frameDurationMs, frameSamples=$frameSizeSamples, " +
                "speechStartFrames=$effectiveSpeechStartFrames, " +
                "speechEndFrames=$effectiveSpeechEndFrames",
        )
    }

    /**
     * Ends the active session, forces SILENCE, and clears transient state.
     * Aggregate counters remain available for stopped-session diagnostics and
     * are reset by the next [startSession].
     */
    fun stopSession() {
        var stopEvent: Event? = null
        val summary: Status

        synchronized(lock) {
            if (!sessionActive) {
                return
            }
            if (sessionActive && state == State.SPEECH) {
                stopEvent = buildSpeechStoppedEventLocked(REASON_SESSION_STOPPED)
                lastSpeechStoppedFrameIndex = vadFramesProcessed
            }
            sessionActive = false
            state = State.SILENCE
            lastFrameClassification = FrameClassification.NON_SPEECH
            consecutiveSpeechFrames = 0
            consecutiveSilenceFrames = 0
            currentSpeechStateFrames = 0L
            currentSilenceStateFrames = 0L
            summary = statusLocked()
        }

        stopEvent?.let(::emitSpeechStopped)
        Log.i(
            AudioEngine.TAG,
            "VAD stopped/reset: frames=${summary.vadFramesProcessed}, " +
                "speechFrames=${summary.speechFrames}, nonSpeechFrames=${summary.nonSpeechFrames}, " +
                "segments=${summary.speechSegments}, errors=${summary.vadErrorCount}",
        )
    }

    /**
     * Classifies exactly one deterministic processing frame. The input is read
     * only and is never retained or modified.
     */
    fun processFrame(pcmSamples: ShortArray, samplesRead: Int): FrameClassification {
        if (!config.enabled || !isSessionActive()) {
            return FrameClassification.NON_SPEECH
        }

        if (samplesRead != frameSizeSamples || samplesRead > pcmSamples.size) {
            val errors = synchronized(lock) {
                vadErrorCount += 1L
                vadErrorCount
            }
            Log.e(
                AudioEngine.TAG,
                "VAD rejected frame: samplesRead=$samplesRead, expected=$frameSizeSamples, " +
                    "bufferSamples=${pcmSamples.size}, errorCount=$errors",
            )
            return FrameClassification.NON_SPEECH
        }

        val energyDbFs = calculateEnergyDbFs(pcmSamples, samplesRead)
        val classification = if (energyDbFs >= config.speechThresholdDbFs) {
            FrameClassification.SPEECH
        } else {
            FrameClassification.NON_SPEECH
        }
        var transitionEvent: Event? = null

        synchronized(lock) {
            if (!sessionActive) {
                return FrameClassification.NON_SPEECH
            }

            vadFramesProcessed += 1L
            lastEnergyDbFs = energyDbFs
            lastFrameClassification = classification

            if (classification == FrameClassification.SPEECH) {
                speechFrames += 1L
                consecutiveSpeechFrames += 1
                consecutiveSilenceFrames = 0
            } else {
                nonSpeechFrames += 1L
                consecutiveSilenceFrames += 1
                consecutiveSpeechFrames = 0
            }

            when (state) {
                State.SILENCE -> {
                    currentSilenceStateFrames += 1L
                    if (
                        classification == FrameClassification.SPEECH &&
                        consecutiveSpeechFrames >= effectiveSpeechStartFrames
                    ) {
                        state = State.SPEECH
                        speechSegments += 1L
                        currentSpeechStateFrames = consecutiveSpeechFrames.toLong()
                        currentSilenceStateFrames = 0L
                        lastSpeechStartedFrameIndex = vadFramesProcessed
                        transitionEvent = Event(
                            event = EVENT_SPEECH_STARTED,
                            timestampMs = System.currentTimeMillis(),
                            frameIndex = vadFramesProcessed,
                            energyDbFs = lastEnergyDbFs,
                            speechDurationMs = currentSpeechStateFrames * frameDurationMs,
                            speechSegmentCount = speechSegments,
                            reason = REASON_SPEECH_CONFIRMED,
                        )
                    }
                }

                State.SPEECH -> {
                    currentSpeechStateFrames += 1L
                    if (
                        classification == FrameClassification.NON_SPEECH &&
                        consecutiveSilenceFrames >= effectiveSpeechEndFrames
                    ) {
                        transitionEvent = buildSpeechStoppedEventLocked(REASON_SILENCE_CONFIRMED)
                        state = State.SILENCE
                        currentSilenceStateFrames = consecutiveSilenceFrames.toLong()
                        currentSpeechStateFrames = 0L
                        lastSpeechStoppedFrameIndex = vadFramesProcessed
                    }
                }
            }
        }

        transitionEvent?.let { event ->
            if (event.event == EVENT_SPEECH_STARTED) {
                emitSpeechStarted(event)
            } else {
                emitSpeechStopped(event)
            }
        }

        return classification
    }

    fun getStatus(): Status = synchronized(lock) { statusLocked() }

    private fun calculateEnergyDbFs(pcmSamples: ShortArray, samplesRead: Int): Double {
        var sumSquares = 0L
        var index = 0
        while (index < samplesRead) {
            val sample = pcmSamples[index].toLong()
            sumSquares += sample * sample
            index += 1
        }

        if (sumSquares == 0L) {
            return SILENCE_FLOOR_DBFS
        }

        val rms = sqrt(sumSquares.toDouble() / samplesRead.toDouble())
        return (20.0 * log10(rms / PCM16_FULL_SCALE))
            .coerceIn(SILENCE_FLOOR_DBFS, VadConfig.MAX_DBFS)
    }

    private fun buildSpeechStoppedEventLocked(reason: String): Event {
        val trailingSilenceFrames = consecutiveSilenceFrames.toLong()
        val confirmedSpeechFrames = (currentSpeechStateFrames - trailingSilenceFrames)
            .coerceAtLeast(effectiveSpeechStartFrames.toLong())
        return Event(
            event = EVENT_SPEECH_STOPPED,
            timestampMs = System.currentTimeMillis(),
            frameIndex = vadFramesProcessed,
            energyDbFs = lastEnergyDbFs,
            speechDurationMs = confirmedSpeechFrames * frameDurationMs,
            speechSegmentCount = speechSegments,
            reason = reason,
        )
    }

    private fun emitSpeechStarted(event: Event) {
        Log.i(
            AudioEngine.TAG,
            "VAD SPEECH_STARTED: frameIndex=${event.frameIndex}, " +
                "energyDbFs=${formatDb(event.energyDbFs)}, segment=${event.speechSegmentCount}",
        )
        listener?.onSpeechStarted(event)
    }

    private fun emitSpeechStopped(event: Event) {
        Log.i(
            AudioEngine.TAG,
            "VAD SPEECH_STOPPED: frameIndex=${event.frameIndex}, " +
                "energyDbFs=${formatDb(event.energyDbFs)}, " +
                "speechDurationMs=${event.speechDurationMs}, reason=${event.reason}",
        )
        listener?.onSpeechStopped(event)
    }

    private fun statusLocked(): Status = Status(
        enabled = config.enabled,
        sessionActive = sessionActive,
        state = state.name,
        thresholdDbFs = config.speechThresholdDbFs,
        lastEnergyDbFs = lastEnergyDbFs,
        lastFrameClassification = lastFrameClassification.name,
        frameDurationMs = frameDurationMs,
        frameSizeSamples = frameSizeSamples,
        minimumSpeechDurationMs = config.minimumSpeechDurationMs,
        minimumSilenceDurationMs = config.minimumSilenceDurationMs,
        configuredSpeechStartConfirmationFrames = config.speechStartConfirmationFrames,
        configuredSpeechEndConfirmationFrames = config.speechEndConfirmationFrames,
        effectiveSpeechStartConfirmationFrames = effectiveSpeechStartFrames,
        effectiveSpeechEndConfirmationFrames = effectiveSpeechEndFrames,
        consecutiveSpeechFrames = consecutiveSpeechFrames,
        consecutiveSilenceFrames = consecutiveSilenceFrames,
        vadFramesProcessed = vadFramesProcessed,
        speechFrames = speechFrames,
        nonSpeechFrames = nonSpeechFrames,
        speechSegments = speechSegments,
        currentSpeechDurationMs = currentSpeechStateFrames * frameDurationMs,
        currentSilenceDurationMs = currentSilenceStateFrames * frameDurationMs,
        lastSpeechStartedFrameIndex = lastSpeechStartedFrameIndex,
        lastSpeechStoppedFrameIndex = lastSpeechStoppedFrameIndex,
        vadErrorCount = vadErrorCount,
    )

    private fun resetAllLocked() {
        state = State.SILENCE
        lastFrameClassification = FrameClassification.NON_SPEECH
        lastEnergyDbFs = SILENCE_FLOOR_DBFS
        consecutiveSpeechFrames = 0
        consecutiveSilenceFrames = 0
        currentSpeechStateFrames = 0L
        currentSilenceStateFrames = 0L
        vadFramesProcessed = 0L
        speechFrames = 0L
        nonSpeechFrames = 0L
        speechSegments = 0L
        lastSpeechStartedFrameIndex = 0L
        lastSpeechStoppedFrameIndex = 0L
        vadErrorCount = 0L
    }

    private fun durationToFrames(durationMs: Int): Int =
        ceil(durationMs.toDouble() / frameDurationMs.toDouble()).toInt().coerceAtLeast(1)

    private fun isSessionActive(): Boolean = synchronized(lock) { sessionActive }

    private fun formatDb(value: Double): String = String.format("%.1f", value)
}
