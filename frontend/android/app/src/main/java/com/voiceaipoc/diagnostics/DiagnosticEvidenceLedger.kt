package com.voiceaipoc.diagnostics

import com.voiceaipoc.vad.PlaybackAwareBargeInDetector
import com.voiceaipoc.voice.VoiceWebSocketTransport

/**
 * Bounded metadata-only history for playback-aware detector decisions.
 *
 * This is intentionally not a logging sink: it never accepts PCM, transcript
 * text, tokens, or provider credentials. Every retained decision carries the
 * response and capture/inference frame correlation needed to explain it.
 */
class DiagnosticEvidenceLedger(
    private val diagnosticSession: DiagnosticSessionContext,
    private val capacity: Int = DEFAULT_CAPACITY,
) {
    data class DecisionRecord(
        val diagnosticSessionId: String,
        val event: String,
        val state: String,
        val reason: String,
        val responseId: String?,
        val monotonicNs: Long,
        val captureStartNs: Long,
        val captureEndNs: Long,
        val sourceFrameSequenceStart: Long,
        val sourceFrameSequenceEnd: Long,
        val inferenceIndex: Long,
        val probability: Double,
        val playbackState: String,
        val playbackPositionMs: Long,
        val referenceReady: Boolean,
        val referenceUsable: Boolean,
        val timestampConfidence: String,
        val aecHealthy: Boolean,
        val communicationModeActive: Boolean,
        val aecAvailable: Boolean,
        val aecEnabled: Boolean,
        val aecEffectiveness: String,
        val echoSimilarity: Double?,
        val echoCoherence: Double?,
        val estimatedDelayMs: Int?,
        val farEndRms: Double?,
        val micRms: Double?,
        val nearEndFarEndEnergyRatio: Double?,
        val discontinuous: Boolean,
        val localStopLatencyMs: Long?,
    )

    private val lock = Any()
    private val records = ArrayDeque<DecisionRecord>()

    init {
        require(capacity > 0) { "capacity must be positive" }
    }

    fun record(
        decision: PlaybackAwareBargeInDetector.Decision,
        playbackStopAck: VoiceWebSocketTransport.BargeInPlaybackStopAck? = null,
    ) = synchronized(lock) {
        val input = decision.input
        val stopLatencyMs = playbackStopAck?.let {
            ((it.stopRequestedMonotonicNs - it.detectionMonotonicNs) / 1_000_000L)
                .coerceAtLeast(0L)
        }
        records.addLast(
            DecisionRecord(
                diagnosticSessionId = diagnosticSession.currentId()
                    ?: DiagnosticSessionContext.NONE,
                event = decision.event,
                state = decision.state.name,
                reason = decision.reason,
                responseId = decision.responseId,
                monotonicNs = input.monotonicTimestampNs,
                captureStartNs = input.captureStartNs,
                captureEndNs = input.captureEndNs,
                sourceFrameSequenceStart = input.sourceFrameSequenceStart,
                sourceFrameSequenceEnd = input.sourceFrameSequenceEnd,
                inferenceIndex = input.inferenceIndex,
                probability = input.probability.toDouble(),
                playbackState = input.playbackState,
                playbackPositionMs = input.playbackPositionMs,
                referenceReady = input.referenceReady,
                referenceUsable = decision.referenceUsable,
                timestampConfidence = decision.timestampConfidence,
                aecHealthy = decision.aecHealthy,
                communicationModeActive = input.routeHealth.communicationModeActive,
                aecAvailable = input.routeHealth.aecAvailable,
                aecEnabled = input.routeHealth.aecEnabled,
                aecEffectiveness = input.routeHealth.aecEffectiveness,
                echoSimilarity = input.echoSimilarity,
                echoCoherence = input.echoCoherence,
                estimatedDelayMs = input.estimatedDelayMs,
                farEndRms = input.farEndRms,
                micRms = input.micRms,
                nearEndFarEndEnergyRatio = input.nearEndFarEndEnergyRatio
                    ?: input.micRms?.let { mic -> input.farEndRms?.takeIf { it > 0.0 }?.let { mic / it } },
                discontinuous = input.discontinuous,
                localStopLatencyMs = stopLatencyMs,
            ),
        )
        while (records.size > capacity) records.removeFirst()
    }

    fun snapshot(): List<DecisionRecord> = synchronized(lock) { records.toList() }

    companion object {
        const val DEFAULT_CAPACITY = 512
    }
}
