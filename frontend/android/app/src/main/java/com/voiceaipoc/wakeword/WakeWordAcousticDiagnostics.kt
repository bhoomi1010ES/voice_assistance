package com.voiceaipoc.wakeword

import java.util.ArrayDeque
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.roundToInt
import kotlin.math.sqrt

data class WakeWordThresholdCount(
    val threshold: Float,
    val count: Long,
)

data class WakeWordTrialThresholdResult(
    val threshold: Float,
    val detectionCount: Long,
    val duplicateSuppressionCount: Long,
    val firstDetectionLatencyMs: Long?,
)

data class WakeWordThresholdAnalysis(
    val threshold: Float,
    val positiveTrials: Int,
    val negativeTrials: Int,
    val trueAccepts: Int,
    val falseRejects: Int,
    val falseAccepts: Int,
    val trueNegatives: Int,
    val duplicateDetections: Long,
    val trueAcceptRate: Double,
    val falseRejectRate: Double,
    val falseAcceptRate: Double,
    val duplicateRate: Double,
    val medianDetectionLatencyMs: Double?,
    val maximumDetectionLatencyMs: Long?,
)

/** Metadata-only summary for one manually marked calibration trial. */
data class WakeWordCalibrationTrial(
    val label: String,
    val condition: String,
    val attemptNumber: Int,
    val expectedPositive: Boolean,
    val audioProcessingMode: String,
    val aecEnabled: Boolean,
    val noiseSuppressionEnabled: Boolean,
    val startedAtTimestampMs: Long,
    val completedAtTimestampMs: Long,
    val firstInferenceIndex: Long,
    val lastInferenceIndex: Long,
    val inferenceWindowCount: Long,
    val minimumScore: Float?,
    val maximumScore: Float?,
    val averageScore: Double,
    val peakPcmAmplitude: Int,
    val peakPcmRms: Double,
    val peakPcmDbFs: Double,
    val maximumQueueDepthFrames: Int,
    val averageInferenceLatencyMs: Double,
    val maximumInferenceLatencyMs: Double,
    val detectionCount: Long,
    val duplicateDetectionCount: Long,
    val firstDetectionTimestampMs: Long?,
    val firstDetectionLatencyMs: Long?,
    val thresholdResults: List<WakeWordTrialThresholdResult>,
)

/** Metadata-only snapshot; no PCM sample array is retained or exposed. */
data class WakeWordAcousticStatus(
    val available: Boolean,
    val enabled: Boolean,
    val pcmByteOrder: String,
    val pcmScaling: String,
    val byteSwapApplied: Boolean,
    val normalizationApplied: Boolean,
    val inferenceWindowCount: Long,
    val scoreMinimum: Float?,
    val scoreMaximum: Float?,
    val scoreAverage: Double,
    val scoreP50: Float?,
    val scoreP90: Float?,
    val scoreP95: Float?,
    val scoreP99: Float?,
    val thresholdCounts: List<WakeWordThresholdCount>,
    val lastInferenceTimestampMs: Long,
    val lastInferenceIndex: Long,
    val lastClassifierScore: Float?,
    val peakClassifierScore: Float?,
    val lastPcmMinimum: Int?,
    val lastPcmMaximum: Int?,
    val lastPcmPeak: Int,
    val lastPcmRms: Double,
    val lastPcmDbFs: Double,
    val maximumObservedPcmRms: Double,
    val maximumObservedPcmDbFs: Double,
    val clippedSampleCount: Long,
    val lastQueueDepthFrames: Int,
    val lastInferenceLatencyMs: Double,
    val lastAecEnabled: Boolean,
    val lastNoiseSuppressionEnabled: Boolean,
    val activeTrialLabel: String?,
    val activeTrialCondition: String?,
    val activeTrialAttemptNumber: Int?,
    val activeTrialExpectedPositive: Boolean?,
    val completedPositiveTrials: Int,
    val completedNegativeTrials: Int,
    val positiveScoreMedian: Float?,
    val positiveScoreMaximum: Float?,
    val negativeScoreMedian: Float?,
    val negativeScoreMaximum: Float?,
    val medianDetectionLatencyMs: Double?,
    val maximumDetectionLatencyMs: Long?,
    val thresholdAnalysis: List<WakeWordThresholdAnalysis>,
    val calibrationTrials: List<WakeWordCalibrationTrial>,
)

/**
 * Preallocated score histogram and PCM statistics for acoustic calibration.
 * The wake worker is the sole writer; status reads are synchronized. PCM is
 * inspected in place and never retained.
 */
internal class WakeWordAcousticDiagnostics(
    private val available: Boolean,
    thresholds: List<Float>,
    private val trialDurationMs: Long,
    private val trialHistoryCapacity: Int,
    private val cooldownMs: Long,
    initiallyEnabled: Boolean = false,
) {
    private data class TrialAccumulator(
        val label: String,
        val condition: String,
        val attemptNumber: Int,
        val expectedPositive: Boolean,
        val audioProcessingMode: String,
        val aecEnabled: Boolean,
        val noiseSuppressionEnabled: Boolean,
        val startedAtTimestampMs: Long,
        val endsAtTimestampMs: Long,
        var firstInferenceIndex: Long = 0L,
        var lastInferenceIndex: Long = 0L,
        var inferenceWindowCount: Long = 0L,
        var minimumScore: Float = Float.POSITIVE_INFINITY,
        var maximumScore: Float = Float.NEGATIVE_INFINITY,
        var scoreSum: Double = 0.0,
        var peakPcmAmplitude: Int = 0,
        var peakPcmRms: Double = 0.0,
        var peakPcmDbFs: Double = DIGITAL_SILENCE_DBFS,
        var maximumQueueDepthFrames: Int = 0,
        var inferenceDurationNanos: Long = 0L,
        var maximumInferenceDurationNanos: Long = 0L,
        var detectionCount: Long = 0L,
        var duplicateDetectionCount: Long = 0L,
        var firstDetectionTimestampMs: Long? = null,
        val candidateDetectionCounts: LongArray,
        val candidateDuplicateCounts: LongArray,
        val candidateCooldownUntilMs: LongArray,
        val candidateFirstDetectionTimestampMs: LongArray,
    )

    private val lock = Any()
    private val scoreThresholds = thresholds.sorted().toFloatArray()
    private val thresholdCounts = LongArray(scoreThresholds.size)
    private val scoreHistogram = LongArray(SCORE_HISTOGRAM_BINS)
    private val completedTrials = ArrayDeque<WakeWordCalibrationTrial>(trialHistoryCapacity)
    private val attemptSequences = mutableMapOf<String, Int>()

    private var enabled = available && initiallyEnabled
    private var inferenceWindowCount = 0L
    private var scoreMinimum = Float.POSITIVE_INFINITY
    private var scoreMaximum = Float.NEGATIVE_INFINITY
    private var scoreSum = 0.0
    private var lastInferenceTimestampMs = 0L
    private var lastInferenceIndex = 0L
    private var lastClassifierScore: Float? = null
    private var lastPcmMinimum: Int? = null
    private var lastPcmMaximum: Int? = null
    private var lastPcmPeak = 0
    private var lastPcmRms = 0.0
    private var lastPcmDbFs = DIGITAL_SILENCE_DBFS
    private var maximumObservedPcmRms = 0.0
    private var maximumObservedPcmDbFs = DIGITAL_SILENCE_DBFS
    private var clippedSampleCount = 0L
    private var lastQueueDepthFrames = 0
    private var lastInferenceLatencyMs = 0.0
    private var lastAecEnabled = false
    private var lastNoiseSuppressionEnabled = false
    private var positiveTrialSequence = 0
    private var negativeTrialSequence = 0
    private var activeTrial: TrialAccumulator? = null

    init {
        require(scoreThresholds.isNotEmpty()) { "diagnostic score thresholds cannot be empty" }
        require(scoreThresholds.all { it.isFinite() && it in 0f..1f }) {
            "diagnostic score thresholds must be finite and in [0, 1]"
        }
        require(scoreThresholds.toSet().size == scoreThresholds.size) {
            "diagnostic score thresholds must be unique"
        }
        require(trialDurationMs > 0L) { "trialDurationMs must be positive" }
        require(trialHistoryCapacity > 0) { "trialHistoryCapacity must be positive" }
        require(cooldownMs > 0L) { "cooldownMs must be positive" }
    }

    fun setEnabled(value: Boolean, timestampMs: Long): WakeWordCalibrationTrial? =
        synchronized(lock) {
            if (!available) {
                enabled = false
                return@synchronized null
            }
            val completed = if (!value) finishActiveTrialLocked(timestampMs) else null
            enabled = value
            completed
        }

    fun beginTrial(
        expectedPositive: Boolean,
        condition: String,
        timestampMs: Long,
        aecEnabled: Boolean,
        noiseSuppressionEnabled: Boolean,
    ): String? = synchronized(lock) {
        if (!enabled || activeTrial != null) {
            return@synchronized null
        }
        val normalizedCondition = normalizeCondition(condition)
        val audioProcessingMode = audioProcessingMode(aecEnabled, noiseSuppressionEnabled)
        val attemptKey = "$normalizedCondition|$expectedPositive|$audioProcessingMode"
        val attemptNumber = (attemptSequences[attemptKey] ?: 0) + 1
        attemptSequences[attemptKey] = attemptNumber
        val globalSequence = if (expectedPositive) {
            positiveTrialSequence += 1
            positiveTrialSequence
        } else {
            negativeTrialSequence += 1
            negativeTrialSequence
        }
        val polarity = if (expectedPositive) "POSITIVE" else "NEGATIVE"
        val label = "${normalizedCondition}_${polarity}_$globalSequence"
        activeTrial = TrialAccumulator(
            label = label,
            condition = normalizedCondition,
            attemptNumber = attemptNumber,
            expectedPositive = expectedPositive,
            audioProcessingMode = audioProcessingMode,
            aecEnabled = aecEnabled,
            noiseSuppressionEnabled = noiseSuppressionEnabled,
            startedAtTimestampMs = timestampMs,
            endsAtTimestampMs = timestampMs + trialDurationMs,
            candidateDetectionCounts = LongArray(scoreThresholds.size),
            candidateDuplicateCounts = LongArray(scoreThresholds.size),
            candidateCooldownUntilMs = LongArray(scoreThresholds.size),
            candidateFirstDetectionTimestampMs = LongArray(scoreThresholds.size),
        )
        label
    }

    fun recordInference(
        pcm16: ShortArray,
        samplesRead: Int,
        inferenceIndex: Long,
        score: Float,
        timestampMs: Long,
        queueDepthFrames: Int,
        inferenceDurationNanos: Long,
        aecEnabled: Boolean,
        noiseSuppressionEnabled: Boolean,
        detectionOccurred: Boolean,
        duplicateSuppressed: Boolean,
    ): WakeWordCalibrationTrial? {
        if (!enabled) {
            return null
        }

        var minimum = Int.MAX_VALUE
        var maximum = Int.MIN_VALUE
        var peak = 0
        var sumSquares = 0.0
        var clipped = 0L
        for (index in 0 until samplesRead) {
            val sample = pcm16[index].toInt()
            minimum = minOf(minimum, sample)
            maximum = maxOf(maximum, sample)
            peak = max(peak, abs(sample))
            sumSquares += sample.toDouble() * sample.toDouble()
            if (sample == Short.MIN_VALUE.toInt() || sample == Short.MAX_VALUE.toInt()) {
                clipped += 1L
            }
        }
        val rms = if (samplesRead == 0) 0.0 else sqrt(sumSquares / samplesRead.toDouble())
        val dbFs = if (rms <= 0.0) {
            DIGITAL_SILENCE_DBFS
        } else {
            max(DIGITAL_SILENCE_DBFS, 20.0 * log10(rms / PCM16_FULL_SCALE))
        }

        return synchronized(lock) {
            var completedTrial: WakeWordCalibrationTrial? = null
            inferenceWindowCount += 1L
            scoreMinimum = minOf(scoreMinimum, score)
            scoreMaximum = maxOf(scoreMaximum, score)
            scoreSum += score.toDouble()
            scoreHistogram[histogramIndex(score)] += 1L
            for (index in scoreThresholds.indices) {
                if (score >= scoreThresholds[index]) {
                    thresholdCounts[index] += 1L
                }
            }
            lastInferenceTimestampMs = timestampMs
            lastInferenceIndex = inferenceIndex
            lastClassifierScore = score
            lastPcmMinimum = minimum
            lastPcmMaximum = maximum
            lastPcmPeak = peak
            lastPcmRms = rms
            lastPcmDbFs = dbFs
            maximumObservedPcmRms = max(maximumObservedPcmRms, rms)
            maximumObservedPcmDbFs = max(maximumObservedPcmDbFs, dbFs)
            clippedSampleCount += clipped
            lastQueueDepthFrames = queueDepthFrames
            lastInferenceLatencyMs = inferenceDurationNanos.toDouble() / NANOS_PER_MILLISECOND
            lastAecEnabled = aecEnabled
            lastNoiseSuppressionEnabled = noiseSuppressionEnabled

            activeTrial?.let { trial ->
                if (trial.inferenceWindowCount == 0L) {
                    trial.firstInferenceIndex = inferenceIndex
                }
                trial.lastInferenceIndex = inferenceIndex
                trial.inferenceWindowCount += 1L
                trial.minimumScore = minOf(trial.minimumScore, score)
                trial.maximumScore = maxOf(trial.maximumScore, score)
                trial.scoreSum += score.toDouble()
                trial.peakPcmAmplitude = max(trial.peakPcmAmplitude, peak)
                trial.peakPcmRms = max(trial.peakPcmRms, rms)
                trial.peakPcmDbFs = max(trial.peakPcmDbFs, dbFs)
                trial.maximumQueueDepthFrames = max(trial.maximumQueueDepthFrames, queueDepthFrames)
                trial.inferenceDurationNanos += inferenceDurationNanos
                trial.maximumInferenceDurationNanos = max(
                    trial.maximumInferenceDurationNanos,
                    inferenceDurationNanos,
                )
                if (detectionOccurred) {
                    trial.detectionCount += 1L
                    if (trial.firstDetectionTimestampMs == null) {
                        trial.firstDetectionTimestampMs = timestampMs
                    }
                }
                if (duplicateSuppressed) trial.duplicateDetectionCount += 1L
                for (index in scoreThresholds.indices) {
                    if (score < scoreThresholds[index]) continue
                    if (timestampMs >= trial.candidateCooldownUntilMs[index]) {
                        trial.candidateDetectionCounts[index] += 1L
                        trial.candidateCooldownUntilMs[index] = timestampMs + cooldownMs
                        if (trial.candidateFirstDetectionTimestampMs[index] == 0L) {
                            trial.candidateFirstDetectionTimestampMs[index] = timestampMs
                        }
                    } else {
                        trial.candidateDuplicateCounts[index] += 1L
                    }
                }
                if (timestampMs >= trial.endsAtTimestampMs) {
                    completedTrial = finishActiveTrialLocked(timestampMs)
                }
            }
            completedTrial
        }
    }

    fun finishActiveTrial(timestampMs: Long): WakeWordCalibrationTrial? = synchronized(lock) {
        finishActiveTrialLocked(timestampMs)
    }

    fun resetSessionMetrics() = synchronized(lock) {
        scoreHistogram.fill(0L)
        thresholdCounts.fill(0L)
        inferenceWindowCount = 0L
        scoreMinimum = Float.POSITIVE_INFINITY
        scoreMaximum = Float.NEGATIVE_INFINITY
        scoreSum = 0.0
        lastInferenceTimestampMs = 0L
        lastInferenceIndex = 0L
        lastClassifierScore = null
        lastPcmMinimum = null
        lastPcmMaximum = null
        lastPcmPeak = 0
        lastPcmRms = 0.0
        lastPcmDbFs = DIGITAL_SILENCE_DBFS
        maximumObservedPcmRms = 0.0
        maximumObservedPcmDbFs = DIGITAL_SILENCE_DBFS
        clippedSampleCount = 0L
        lastQueueDepthFrames = 0
        lastInferenceLatencyMs = 0.0
        lastAecEnabled = false
        lastNoiseSuppressionEnabled = false
        activeTrial = null
    }

    fun reset() = synchronized(lock) {
        resetSessionMetricsLocked()
        completedTrials.clear()
        attemptSequences.clear()
        positiveTrialSequence = 0
        negativeTrialSequence = 0
    }

    fun snapshot(): WakeWordAcousticStatus = synchronized(lock) {
        WakeWordAcousticStatus(
            available = available,
            enabled = enabled,
            pcmByteOrder = "ANDROID_SHORT_ARRAY_SIGNED_PCM16",
            pcmScaling = "RAW_PCM16_TO_FLOAT32_NO_SCALING",
            byteSwapApplied = false,
            normalizationApplied = false,
            inferenceWindowCount = inferenceWindowCount,
            scoreMinimum = scoreMinimum.takeIf { inferenceWindowCount > 0L },
            scoreMaximum = scoreMaximum.takeIf { inferenceWindowCount > 0L },
            scoreAverage = if (inferenceWindowCount == 0L) {
                0.0
            } else {
                scoreSum / inferenceWindowCount.toDouble()
            },
            scoreP50 = percentileLocked(0.50),
            scoreP90 = percentileLocked(0.90),
            scoreP95 = percentileLocked(0.95),
            scoreP99 = percentileLocked(0.99),
            thresholdCounts = scoreThresholds.indices.map { index ->
                WakeWordThresholdCount(scoreThresholds[index], thresholdCounts[index])
            },
            lastInferenceTimestampMs = lastInferenceTimestampMs,
            lastInferenceIndex = lastInferenceIndex,
            lastClassifierScore = lastClassifierScore,
            peakClassifierScore = scoreMaximum.takeIf { inferenceWindowCount > 0L },
            lastPcmMinimum = lastPcmMinimum,
            lastPcmMaximum = lastPcmMaximum,
            lastPcmPeak = lastPcmPeak,
            lastPcmRms = lastPcmRms,
            lastPcmDbFs = lastPcmDbFs,
            maximumObservedPcmRms = maximumObservedPcmRms,
            maximumObservedPcmDbFs = maximumObservedPcmDbFs,
            clippedSampleCount = clippedSampleCount,
            lastQueueDepthFrames = lastQueueDepthFrames,
            lastInferenceLatencyMs = lastInferenceLatencyMs,
            lastAecEnabled = lastAecEnabled,
            lastNoiseSuppressionEnabled = lastNoiseSuppressionEnabled,
            activeTrialLabel = activeTrial?.label,
            activeTrialCondition = activeTrial?.condition,
            activeTrialAttemptNumber = activeTrial?.attemptNumber,
            activeTrialExpectedPositive = activeTrial?.expectedPositive,
            completedPositiveTrials = completedTrials.count { it.expectedPositive },
            completedNegativeTrials = completedTrials.count { !it.expectedPositive },
            positiveScoreMedian = trialScorePercentileLocked(true, 0.50),
            positiveScoreMaximum = trialScoreMaximumLocked(true),
            negativeScoreMedian = trialScorePercentileLocked(false, 0.50),
            negativeScoreMaximum = trialScoreMaximumLocked(false),
            medianDetectionLatencyMs = productionDetectionLatenciesLocked().medianOrNull(),
            maximumDetectionLatencyMs = productionDetectionLatenciesLocked().maxOrNull(),
            thresholdAnalysis = thresholdAnalysisLocked(),
            calibrationTrials = completedTrials.toList(),
        )
    }

    private fun finishActiveTrialLocked(timestampMs: Long): WakeWordCalibrationTrial? {
        val trial = activeTrial ?: return null
        val count = trial.inferenceWindowCount
        if (completedTrials.size == trialHistoryCapacity) {
            completedTrials.removeFirst()
        }
        val completedTrial = WakeWordCalibrationTrial(
                label = trial.label,
                condition = trial.condition,
                attemptNumber = trial.attemptNumber,
                expectedPositive = trial.expectedPositive,
                audioProcessingMode = trial.audioProcessingMode,
                aecEnabled = trial.aecEnabled,
                noiseSuppressionEnabled = trial.noiseSuppressionEnabled,
                startedAtTimestampMs = trial.startedAtTimestampMs,
                completedAtTimestampMs = timestampMs,
                firstInferenceIndex = trial.firstInferenceIndex,
                lastInferenceIndex = trial.lastInferenceIndex,
                inferenceWindowCount = count,
                minimumScore = trial.minimumScore.takeIf { count > 0L },
                maximumScore = trial.maximumScore.takeIf { count > 0L },
                averageScore = if (count == 0L) 0.0 else trial.scoreSum / count.toDouble(),
                peakPcmAmplitude = trial.peakPcmAmplitude,
                peakPcmRms = trial.peakPcmRms,
                peakPcmDbFs = trial.peakPcmDbFs,
                maximumQueueDepthFrames = trial.maximumQueueDepthFrames,
                averageInferenceLatencyMs = if (count == 0L) {
                    0.0
                } else {
                    trial.inferenceDurationNanos.toDouble() /
                        count.toDouble() / NANOS_PER_MILLISECOND
                },
                maximumInferenceLatencyMs =
                    trial.maximumInferenceDurationNanos.toDouble() / NANOS_PER_MILLISECOND,
                detectionCount = trial.detectionCount,
                duplicateDetectionCount = trial.duplicateDetectionCount,
                firstDetectionTimestampMs = trial.firstDetectionTimestampMs,
                firstDetectionLatencyMs = trial.firstDetectionTimestampMs?.let {
                    (it - trial.startedAtTimestampMs).coerceAtLeast(0L)
                },
                thresholdResults = scoreThresholds.indices.map { index ->
                    val firstTimestamp = trial.candidateFirstDetectionTimestampMs[index]
                    WakeWordTrialThresholdResult(
                        threshold = scoreThresholds[index],
                        detectionCount = trial.candidateDetectionCounts[index],
                        duplicateSuppressionCount = trial.candidateDuplicateCounts[index],
                        firstDetectionLatencyMs = firstTimestamp.takeIf { it > 0L }?.let {
                            (it - trial.startedAtTimestampMs).coerceAtLeast(0L)
                        },
                    )
                },
            )
        completedTrials.addLast(completedTrial)
        activeTrial = null
        return completedTrial
    }

    private fun percentileLocked(percentile: Double): Float? {
        if (inferenceWindowCount == 0L) {
            return null
        }
        val target = ceil(percentile * inferenceWindowCount.toDouble()).toLong().coerceAtLeast(1L)
        var cumulative = 0L
        for (index in scoreHistogram.indices) {
            cumulative += scoreHistogram[index]
            if (cumulative >= target) {
                return index.toFloat() / (SCORE_HISTOGRAM_BINS - 1).toFloat()
            }
        }
        return 1.0f
    }

    private fun histogramIndex(score: Float): Int =
        (score * (SCORE_HISTOGRAM_BINS - 1)).roundToInt()
            .coerceIn(0, SCORE_HISTOGRAM_BINS - 1)

    private fun resetSessionMetricsLocked() {
        scoreHistogram.fill(0L)
        thresholdCounts.fill(0L)
        inferenceWindowCount = 0L
        scoreMinimum = Float.POSITIVE_INFINITY
        scoreMaximum = Float.NEGATIVE_INFINITY
        scoreSum = 0.0
        lastInferenceTimestampMs = 0L
        lastInferenceIndex = 0L
        lastClassifierScore = null
        lastPcmMinimum = null
        lastPcmMaximum = null
        lastPcmPeak = 0
        lastPcmRms = 0.0
        lastPcmDbFs = DIGITAL_SILENCE_DBFS
        maximumObservedPcmRms = 0.0
        maximumObservedPcmDbFs = DIGITAL_SILENCE_DBFS
        clippedSampleCount = 0L
        lastQueueDepthFrames = 0
        lastInferenceLatencyMs = 0.0
        lastAecEnabled = false
        lastNoiseSuppressionEnabled = false
        activeTrial = null
    }

    private fun thresholdAnalysisLocked(): List<WakeWordThresholdAnalysis> {
        val trials = completedTrials.toList()
        val positiveTrials = trials.count { it.expectedPositive }
        val negativeTrials = trials.size - positiveTrials
        return scoreThresholds.indices.map { index ->
            val trueAccepts = trials.count {
                it.expectedPositive && it.thresholdResults[index].detectionCount > 0L
            }
            val falseAccepts = trials.count {
                !it.expectedPositive && it.thresholdResults[index].detectionCount > 0L
            }
            val duplicateDetections = trials.sumOf {
                it.thresholdResults[index].duplicateSuppressionCount
            }
            val latencies = trials.mapNotNull {
                if (it.expectedPositive) it.thresholdResults[index].firstDetectionLatencyMs else null
            }
            WakeWordThresholdAnalysis(
                threshold = scoreThresholds[index],
                positiveTrials = positiveTrials,
                negativeTrials = negativeTrials,
                trueAccepts = trueAccepts,
                falseRejects = positiveTrials - trueAccepts,
                falseAccepts = falseAccepts,
                trueNegatives = negativeTrials - falseAccepts,
                duplicateDetections = duplicateDetections,
                trueAcceptRate = rate(trueAccepts, positiveTrials),
                falseRejectRate = rate(positiveTrials - trueAccepts, positiveTrials),
                falseAcceptRate = rate(falseAccepts, negativeTrials),
                duplicateRate = rate(duplicateDetections, trials.size),
                medianDetectionLatencyMs = latencies.medianOrNull(),
                maximumDetectionLatencyMs = latencies.maxOrNull(),
            )
        }
    }

    private fun productionDetectionLatenciesLocked(): List<Long> =
        completedTrials.mapNotNull { it.firstDetectionLatencyMs }

    private fun trialScoreMaximumLocked(expectedPositive: Boolean): Float? =
        completedTrials.mapNotNull {
            if (it.expectedPositive == expectedPositive) it.maximumScore else null
        }.maxOrNull()

    private fun trialScorePercentileLocked(expectedPositive: Boolean, percentile: Double): Float? {
        val values = completedTrials.mapNotNull {
            if (it.expectedPositive == expectedPositive) it.maximumScore else null
        }.sorted()
        if (values.isEmpty()) return null
        val index = ceil(percentile * values.size.toDouble()).toInt().coerceIn(1, values.size) - 1
        return values[index]
    }

    private fun normalizeCondition(condition: String): String {
        val normalized = condition.trim().uppercase()
            .replace(Regex("[^A-Z0-9]+"), "_")
            .trim('_')
        require(normalized.isNotEmpty() && normalized.length <= MAX_CONDITION_LENGTH) {
            "calibration condition must contain 1-$MAX_CONDITION_LENGTH letters or digits"
        }
        return normalized
    }

    private fun audioProcessingMode(aecEnabled: Boolean, nsEnabled: Boolean): String = when {
        aecEnabled && nsEnabled -> "AEC_NS"
        aecEnabled -> "AEC_ONLY"
        nsEnabled -> "NS_ONLY"
        else -> "DISABLED"
    }

    private fun rate(numerator: Int, denominator: Int): Double =
        if (denominator == 0) 0.0 else numerator.toDouble() / denominator.toDouble()

    private fun rate(numerator: Long, denominator: Int): Double =
        if (denominator == 0) 0.0 else numerator.toDouble() / denominator.toDouble()

    private fun List<Long>.medianOrNull(): Double? {
        if (isEmpty()) return null
        val sorted = sorted()
        val middle = sorted.size / 2
        return if (sorted.size % 2 == 0) {
            (sorted[middle - 1].toDouble() + sorted[middle].toDouble()) / 2.0
        } else {
            sorted[middle].toDouble()
        }
    }

    companion object {
        private const val SCORE_HISTOGRAM_BINS = 1_001
        private const val PCM16_FULL_SCALE = 32_768.0
        private const val DIGITAL_SILENCE_DBFS = -120.0
        private const val NANOS_PER_MILLISECOND = 1_000_000.0
        private const val MAX_CONDITION_LENGTH = 48
    }
}
