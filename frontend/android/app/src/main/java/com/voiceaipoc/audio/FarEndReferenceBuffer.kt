package com.voiceaipoc.audio

import android.os.SystemClock
import com.voiceaipoc.voice.TtsPlaybackPosition
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Bounded, presentation-timed far-end PCM reference.
 *
 * AudioTrack writes are only accepted input. Each accepted source frame is
 * mapped to the time at which the playback head is expected to present it,
 * using AudioTimestamp first and a wrap-safe playback-head estimate second.
 * Microphone comparisons are then queried by capture interval and bounded
 * delay search, rather than by the age of the latest write.
 */
class FarEndReferenceBuffer(
    private val referenceSampleRateHz: Int = REFERENCE_SAMPLE_RATE_HZ,
    private val capacityMs: Int = REFERENCE_CAPACITY_MS,
    private val maxDelayMs: Int = MAX_DELAY_MS,
    private val analysisContextMs: Int = DEFAULT_ANALYSIS_CONTEXT_MS,
    private val tailSuppressionMs: Long = DEFAULT_TAIL_SUPPRESSION_MS,
    /** Rollout switch for presentation-timed reference; false is diagnostic fallback only. */
    private val presentationTimingEnabled: Boolean = true,
    private val clockNs: () -> Long = SystemClock::elapsedRealtimeNanos,
) {
    /** Reusable output metadata for the software AEC render lookup. */
    class ReferenceWindow {
        var referenceReady: Boolean = false
        var samplesAvailable: Int = 0
        var timestampConfidence: String = TIMESTAMP_NONE
        var firstSampleTimestampNs: Long = 0L
        var farEndRms: Double = 0.0

        fun reset() {
            referenceReady = false
            samplesAvailable = 0
            timestampConfidence = TIMESTAMP_NONE
            firstSampleTimestampNs = 0L
            farEndRms = 0.0
        }
    }

    enum class State {
        BUFFERING,
        PLAYING,
        DRAINING,
        TAIL_SUPPRESSION,
        STOPPED,
        COMPLETED,
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
        val timestampConfidence: String,
        val estimatedDelayMs: Int?,
        val correlation: Double?,
        val coherence: Double?,
        val farEndRms: Double?,
        val micRms: Double?,
        val nearEndResidualRatio: Double?,
        val nearEndFarEndEnergyRatio: Double? = null,
        val timestampNs: Long,
    ) {
        companion object {
            fun stopped(nowNs: Long = SystemClock.elapsedRealtimeNanos()): Assessment = Assessment(
                state = State.STOPPED,
                playbackActive = false,
                responseId = null,
                playbackPositionMs = 0L,
                similarity = null,
                lagMs = null,
                echoLikely = false,
                referenceAvailable = false,
                timestampConfidence = TIMESTAMP_NONE,
                estimatedDelayMs = null,
                correlation = null,
                coherence = null,
                farEndRms = null,
                micRms = null,
                nearEndResidualRatio = null,
                nearEndFarEndEnergyRatio = null,
                timestampNs = nowNs,
            )
        }
    }

    /** Metadata-only reference state exposed to diagnostics and evidence export. */
    data class Status(
        val state: State,
        val responseId: String?,
        val writtenPlaybackFrames: Long,
        val presentedPlaybackFrames: Long,
        val referenceBufferedFrames: Int,
        val referenceReady: Boolean,
        val timestampConfidence: String,
        val estimatedDelayMs: Int?,
        val echoSimilarity: Double?,
        val echoCoherence: Double?,
        val farEndRms: Double?,
        val micRms: Double?,
        val nearEndFarEndEnergyRatio: Double?,
        val lastAssessmentTimestampNs: Long,
    )

    companion object {
        const val REFERENCE_SAMPLE_RATE_HZ = 16_000
        const val REFERENCE_CAPACITY_MS = 3_000
        const val MAX_DELAY_MS = 500
        const val DEFAULT_ANALYSIS_CONTEXT_MS = 120
        const val DEFAULT_TAIL_SUPPRESSION_MS = 300L
        const val DELAY_STEP_MS = 20
        const val ECHO_SIMILARITY_THRESHOLD = 0.58
        private const val MIN_RMS = 180.0
        private const val BYTES_PER_SAMPLE = 2
        private const val MAX_INPUT_CHUNK_SAMPLES = 4_096
        private const val TIMESTAMP_DISCONTINUITY_MS = 100L
        private const val TIMESTAMP_NONE = "NONE"
        private const val TIMESTAMP_AUDIO_TIMESTAMP = "AUDIO_TIMESTAMP"
        private const val TIMESTAMP_PLAYBACK_HEAD = "PLAYBACK_HEAD_FALLBACK"
        private const val TIMESTAMP_WRITE_TIME = "WRITE_TIME_FALLBACK"
    }

    private val lock = Any()
    private val capacitySamples = (referenceSampleRateHz * capacityMs / 1_000).coerceAtLeast(1)
    private val analysisSamples = (referenceSampleRateHz * analysisContextMs / 1_000).coerceAtLeast(1)
    private val ring = ShortArray(capacitySamples)
    private val ringTimesNs = LongArray(capacitySamples)
    private val comparisonReference = ShortArray(analysisSamples)
    private val comparisonMic = ShortArray(analysisSamples)
    private val resampledScratch = ShortArray(MAX_INPUT_CHUNK_SAMPLES + 64)

    private var state = State.STOPPED
    private var responseId: String? = null
    private var sourceSampleRateHz = REFERENCE_SAMPLE_RATE_HZ
    private var resampler = PcmResampler(sourceSampleRateHz, referenceSampleRateHz)
    private var ringStart = 0
    private var ringSize = 0
    private var nextReferenceTimeNs = 0L
    private var timelineInitialized = false
    private var writtenSourceFrames = 0L
    private var presentedSourceFrames = 0L
    private var lastTimestampConfidence = TIMESTAMP_NONE
    private var playbackStartedNs = 0L
    private var finalPresentationNs = 0L
    private var lastAssessment = Assessment.stopped()

    fun onPlaybackStarted(
        responseId: String,
        sampleRateHz: Int,
        nowNs: Long = clockNs(),
    ) = synchronized(lock) {
        this.responseId = responseId
        sourceSampleRateHz = sampleRateHz.coerceAtLeast(1)
        resampler = PcmResampler(sourceSampleRateHz, referenceSampleRateHz)
        state = State.BUFFERING
        ringStart = 0
        ringSize = 0
        nextReferenceTimeNs = 0L
        timelineInitialized = false
        writtenSourceFrames = 0L
        presentedSourceFrames = 0L
        lastTimestampConfidence = TIMESTAMP_NONE
        playbackStartedNs = nowNs
        finalPresentationNs = 0L
        lastAssessment = assessmentLocked(nowNs)
    }

    /** Records PCM accepted by AudioTrack and anchors it to presentation time. */
    fun onPcmWritten(
        responseId: String,
        payload: ByteArray,
        offsetBytes: Int,
        byteCount: Int,
        sampleRateHz: Int,
        writtenFrameStart: Long,
        playbackPosition: TtsPlaybackPosition,
    ) = synchronized(lock) {
        if (this.responseId != responseId || state == State.STOPPED || state == State.COMPLETED) {
            return@synchronized
        }
        if (sampleRateHz <= 0 || byteCount <= 1 || offsetBytes < 0) return@synchronized
        val safeEnd = (offsetBytes + byteCount).coerceAtMost(payload.size)
        val evenEnd = safeEnd - (safeEnd - offsetBytes) % BYTES_PER_SAMPLE
        if (evenEnd <= offsetBytes) return@synchronized

        val writtenFrames = (evenEnd - offsetBytes) / BYTES_PER_SAMPLE
        writtenSourceFrames = maxOf(writtenSourceFrames, writtenFrameStart + writtenFrames.toLong())
        presentedSourceFrames = maxOf(
            presentedSourceFrames,
            playbackPosition.timestamp?.framePosition ?: playbackPosition.playbackHeadFramePosition ?: 0L,
        )

        val presentationStartNs = presentationTimeForFrame(
            writtenFrameStart,
            sampleRateHz,
            playbackPosition,
        )
        var remainingBytes = evenEnd - offsetBytes
        var currentOffsetBytes = offsetBytes
        var sourceFramesConsumed = 0
        while (remainingBytes > 0) {
            val chunkBytes = minOf(remainingBytes, MAX_INPUT_CHUNK_SAMPLES * BYTES_PER_SAMPLE)
            val produced = resampler.processPcm16(
                input = payload,
                inputOffsetBytes = currentOffsetBytes,
                byteCount = chunkBytes,
                output = resampledScratch,
            )
            if (produced > 0) {
                val chunkStartNs = presentationStartNs +
                    sourceFramesConsumed.toLong() * 1_000_000_000L / sampleRateHz.toLong()
                if (!timelineInitialized) {
                    nextReferenceTimeNs = chunkStartNs
                    timelineInitialized = true
                } else if (abs(chunkStartNs - nextReferenceTimeNs) > TIMESTAMP_DISCONTINUITY_MS * 1_000_000L) {
                    nextReferenceTimeNs = chunkStartNs
                }
                appendSamples(resampledScratch, produced)
            }
            currentOffsetBytes += chunkBytes
            remainingBytes -= chunkBytes
            sourceFramesConsumed += chunkBytes / BYTES_PER_SAMPLE
        }
        if (state == State.BUFFERING && ringSize > 0) state = State.PLAYING
        lastAssessment = assessmentLocked(playbackPosition.measuredAtNs)
    }

    fun onPlaybackDraining(responseId: String, nowNs: Long = clockNs()) = synchronized(lock) {
        if (this.responseId != responseId || state == State.STOPPED || state == State.COMPLETED) return@synchronized
        state = State.DRAINING
        lastAssessment = assessmentLocked(nowNs)
    }

    fun onPlaybackCompleted(
        responseId: String,
        finalPosition: TtsPlaybackPosition? = null,
        nowNs: Long = clockNs(),
    ) = synchronized(lock) {
        if (this.responseId != responseId) return@synchronized
        presentedSourceFrames = maxOf(
            presentedSourceFrames,
            finalPosition?.timestamp?.framePosition
                ?: finalPosition?.playbackHeadFramePosition
                ?: 0L,
        )
        finalPresentationNs = finalPosition?.timestamp?.nanoTime
            ?: finalPosition?.measuredAtNs
            ?: nowNs
        state = State.TAIL_SUPPRESSION
        lastAssessment = assessmentLocked(nowNs)
    }

    fun onPlaybackStopped(responseId: String, nowNs: Long = clockNs()) = synchronized(lock) {
        if (this.responseId != responseId) return@synchronized
        state = State.STOPPED
        finalPresentationNs = nowNs
        lastAssessment = assessmentLocked(nowNs)
    }

    fun reset() = synchronized(lock) {
        state = State.STOPPED
        responseId = null
        ringStart = 0
        ringSize = 0
        nextReferenceTimeNs = 0L
        timelineInitialized = false
        writtenSourceFrames = 0L
        presentedSourceFrames = 0L
        lastTimestampConfidence = TIMESTAMP_NONE
        playbackStartedNs = 0L
        finalPresentationNs = 0L
        resampler.reset()
        lastAssessment = Assessment.stopped(clockNs())
    }

    /** Queries the reference over the exact microphone capture interval. */
    fun assess(
        microphone: ShortArray,
        samplesRead: Int,
        captureStartNs: Long,
        captureEndNs: Long,
        microphoneSampleRateHz: Int = referenceSampleRateHz,
    ): Assessment = synchronized(lock) {
        val nowNs = captureEndNs
        if (state == State.TAIL_SUPPRESSION &&
            nowNs - finalPresentationNs >= tailSuppressionMs * 1_000_000L
        ) {
            state = State.COMPLETED
        }
        val base = assessmentLocked(nowNs)
        if (!base.playbackActive || samplesRead <= 0 || ringSize == 0) {
            lastAssessment = base
            return@synchronized base
        }

        val window = minOf(samplesRead, comparisonMic.size, microphone.size)
        if (window <= 0 || microphoneSampleRateHz <= 0) {
            lastAssessment = base
            return@synchronized base
        }
        val intervalStartNs = if (window == samplesRead) {
            captureStartNs
        } else {
            captureEndNs - window.toLong() * 1_000_000_000L / microphoneSampleRateHz.toLong()
        }
        val micOffset = samplesRead - window
        microphone.copyInto(comparisonMic, 0, micOffset, micOffset + window)
        val micRms = rms(comparisonMic, 0, window)
        val match = findBestMatchLocked(
            window = window,
            intervalStartNs = intervalStartNs,
            microphoneSampleRateHz = microphoneSampleRateHz,
            micRms = micRms,
        )
        val assessment = base.copy(
            referenceAvailable = match.referenceAvailable,
            similarity = match.similarity,
            lagMs = match.delayMs,
            echoLikely = (match.similarity ?: 0.0) >= ECHO_SIMILARITY_THRESHOLD,
            estimatedDelayMs = match.delayMs,
            correlation = match.similarity,
            coherence = match.coherence,
            farEndRms = match.farEndRms,
            micRms = micRms,
            nearEndResidualRatio = match.residualRatio,
            nearEndFarEndEnergyRatio = match.farEndRms
                ?.takeIf { it > 0.0 }
                ?.let { micRms / it },
        )
        lastAssessment = assessment
        assessment
    }

    /** Compatibility helper for callers that only have a current end time. */
    fun assess(microphone: ShortArray, samplesRead: Int, nowNs: Long = clockNs()): Assessment =
        assess(
            microphone = microphone,
            samplesRead = samplesRead,
            captureStartNs = nowNs - samplesRead.toLong() * 1_000_000_000L / referenceSampleRateHz,
            captureEndNs = nowNs,
        )

    fun latestAssessment(): Assessment = synchronized(lock) { lastAssessment }

    fun getStatus(): Status = synchronized(lock) {
        val assessment = lastAssessment
        Status(
            state = state,
            responseId = responseId,
            writtenPlaybackFrames = writtenSourceFrames,
            presentedPlaybackFrames = presentedSourceFrames,
            referenceBufferedFrames = ringSize,
            referenceReady = assessment.referenceAvailable,
            timestampConfidence = assessment.timestampConfidence,
            estimatedDelayMs = assessment.estimatedDelayMs,
            echoSimilarity = assessment.similarity,
            echoCoherence = assessment.coherence,
            farEndRms = assessment.farEndRms,
            micRms = assessment.micRms,
            nearEndFarEndEnergyRatio = assessment.nearEndFarEndEnergyRatio,
            lastAssessmentTimestampNs = assessment.timestampNs,
        )
    }

    /**
     * Copies the presentation-aligned far-end interval for a capture frame.
     * The caller supplies a reusable [result] and [output] so this method does
     * not allocate for each 10 ms AEC frame.
     */
    fun copyPresentationReference(
        output: ShortArray,
        outputOffset: Int,
        sampleCount: Int,
        captureStartNs: Long,
        sampleRateHz: Int,
        delayMs: Int,
        result: ReferenceWindow,
    ) = synchronized(lock) {
        result.reset()
        if (sampleCount <= 0 || sampleRateHz <= 0 ||
            outputOffset < 0 || outputOffset + sampleCount > output.size
        ) {
            return@synchronized
        }

        output.fill(0, outputOffset, outputOffset + sampleCount)
        result.firstSampleTimestampNs = captureStartNs - delayMs.toLong() * 1_000_000L
        result.timestampConfidence = lastTimestampConfidence
        if (ringSize == 0 || lastTimestampConfidence == TIMESTAMP_NONE) {
            return@synchronized
        }

        var available = 0
        var energy = 0.0
        for (index in 0 until sampleCount) {
            val targetNs = captureStartNs +
                index.toLong() * 1_000_000_000L / sampleRateHz.toLong() -
                delayMs.toLong() * 1_000_000L
            val referenceIndex = nearestReferenceOffset(targetNs)
            if (referenceIndex < 0) continue
            val sample = ring[(ringStart + referenceIndex) % capacitySamples]
            output[outputOffset + index] = sample
            available += 1
            val value = sample.toDouble()
            energy += value * value
        }
        result.samplesAvailable = available
        result.referenceReady = available == sampleCount
        result.farEndRms = if (available == 0) 0.0 else sqrt(energy / available.toDouble())
    }

    private fun presentationTimeForFrame(
        sourceFrame: Long,
        sampleRateHz: Int,
        position: TtsPlaybackPosition,
    ): Long {
        if (!presentationTimingEnabled) {
            lastTimestampConfidence = TIMESTAMP_WRITE_TIME
            return position.measuredAtNs
        }
        val timestamp = position.timestamp
        if (timestamp != null && timestamp.framePosition >= 0L) {
            lastTimestampConfidence = TIMESTAMP_AUDIO_TIMESTAMP
            return timestamp.nanoTime +
                (sourceFrame - timestamp.framePosition) * 1_000_000_000L / sampleRateHz.toLong()
        }
        val playbackHead = position.playbackHeadFramePosition
        if (playbackHead != null && playbackHead >= 0L) {
            lastTimestampConfidence = TIMESTAMP_PLAYBACK_HEAD
            return position.measuredAtNs +
                (sourceFrame - playbackHead) * 1_000_000_000L / sampleRateHz.toLong()
        }
        lastTimestampConfidence = TIMESTAMP_WRITE_TIME
        return position.measuredAtNs
    }

    private fun appendSamples(samples: ShortArray, count: Int) {
        for (index in 0 until count) {
            val ringIndex = (ringStart + ringSize) % capacitySamples
            ring[ringIndex] = samples[index]
            ringTimesNs[ringIndex] = nextReferenceTimeNs
            nextReferenceTimeNs += 1_000_000_000L / referenceSampleRateHz.toLong()
            if (ringSize == capacitySamples) {
                ringStart = (ringStart + 1) % capacitySamples
            } else {
                ringSize += 1
            }
        }
    }

    private fun assessmentLocked(nowNs: Long): Assessment {
        if (state == State.TAIL_SUPPRESSION &&
            nowNs - finalPresentationNs >= tailSuppressionMs * 1_000_000L
        ) {
            state = State.COMPLETED
        }
        val active = state == State.BUFFERING || state == State.PLAYING || state == State.DRAINING
        val tailActive = state == State.TAIL_SUPPRESSION
        return Assessment(
            state = state,
            playbackActive = active || tailActive,
            responseId = responseId,
            playbackPositionMs = if (sourceSampleRateHz <= 0) 0L else {
                presentedSourceFrames * 1_000L / sourceSampleRateHz.toLong()
            },
            similarity = null,
            lagMs = null,
            echoLikely = false,
            referenceAvailable = ringSize >= analysisSamples,
            timestampConfidence = lastTimestampConfidence,
            estimatedDelayMs = null,
            correlation = null,
            coherence = null,
            farEndRms = null,
            micRms = null,
            nearEndResidualRatio = null,
            nearEndFarEndEnergyRatio = null,
            timestampNs = nowNs,
        )
    }

    private fun findBestMatchLocked(
        window: Int,
        intervalStartNs: Long,
        microphoneSampleRateHz: Int,
        micRms: Double,
    ): Match {
        if (micRms < MIN_RMS) return Match.none()
        var bestSimilarity = 0.0
        var bestDelayMs: Int? = null
        var bestFarEndRms: Double? = null
        var bestResidualRatio: Double? = null
        var bestCoherence: Double? = null
        var anyReference = false
        var delayMs = 0
        while (delayMs <= maxDelayMs) {
            val delayNs = delayMs.toLong() * 1_000_000L
            var valid = true
            for (index in 0 until window) {
                val targetNs = intervalStartNs +
                    index.toLong() * 1_000_000_000L / microphoneSampleRateHz.toLong() - delayNs
                val referenceIndex = nearestReferenceOffset(targetNs)
                if (referenceIndex < 0) {
                    valid = false
                    break
                }
                comparisonReference[index] = ring[(ringStart + referenceIndex) % capacitySamples]
            }
            if (valid) {
                anyReference = true
                val referenceRms = rms(comparisonReference, 0, window)
                if (referenceRms >= MIN_RMS) {
                    val correlation = normalizedCorrelation(comparisonMic, comparisonReference, window)
                    if (correlation > bestSimilarity) {
                        bestSimilarity = correlation
                        bestDelayMs = delayMs
                        bestFarEndRms = referenceRms
                        bestResidualRatio = residualRatio(comparisonMic, comparisonReference, window)
                        bestCoherence = correlation
                    }
                }
            }
            delayMs += DELAY_STEP_MS
        }
        return Match(
            referenceAvailable = anyReference,
            similarity = bestDelayMs?.let { bestSimilarity },
            delayMs = bestDelayMs,
            coherence = bestCoherence,
            farEndRms = bestFarEndRms,
            residualRatio = bestResidualRatio,
        )
    }

    private fun nearestReferenceOffset(targetNs: Long): Int {
        if (ringSize == 0) return -1
        val firstTime = ringTimesNs[ringStart]
        val lastIndex = (ringStart + ringSize - 1) % capacitySamples
        val lastTime = ringTimesNs[lastIndex]
        if (targetNs < firstTime || targetNs > lastTime) return -1

        var low = 0
        var high = ringSize - 1
        while (low <= high) {
            val middle = (low + high) ushr 1
            val middleTime = ringTimesNs[(ringStart + middle) % capacitySamples]
            when {
                middleTime < targetNs -> low = middle + 1
                middleTime > targetNs -> high = middle - 1
                else -> return middle
            }
        }
        if (low <= 0) return 0
        if (low >= ringSize) return ringSize - 1
        val before = low - 1
        val beforeTime = ringTimesNs[(ringStart + before) % capacitySamples]
        val afterTime = ringTimesNs[(ringStart + low) % capacitySamples]
        return if (targetNs - beforeTime <= afterTime - targetNs) before else low
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

    private fun residualRatio(a: ShortArray, b: ShortArray, size: Int): Double {
        var aMean = 0.0
        var bMean = 0.0
        for (index in 0 until size) {
            aMean += a[index].toDouble()
            bMean += b[index].toDouble()
        }
        aMean /= size
        bMean /= size
        var dot = 0.0
        var bEnergy = 0.0
        var aEnergy = 0.0
        for (index in 0 until size) {
            val av = a[index] - aMean
            val bv = b[index] - bMean
            dot += av * bv
            bEnergy += bv * bv
            aEnergy += av * av
        }
        if (aEnergy <= 0.0 || bEnergy <= 0.0) return 1.0
        val gain = dot / bEnergy
        var residualEnergy = 0.0
        for (index in 0 until size) {
            val av = a[index] - aMean
            val bv = b[index] - bMean
            val residual = av - gain * bv
            residualEnergy += residual * residual
        }
        return (sqrt(residualEnergy / aEnergy)).coerceIn(0.0, 1.0)
    }

    private fun rms(samples: ShortArray, offset: Int, size: Int): Double {
        var energy = 0.0
        for (index in offset until offset + size) {
            val value = samples[index].toDouble()
            energy += value * value
        }
        return sqrt(energy / size.toDouble())
    }

    private data class Match(
        val referenceAvailable: Boolean,
        val similarity: Double?,
        val delayMs: Int?,
        val coherence: Double?,
        val farEndRms: Double?,
        val residualRatio: Double?,
    ) {
        companion object {
            fun none() = Match(false, null, null, null, null, null)
        }
    }
}
