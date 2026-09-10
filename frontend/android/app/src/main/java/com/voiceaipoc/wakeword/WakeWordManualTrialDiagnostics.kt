package com.voiceaipoc.wakeword

import java.util.ArrayDeque

/** Metadata for one classifier window that crossed the production threshold. */
data class WakeWordThresholdCrossing(
    val inferenceWindowSequence: Long,
    val inferenceTimestampMs: Long,
    val score: Float,
    val wakeStateBefore: String,
    val wakeStateAfter: String,
    val cooldownRemainingMs: Long,
    val generatedWakeEvent: Boolean,
    val suppressedByCooldown: Boolean,
)

/** Metadata for one native wake event; no PCM, tensors, or recordings are retained. */
data class WakeWordDetectionMetadata(
    val detectionSequenceNumber: Long,
    val classifierScore: Float,
    val inferenceWindowSequence: Long,
    val inferenceTimestampMs: Long,
    val wakeStateBefore: String,
    val wakeStateAfter: String,
    val cooldownRemainingMs: Long,
    val millisecondsSincePreviousDetection: Long?,
    val workerGeneration: Long,
)

/** Bounded, metadata-only record for the manually controlled microphone session. */
data class WakeWordManualTrialStatus(
    val active: Boolean,
    val trialId: String?,
    val microphoneSessionId: Int,
    val startTimestampMs: Long,
    val stopTimestampMs: Long,
    val wakeDetectionCount: Long,
    val inferenceWindowCount: Long,
    val aboveThresholdWindowCount: Long,
    val maximumScore: Float?,
    val maximumScoreTimestampMs: Long,
    val lastDetectionTimestampMs: Long,
    val lastDetectionIntervalMs: Long?,
    val currentWakeState: String,
    val cooldownActive: Boolean,
    val cooldownRemainingMs: Long,
    val cooldownDurationMs: Long,
    val queueDepthFrames: Int,
    val queueHighWaterMarkFrames: Int,
    val queueDrops: Long,
    val runtimeErrors: Long,
    val workerGeneration: Long,
    val detections: List<WakeWordDetectionMetadata>,
    val thresholdCrossings: List<WakeWordThresholdCrossing>,
)

/**
 * Tracks one manually delimited AudioRecord session. The microphone remains
 * controlled exclusively by AudioEngine.startRecording/stopRecording; this
 * class only observes inference metadata and keeps bounded lists for review.
 */
internal class WakeWordManualTrialDiagnostics(
    private val detectionThreshold: Float,
    private val cooldownDurationMs: Long,
    private val detectionHistoryCapacity: Int = 64,
    private val thresholdCrossingHistoryCapacity: Int = 128,
) {
    private data class MutableTrial(
        val trialId: String,
        val microphoneSessionId: Int,
        val startTimestampMs: Long,
        val workerGeneration: Long,
        var stopTimestampMs: Long = 0L,
        var wakeDetectionCount: Long = 0L,
        var inferenceWindowCount: Long = 0L,
        var aboveThresholdWindowCount: Long = 0L,
        var maximumScore: Float? = null,
        var maximumScoreTimestampMs: Long = 0L,
        var lastDetectionTimestampMs: Long = 0L,
        var lastDetectionIntervalMs: Long? = null,
        var currentWakeState: String = WakeWordStateMachine.State.IDLE.name,
        var cooldownActive: Boolean = false,
        var cooldownRemainingMs: Long = 0L,
        var queueDepthFrames: Int = 0,
        var queueHighWaterMarkFrames: Int = 0,
        var queueDrops: Long = 0L,
        var runtimeErrors: Long = 0L,
        val detections: ArrayDeque<WakeWordDetectionMetadata> = ArrayDeque(),
        val thresholdCrossings: ArrayDeque<WakeWordThresholdCrossing> = ArrayDeque(),
    )

    private val lock = Any()
    private var nextTrialSequence = 0L
    private var activeTrial: MutableTrial? = null
    private var completedTrial: WakeWordManualTrialStatus? = null
    private val completedHistory = ArrayDeque<WakeWordManualTrialStatus>()

    fun begin(microphoneSessionId: Int, timestampMs: Long, workerGeneration: Long) =
        synchronized(lock) {
            nextTrialSequence += 1L
            activeTrial = MutableTrial(
                trialId = "MANUAL_WAKE_${nextTrialSequence.toString().padStart(6, '0')}",
                microphoneSessionId = microphoneSessionId,
                startTimestampMs = timestampMs,
                workerGeneration = workerGeneration,
            )
        }

    fun recordInference(
        inferenceWindowSequence: Long,
        inferenceTimestampMs: Long,
        score: Float,
        wakeStateBefore: String,
        wakeStateAfter: String,
        cooldownRemainingMs: Long,
        queueDepthFrames: Int,
        queueHighWaterMarkFrames: Int,
        queueDrops: Long,
        runtimeErrors: Long,
        detection: WakeWordStateMachine.Detection?,
        suppressedByCooldown: Boolean,
    ) = synchronized(lock) {
        val trial = activeTrial ?: return@synchronized
        trial.inferenceWindowCount += 1L
        trial.currentWakeState = wakeStateAfter
        trial.cooldownActive = cooldownRemainingMs > 0L
        trial.cooldownRemainingMs = cooldownRemainingMs
        trial.queueDepthFrames = queueDepthFrames
        trial.queueHighWaterMarkFrames = maxOf(trial.queueHighWaterMarkFrames, queueHighWaterMarkFrames)
        trial.queueDrops = queueDrops
        trial.runtimeErrors = runtimeErrors
        if (trial.maximumScore == null || score > trial.maximumScore!!) {
            trial.maximumScore = score
            trial.maximumScoreTimestampMs = inferenceTimestampMs
        }

        if (score >= detectionThreshold) {
            trial.aboveThresholdWindowCount += 1L
            appendBounded(
                trial.thresholdCrossings,
                WakeWordThresholdCrossing(
                    inferenceWindowSequence = inferenceWindowSequence,
                    inferenceTimestampMs = inferenceTimestampMs,
                    score = score,
                    wakeStateBefore = wakeStateBefore,
                    wakeStateAfter = wakeStateAfter,
                    cooldownRemainingMs = cooldownRemainingMs,
                    generatedWakeEvent = detection != null,
                    suppressedByCooldown = suppressedByCooldown,
                ),
                thresholdCrossingHistoryCapacity,
            )
        }

        detection?.let {
            trial.wakeDetectionCount += 1L
            trial.lastDetectionTimestampMs = inferenceTimestampMs
            trial.lastDetectionIntervalMs = it.millisecondsSincePreviousDetection
            appendBounded(
                trial.detections,
                WakeWordDetectionMetadata(
                    detectionSequenceNumber = it.detectionCount,
                    classifierScore = score,
                    inferenceWindowSequence = inferenceWindowSequence,
                    inferenceTimestampMs = inferenceTimestampMs,
                    wakeStateBefore = wakeStateBefore,
                    wakeStateAfter = wakeStateAfter,
                    cooldownRemainingMs = cooldownRemainingMs,
                    millisecondsSincePreviousDetection =
                        it.millisecondsSincePreviousDetection,
                    workerGeneration = trial.workerGeneration,
                ),
                detectionHistoryCapacity,
            )
        }
    }

    fun finish(
        timestampMs: Long,
        currentWakeState: String,
        cooldownRemainingMs: Long,
        queueDepthFrames: Int,
        queueHighWaterMarkFrames: Int,
        queueDrops: Long,
        runtimeErrors: Long,
    ) = synchronized(lock) {
        val trial = activeTrial ?: return@synchronized
        trial.stopTimestampMs = timestampMs
        trial.currentWakeState = currentWakeState
        trial.cooldownActive = cooldownRemainingMs > 0L
        trial.cooldownRemainingMs = cooldownRemainingMs
        trial.queueDepthFrames = queueDepthFrames
        trial.queueHighWaterMarkFrames = maxOf(trial.queueHighWaterMarkFrames, queueHighWaterMarkFrames)
        trial.queueDrops = queueDrops
        trial.runtimeErrors = runtimeErrors
        completedTrial = snapshotLocked(trial, active = false)
        if (completedHistory.size == 20) completedHistory.removeFirst()
        completedHistory.addLast(completedTrial!!)
        activeTrial = null
    }

    fun snapshot(): WakeWordManualTrialStatus = synchronized(lock) {
        activeTrial?.let { return@synchronized snapshotLocked(it, active = true) }
        completedTrial ?: emptySnapshot()
    }

    fun history(): List<WakeWordManualTrialStatus> = synchronized(lock) {
        completedHistory.toList()
    }

    private fun snapshotLocked(trial: MutableTrial, active: Boolean): WakeWordManualTrialStatus =
        WakeWordManualTrialStatus(
            active = active,
            trialId = trial.trialId,
            microphoneSessionId = trial.microphoneSessionId,
            startTimestampMs = trial.startTimestampMs,
            stopTimestampMs = trial.stopTimestampMs,
            wakeDetectionCount = trial.wakeDetectionCount,
            inferenceWindowCount = trial.inferenceWindowCount,
            aboveThresholdWindowCount = trial.aboveThresholdWindowCount,
            maximumScore = trial.maximumScore,
            maximumScoreTimestampMs = trial.maximumScoreTimestampMs,
            lastDetectionTimestampMs = trial.lastDetectionTimestampMs,
            lastDetectionIntervalMs = trial.lastDetectionIntervalMs,
            currentWakeState = trial.currentWakeState,
            cooldownActive = trial.cooldownActive,
            cooldownRemainingMs = trial.cooldownRemainingMs,
            cooldownDurationMs = cooldownDurationMs,
            queueDepthFrames = trial.queueDepthFrames,
            queueHighWaterMarkFrames = trial.queueHighWaterMarkFrames,
            queueDrops = trial.queueDrops,
            runtimeErrors = trial.runtimeErrors,
            workerGeneration = trial.workerGeneration,
            detections = trial.detections.toList(),
            thresholdCrossings = trial.thresholdCrossings.toList(),
        )

    private fun emptySnapshot() = WakeWordManualTrialStatus(
        active = false,
        trialId = null,
        microphoneSessionId = -1,
        startTimestampMs = 0L,
        stopTimestampMs = 0L,
        wakeDetectionCount = 0L,
        inferenceWindowCount = 0L,
        aboveThresholdWindowCount = 0L,
        maximumScore = null,
        maximumScoreTimestampMs = 0L,
        lastDetectionTimestampMs = 0L,
        lastDetectionIntervalMs = null,
        currentWakeState = WakeWordStateMachine.State.IDLE.name,
        cooldownActive = false,
        cooldownRemainingMs = 0L,
        cooldownDurationMs = cooldownDurationMs,
        queueDepthFrames = 0,
        queueHighWaterMarkFrames = 0,
        queueDrops = 0L,
        runtimeErrors = 0L,
        workerGeneration = 0L,
        detections = emptyList(),
        thresholdCrossings = emptyList(),
    )

    private fun <T> appendBounded(queue: ArrayDeque<T>, value: T, capacity: Int) {
        if (queue.size == capacity) queue.removeFirst()
        queue.addLast(value)
    }

}
