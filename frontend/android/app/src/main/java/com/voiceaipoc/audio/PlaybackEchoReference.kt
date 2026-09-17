package com.voiceaipoc.audio

import android.os.SystemClock
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Native, bounded reference of the PCM currently rendered by the TTS player.
 *
 * The microphone remains active during playback so real barge-in is possible,
 * but VAD output can be compared with the audio that the app itself rendered.
 * The comparison is deliberately small and bounded: it examines a short
 * window over a finite speaker-to-microphone delay range and never runs in JS.
 */
class PlaybackEchoReference(
    private val referenceSampleRateHz: Int = REFERENCE_SAMPLE_RATE_HZ,
    private val capacityMs: Int = REFERENCE_CAPACITY_MS,
    private val maxLagMs: Int = MAX_LAG_MS,
    private val clockNs: () -> Long = SystemClock::elapsedRealtimeNanos,
) {
    enum class State {
        IDLE,
        TTS_PLAYING,
        TTS_TAIL_SUPPRESSION,
    }

    data class Assessment(
        val state: State,
        val playbackActive: Boolean,
        val responseId: String?,
        val playbackPositionMs: Long,
        val similarity: Double?,
        val lagMs: Int?,
        val echoLikely: Boolean,
        val referenceAvailable: Boolean,
        val timestampNs: Long,
    ) {
        companion object {
            fun idle(nowNs: Long = SystemClock.elapsedRealtimeNanos()): Assessment = Assessment(
                state = State.IDLE,
                playbackActive = false,
                responseId = null,
                playbackPositionMs = 0L,
                similarity = null,
                lagMs = null,
                echoLikely = false,
                referenceAvailable = false,
                timestampNs = nowNs,
            )
        }
    }

    companion object {
        const val REFERENCE_SAMPLE_RATE_HZ = 16_000
        const val REFERENCE_CAPACITY_MS = 3_000
        const val MAX_LAG_MS = 500
        private const val CORRELATION_WINDOW_MS = 40
        private const val LAG_STEP_MS = 20
        private const val ECHO_SIMILARITY_THRESHOLD = 0.58
        private const val MIN_RMS = 180.0
        private const val TAIL_SUPPRESSION_MS = 180L
        private const val BYTES_PER_SAMPLE = 2
    }

    private val lock = Any()
    private val ring = ShortArray(referenceSampleRateHz * capacityMs / 1_000)
    private val comparisonReference = ShortArray(referenceSampleRateHz * CORRELATION_WINDOW_MS / 1_000)
    private val comparisonMic = ShortArray(comparisonReference.size)

    private var state = State.IDLE
    private var responseId: String? = null
    private var playbackStartedNs = 0L
    private var playbackEndedNs = 0L
    private var ringStart = 0
    private var ringSize = 0
    private var outputSamples = 0L
    private var sourceSamplesSeen = 0L
    private var nextSourceSample = 0L
    private var lastAssessment = Assessment.idle()

    fun onPlaybackStarted(responseId: String, nowNs: Long = clockNs()) = synchronized(lock) {
        state = State.TTS_PLAYING
        this.responseId = responseId
        playbackStartedNs = nowNs
        playbackEndedNs = 0L
        ringStart = 0
        ringSize = 0
        outputSamples = 0L
        sourceSamplesSeen = 0L
        nextSourceSample = 0L
        lastAssessment = assessmentLocked(nowNs)
    }

    /**
     * Adds bytes accepted by AudioTrack. This is intentionally called from
     * the actual AudioTrack writer, rather than when a WebSocket frame arrives.
     */
    fun onPcmRendered(
        responseId: String,
        payload: ByteArray,
        offsetBytes: Int,
        byteCount: Int,
        sampleRateHz: Int,
    ) = synchronized(lock) {
        if (state != State.TTS_PLAYING || this.responseId != responseId) return@synchronized
        if (sampleRateHz <= 0 || byteCount <= 1 || offsetBytes < 0) return@synchronized
        val safeEnd = (offsetBytes + byteCount).coerceAtMost(payload.size)
        val end = safeEnd - (safeEnd - offsetBytes) % BYTES_PER_SAMPLE
        if (end <= offsetBytes) return@synchronized

        var sourceIndex = 0
        while (offsetBytes + sourceIndex + 1 < end) {
            val sample = (
                (payload[offsetBytes + sourceIndex].toInt() and 0xff) or
                    (payload[offsetBytes + sourceIndex + 1].toInt() shl 8)
                ).toShort()
            val globalSourceIndex = sourceSamplesSeen
            if (sampleRateHz == referenceSampleRateHz) {
                appendReferenceSample(sample)
            } else if (globalSourceIndex >= nextSourceSample) {
                appendReferenceSample(sample)
                outputSamples += 1L
                nextSourceSample = (outputSamples * sampleRateHz) / referenceSampleRateHz
            }
            sourceSamplesSeen += 1L
            sourceIndex += BYTES_PER_SAMPLE
        }
        if (sampleRateHz == referenceSampleRateHz) {
            outputSamples += (end - offsetBytes) / BYTES_PER_SAMPLE
        }
    }

    fun onPlaybackEnded(responseId: String, nowNs: Long = clockNs()) = synchronized(lock) {
        if (this.responseId != responseId) return@synchronized
        state = State.TTS_TAIL_SUPPRESSION
        playbackEndedNs = nowNs
        lastAssessment = assessmentLocked(nowNs)
    }

    fun reset() = synchronized(lock) {
        state = State.IDLE
        responseId = null
        playbackStartedNs = 0L
        playbackEndedNs = 0L
        ringStart = 0
        ringSize = 0
        outputSamples = 0L
        sourceSamplesSeen = 0L
        nextSourceSample = 0L
        lastAssessment = Assessment.idle(clockNs())
    }

    fun assess(microphone: ShortArray, samplesRead: Int, nowNs: Long = clockNs()): Assessment =
        synchronized(lock) {
            if (state == State.TTS_TAIL_SUPPRESSION &&
                nowNs - playbackEndedNs >= TAIL_SUPPRESSION_MS * 1_000_000L
            ) {
                state = State.IDLE
                responseId = null
            }
            val assessment = assessmentLocked(nowNs)
            if (assessment.playbackActive && assessment.referenceAvailable && samplesRead > 0) {
                val window = minOf(samplesRead, comparisonMic.size)
                val micOffset = samplesRead - window
                for (index in 0 until window) {
                    comparisonMic[index] = microphone[micOffset + index]
                }
                val match = findBestMatchLocked(window)
                lastAssessment = assessment.copy(
                    similarity = match.first,
                    lagMs = match.second,
                    echoLikely = match.first >= ECHO_SIMILARITY_THRESHOLD,
                )
            } else {
                lastAssessment = assessment
            }
            lastAssessment
        }

    fun latestAssessment(): Assessment = synchronized(lock) { lastAssessment }

    private fun assessmentLocked(nowNs: Long): Assessment {
        val active = state == State.TTS_PLAYING
        val tailActive = state == State.TTS_TAIL_SUPPRESSION &&
            nowNs - playbackEndedNs < TAIL_SUPPRESSION_MS * 1_000_000L
        return Assessment(
            state = if (tailActive) State.TTS_TAIL_SUPPRESSION else if (active) State.TTS_PLAYING else State.IDLE,
            playbackActive = active || tailActive,
            responseId = responseId,
            playbackPositionMs = if (playbackStartedNs == 0L) 0L else outputSamples * 1_000L / referenceSampleRateHz,
            similarity = null,
            lagMs = null,
            echoLikely = false,
            referenceAvailable = ringSize >= comparisonReference.size,
            timestampNs = nowNs,
        )
    }

    private fun appendReferenceSample(sample: Short) {
        val index = (ringStart + ringSize) % ring.size
        if (ringSize == ring.size) {
            ring[ringStart] = sample
            ringStart = (ringStart + 1) % ring.size
        } else {
            ring[index] = sample
            ringSize += 1
        }
    }

    private fun findBestMatchLocked(window: Int): Pair<Double, Int> {
        var bestSimilarity = 0.0
        var bestLagMs = 0
        val micRms = rms(comparisonMic, 0, window)
        if (micRms < MIN_RMS) return bestSimilarity to bestLagMs

        var lagMs = 0
        while (lagMs <= maxLagMs) {
            val lagSamples = lagMs * referenceSampleRateHz / 1_000
            val referenceEnd = ringSize - lagSamples
            val referenceStart = referenceEnd - window
            if (referenceStart >= 0) {
                for (index in 0 until window) {
                    comparisonReference[index] = ring[(ringStart + referenceStart + index) % ring.size]
                }
                val referenceRms = rms(comparisonReference, 0, window)
                if (referenceRms >= MIN_RMS) {
                    val similarity = normalizedCorrelation(comparisonMic, comparisonReference, window)
                    if (similarity > bestSimilarity) {
                        bestSimilarity = similarity
                        bestLagMs = lagMs
                    }
                }
            }
            lagMs += LAG_STEP_MS
        }
        return bestSimilarity to bestLagMs
    }

    private fun normalizedCorrelation(a: ShortArray, b: ShortArray, size: Int): Double {
        var aMean = 0.0
        var bMean = 0.0
        for (index in 0 until size) {
            aMean += a[index].toDouble()
            bMean += b[index].toDouble()
        }
        aMean /= size
        bMean /= size
        var dot = 0.0
        var aEnergy = 0.0
        var bEnergy = 0.0
        for (index in 0 until size) {
            val av = a[index] - aMean
            val bv = b[index] - bMean
            dot += av * bv
            aEnergy += av * av
            bEnergy += bv * bv
        }
        val denominator = sqrt(aEnergy * bEnergy)
        return if (denominator <= 0.0) 0.0 else abs(dot / denominator)
    }

    private fun rms(samples: ShortArray, offset: Int, size: Int): Double {
        var energy = 0.0
        for (index in offset until offset + size) {
            val value = samples[index].toDouble()
            energy += value * value
        }
        return sqrt(energy / size)
    }

}
