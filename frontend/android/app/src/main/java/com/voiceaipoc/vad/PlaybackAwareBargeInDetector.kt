package com.voiceaipoc.vad

import com.voiceaipoc.vad.silero.SileroVadEngine
import kotlin.math.max

/**
 * Native, deterministic near-end policy for duplex playback.
 *
 * This class is deliberately a synchronous state machine.  A caller supplies
 * the monotonic/capture timestamps carried by a Silero observation, so tests
 * and production use the same clock domain and no wall-clock callback is
 * needed.  The detector emits metrics only; stopping AudioTrack or cancelling
 * a response remains a later lifecycle decision.
 */
class PlaybackAwareBargeInDetector(
    private val config: BargeInConfig = BargeInConfig(),
) {
    enum class State {
        IDLE,
        PLAYBACK_GUARD,
        ECHO_ONLY,
        NEAR_END_PENDING,
        NEAR_END_CONFIRMED,
        COOLDOWN,
    }

    data class Decision(
        val event: String,
        val state: State,
        val reason: String,
        val input: BargeInInput,
        val responseId: String?,
        val segmentDurationMs: Long,
        val referenceUsable: Boolean,
        val timestampConfidence: String,
        val aecHealthy: Boolean,
    )

    data class Status(
        val state: State,
        val lastEvent: String,
        val lastReason: String,
        val candidateCount: Long,
        val rejectedEchoCount: Long,
        val confirmedCount: Long,
        val degradedCount: Long,
        val lastResponseId: String?,
        val lastSourceFrameSequenceStart: Long,
        val lastSourceFrameSequenceEnd: Long,
        val lastInferenceIndex: Long,
        val lastLocalStopLatencyMs: Long?,
        val lastLocalStopResponseId: String?,
    )

    companion object {
        const val EVENT_CANDIDATE = "BARGE_IN_CANDIDATE"
        const val EVENT_REJECTED_ECHO = "BARGE_IN_REJECTED_ECHO"
        const val EVENT_CONFIRMED = "BARGE_IN_CONFIRMED"
        const val EVENT_DEGRADED = "BARGE_IN_DEGRADED"

        private val ACTIVE_PLAYBACK_STATES = setOf(
            "BUFFERING",
            "PLAYING",
            "DRAINING",
            "TAIL_SUPPRESSION",
            "MIXED",
        )
        private val TERMINAL_PLAYBACK_STATES = setOf("STOPPED", "COMPLETED")
    }

    private var state = State.IDLE
    private var activeResponseId: String? = null
    private var confirmedResponseId: String? = null
    private var playbackStartedNs: Long? = null
    private var segmentStartedNs: Long? = null
    private var lastEvent = "NONE"
    private var lastReason = "NONE"
    private var candidateCount = 0L
    private var rejectedEchoCount = 0L
    private var confirmedCount = 0L
    private var degradedCount = 0L
    private var lastDecision: Decision? = null
    private var lastLocalStopLatencyMs: Long? = null
    private var lastLocalStopResponseId: String? = null

    @Synchronized
    fun currentState(): State = state

    @Synchronized
    fun getStatus(): Status = Status(
        state = state,
        lastEvent = lastEvent,
        lastReason = lastReason,
        candidateCount = candidateCount,
        rejectedEchoCount = rejectedEchoCount,
        confirmedCount = confirmedCount,
        degradedCount = degradedCount,
        lastResponseId = lastDecision?.responseId,
        lastSourceFrameSequenceStart = lastDecision?.input?.sourceFrameSequenceStart ?: 0L,
        lastSourceFrameSequenceEnd = lastDecision?.input?.sourceFrameSequenceEnd ?: 0L,
        lastInferenceIndex = lastDecision?.input?.inferenceIndex ?: 0L,
        lastLocalStopLatencyMs = lastLocalStopLatencyMs,
        lastLocalStopResponseId = lastLocalStopResponseId,
    )

    @Synchronized
    fun recordLocalStopLatency(responseId: String, latencyMs: Long) {
        lastLocalStopLatencyMs = latencyMs.coerceAtLeast(0L)
        lastLocalStopResponseId = responseId
    }

    /** Clears PCM ownership, response correlation, and timing state together. */
    @Synchronized
    fun reset(@Suppress("UNUSED_PARAMETER") reason: String = "reset") {
        state = State.IDLE
        activeResponseId = null
        confirmedResponseId = null
        playbackStartedNs = null
        segmentStartedNs = null
        lastReason = reason
    }

    /** Convenience adapter for the native Silero event type. */
    fun onSileroEvent(
        event: SileroVadEngine.Event,
        routeHealth: BargeInRouteHealth = BargeInRouteHealth(),
    ): Decision? = evaluate(
        BargeInInput(
            event = event.event,
            monotonicTimestampNs = event.monotonicTimestampNs,
            captureStartNs = event.captureStartNs,
            captureEndNs = event.captureEndNs,
            probability = event.probability,
            playbackState = event.playbackState,
            playbackActive = event.playbackActive,
            playbackResponseId = event.playbackResponseId,
            playbackPositionMs = event.playbackPositionMs,
            referenceReady = event.referenceReady,
            referenceConfidence = event.referenceConfidence,
            estimatedDelayMs = event.estimatedEchoDelayMs,
            echoLikely = event.echoLikely,
            echoSimilarity = event.echoSimilarity,
            echoCoherence = event.echoCoherence,
            farEndRms = event.farEndRms,
            micRms = event.micRms,
            nearEndResidualRatio = event.nearEndResidualRatio,
            nearEndFarEndEnergyRatio = event.nearEndFarEndEnergyRatio,
            discontinuous = event.discontinuous,
            routeHealth = routeHealth,
            sourceFrameSequenceStart = event.sourceFrameSequenceStart,
            sourceFrameSequenceEnd = event.sourceFrameSequenceEnd,
            inferenceIndex = event.inferenceIndex,
        ),
    )

    /** Evaluates one observation without allocating or consulting wall time. */
    @Synchronized
    fun evaluate(input: BargeInInput): Decision? {
        val playbackActive = input.playbackActive &&
            (input.playbackState in ACTIVE_PLAYBACK_STATES ||
                input.playbackState !in TERMINAL_PLAYBACK_STATES)

        if (!playbackActive) {
            if (input.playbackState in TERMINAL_PLAYBACK_STATES) {
                reset("playback_completed")
            }
            if (input.discontinuous) {
                reset("queue_discontinuity")
                return decision(EVENT_DEGRADED, State.IDLE, BargeInConfig.REASON_QUEUE_DISCONTINUITY, input)
            }
            if (input.event == SileroVadEngine.EVENT_SPEECH_STARTED &&
                input.probability >= config.normalSpeechProbabilityThreshold
            ) {
                state = State.NEAR_END_CONFIRMED
                return decision(EVENT_CONFIRMED, state, BargeInConfig.REASON_NORMAL_SPEECH, input)
            }
            if (input.event == SileroVadEngine.EVENT_SPEECH_STOPPED) {
                reset("speech_stopped")
            }
            return null
        }

        synchronizeResponse(input)

        if (input.event == SileroVadEngine.EVENT_SPEECH_STOPPED) {
            segmentStartedNs = null
            state = if (isInGuard(input)) State.PLAYBACK_GUARD else State.IDLE
            return null
        }

        if (!input.routeHealth.automaticLoudspeakerBargeInAllowed) {
            state = State.PLAYBACK_GUARD
            return decision(
                EVENT_DEGRADED,
                state,
                BargeInConfig.REASON_NO_SAFE_ACOUSTIC_PATH,
                input,
                referenceUsable = false,
                aecHealthy = false,
            )
        }

        if (state == State.NEAR_END_CONFIRMED || state == State.COOLDOWN) {
            state = State.COOLDOWN
            return null
        }

        if (input.discontinuous) {
            segmentStartedNs = null
            state = State.PLAYBACK_GUARD
            return decision(EVENT_DEGRADED, state, BargeInConfig.REASON_QUEUE_DISCONTINUITY, input)
        }

        if (input.event != SileroVadEngine.EVENT_SPEECH_STARTED &&
            input.event != SileroVadEngine.EVENT_SPEECH_ACTIVITY
        ) {
            return null
        }

        if (segmentStartedNs == null) {
            segmentStartedNs = usableCaptureStartNs(input)
        }

        val referenceUsable = input.referenceReady &&
            BargeInConfig.isReliableReferenceConfidence(input.referenceConfidence)
        val referenceReason = when {
            !input.referenceReady -> BargeInConfig.REASON_REFERENCE_NOT_READY
            !BargeInConfig.isReliableReferenceConfidence(input.referenceConfidence) ->
                BargeInConfig.REASON_REFERENCE_TIMING_UNRELIABLE
            else -> null
        }
        val aecHealthy = input.routeHealth.communicationModeActive &&
            input.routeHealth.aecAvailable &&
            input.routeHealth.aecEnabled
        val strictFallback = !referenceUsable || !aecHealthy
        val degradedReason = referenceReason ?: if (!aecHealthy) {
            BargeInConfig.REASON_AEC_UNAVAILABLE
        } else {
            null
        }
        val threshold = if (strictFallback) {
            max(
                config.playbackSpeechProbabilityThreshold,
                config.degradedSpeechProbabilityThreshold,
            )
        } else {
            config.playbackSpeechProbabilityThreshold
        }
        val sileroQualifies = input.probability >= threshold
        val nearEndEvidence = hasNearEndEvidence(input)
        val echoDominant = isEchoDominant(input, nearEndEvidence)

        if (echoDominant) {
            state = State.ECHO_ONLY
            val reason = if (isFarEndDominant(input)) {
                BargeInConfig.REASON_FAR_END_DOMINANT
            } else {
                BargeInConfig.REASON_ECHO_SIMILARITY_HIGH
            }
            return decision(EVENT_REJECTED_ECHO, state, reason, input, referenceUsable, aecHealthy)
        }

        val guard = isInGuard(input)
        state = if (guard) State.PLAYBACK_GUARD else State.NEAR_END_PENDING

        if (!sileroQualifies) {
            return decision(
                if (strictFallback) EVENT_DEGRADED else EVENT_CANDIDATE,
                state,
                BargeInConfig.REASON_SILERO_PROBABILITY_LOW,
                input,
                referenceUsable,
                aecHealthy,
            )
        }

        if (!nearEndEvidence) {
            return decision(
                if (strictFallback) EVENT_DEGRADED else EVENT_CANDIDATE,
                state,
                BargeInConfig.REASON_NEAR_END_ENERGY_INSUFFICIENT,
                input,
                referenceUsable,
                aecHealthy,
            )
        }

        if (guard) {
            return decision(
                if (strictFallback) EVENT_DEGRADED else EVENT_CANDIDATE,
                state,
                degradedReason ?: BargeInConfig.REASON_PLAYBACK_GUARD,
                input,
                referenceUsable,
                aecHealthy,
            )
        }

        val segmentDurationMs = segmentDurationMs(input)
        val requiredDurationMs = if (strictFallback) {
            config.degradedConfirmationMs
        } else {
            config.confirmationMs
        }
        if (confirmedResponseId != null && confirmedResponseId == activeResponseId) {
            state = State.COOLDOWN
            return null
        }
        if (segmentDurationMs < requiredDurationMs) {
            return decision(
                if (strictFallback) EVENT_DEGRADED else EVENT_CANDIDATE,
                state,
                degradedReason ?: BargeInConfig.REASON_CONFIRMATION_INCOMPLETE,
                input,
                referenceUsable,
                aecHealthy,
            )
        }

        confirmedResponseId = activeResponseId
        state = State.NEAR_END_CONFIRMED
        return decision(
            EVENT_CONFIRMED,
            state,
            BargeInConfig.REASON_NEAR_END_CONFIRMED,
            input,
            referenceUsable,
            aecHealthy,
        )
    }

    private fun synchronizeResponse(input: BargeInInput) {
        if (activeResponseId != null &&
            input.playbackResponseId != null &&
            activeResponseId != input.playbackResponseId
        ) {
            reset("response_replaced")
        }
        if (activeResponseId == null &&
            input.playbackResponseId != null &&
            segmentStartedNs != null
        ) {
            reset("response_replaced")
        }
        if (activeResponseId == null) {
            activeResponseId = input.playbackResponseId
        }
        if (playbackStartedNs == null) {
            playbackStartedNs = usableCaptureStartNs(input) -
                input.playbackPositionMs.coerceAtLeast(0L) * 1_000_000L
        }
    }

    private fun isInGuard(input: BargeInInput): Boolean {
        val started = playbackStartedNs ?: return true
        val elapsedNs = usableCaptureStartNs(input) - started
        return elapsedNs < config.effectivePlaybackGuardMs * 1_000_000L
    }

    private fun usableCaptureStartNs(input: BargeInInput): Long = when {
        input.captureStartNs > 0L -> input.captureStartNs
        input.monotonicTimestampNs > 0L -> input.monotonicTimestampNs
        else -> 0L
    }

    private fun segmentDurationMs(input: BargeInInput): Long {
        val start = segmentStartedNs ?: return 0L
        val end = when {
            input.captureEndNs > 0L -> input.captureEndNs
            input.monotonicTimestampNs > 0L -> input.monotonicTimestampNs
            else -> start
        }
        return ((end - start).coerceAtLeast(0L) / 1_000_000L)
    }

    private fun hasNearEndEvidence(input: BargeInInput): Boolean {
        val micRms = input.micRms ?: 0.0
        if (micRms < config.minimumMicRms) return false
        val residual = input.nearEndResidualRatio
        val farEnd = input.farEndRms
        val residualStrong = residual != null && residual >= config.minimumResidualRatio
        val farEndQuiet = farEnd == null || farEnd < config.minimumFarEndRms
        return residualStrong || farEndQuiet
    }

    private fun isEchoDominant(input: BargeInInput, nearEndEvidence: Boolean): Boolean {
        if (nearEndEvidence) return false
        val similarityHigh = (input.echoSimilarity ?: 0.0) >= config.echoSimilarityThreshold
        val coherenceHigh = (input.echoCoherence ?: 0.0) >= config.echoCoherenceThreshold
        val correlationHigh = input.echoLikely || similarityHigh || coherenceHigh
        if (!correlationHigh) return false
        val residualLow = input.nearEndResidualRatio?.let {
            it <= config.maximumEchoResidualRatio
        } ?: true
        if (!residualLow) return false
        return input.farEndRms?.let { farEnd ->
            farEnd >= config.minimumFarEndRms &&
                farEnd >= (input.micRms ?: 0.0) * config.farEndDominanceRatio
        } ?: input.echoLikely
    }

    private fun isFarEndDominant(input: BargeInInput): Boolean {
        val farEnd = input.farEndRms ?: return false
        return farEnd >= config.minimumFarEndRms &&
            farEnd >= (input.micRms ?: 0.0) * config.farEndDominanceRatio
    }

    private fun decision(
        event: String,
        state: State,
        reason: String,
        input: BargeInInput,
        referenceUsable: Boolean = input.referenceReady &&
            BargeInConfig.isReliableReferenceConfidence(input.referenceConfidence),
        aecHealthy: Boolean = input.routeHealth.communicationModeActive &&
            input.routeHealth.aecAvailable && input.routeHealth.aecEnabled,
    ): Decision {
        val result = Decision(
        event = event,
        state = state,
        reason = reason,
        input = input,
        responseId = activeResponseId ?: input.playbackResponseId,
        segmentDurationMs = segmentDurationMs(input),
        referenceUsable = referenceUsable,
        timestampConfidence = input.referenceConfidence,
        aecHealthy = aecHealthy,
        )
        when (event) {
            EVENT_CANDIDATE -> candidateCount += 1L
            EVENT_REJECTED_ECHO -> rejectedEchoCount += 1L
            EVENT_CONFIRMED -> confirmedCount += 1L
            EVENT_DEGRADED -> degradedCount += 1L
        }
        lastEvent = event
        lastReason = reason
        lastDecision = result
        return result
    }
}
