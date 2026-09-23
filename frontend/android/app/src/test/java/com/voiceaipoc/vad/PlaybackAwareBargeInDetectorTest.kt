package com.voiceaipoc.vad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackAwareBargeInDetectorTest {
    private val detector = PlaybackAwareBargeInDetector()
    private var nextNs = 10_000_000_000L

    @Test
    fun normalSpeechWithoutPlaybackUsesIndependentPolicy() {
        val decision = detector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                positionMs = 0L,
                referenceReady = false,
                confidence = BargeInConfig.TIMESTAMP_NONE,
                playbackActive = false,
                playbackState = "STOPPED",
                micRms = 2_000.0,
                residual = 0.70,
            ),
        )
        assertEquals(PlaybackAwareBargeInDetector.EVENT_CONFIRMED, decision?.event)
        assertEquals(BargeInConfig.REASON_NORMAL_SPEECH, decision?.reason)
    }

    @Test
    fun aecUnavailableSelectsDegradedReasonAndStricterPolicy() {
        val decision = detector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                positionMs = 1_300L,
                micRms = 2_000.0,
                residual = 0.70,
                routeHealth = BargeInRouteHealth(
                    communicationModeActive = true,
                    aecAvailable = false,
                    aecEnabled = false,
                    aecEffectiveness = "UNAVAILABLE",
                ),
            ),
        )
        assertEquals(PlaybackAwareBargeInDetector.EVENT_DEGRADED, decision?.event)
        assertEquals(BargeInConfig.REASON_AEC_UNAVAILABLE, decision?.reason)
    }

    @Test
    fun permittedSpeakerWithUnavailableAecConfirmsOnlyWithUsableReferenceAndStrongResidual() {
        val degradedDetector = PlaybackAwareBargeInDetector()
        val unhealthyAec = BargeInRouteHealth(
            communicationModeActive = true,
            aecAvailable = false,
            aecEnabled = false,
            aecEffectiveness = "UNAVAILABLE",
            automaticLoudspeakerBargeInAllowed = true,
        )

        val initial = degradedDetector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                positionMs = 1_300L,
                farEndRms = 2_000.0,
                micRms = 3_000.0,
                residual = 0.70,
                routeHealth = unhealthyAec,
            ),
        )
        assertEquals(PlaybackAwareBargeInDetector.EVENT_DEGRADED, initial?.event)
        assertEquals(BargeInConfig.REASON_AEC_UNAVAILABLE, initial?.reason)

        nextNs += 700_000_000L
        val confirmed = degradedDetector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_ACTIVITY",
                positionMs = 2_000L,
                farEndRms = 2_000.0,
                micRms = 3_000.0,
                residual = 0.70,
                routeHealth = unhealthyAec,
            ),
        )

        assertEquals(PlaybackAwareBargeInDetector.EVENT_CONFIRMED, confirmed?.event)
        assertFalse(confirmed?.aecHealthy ?: true)
        assertTrue(confirmed?.referenceUsable ?: false)
    }

    @Test
    fun ttsOnlySpeechLikeInputNeverConfirms() {
        val start = detector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                positionMs = 0L,
                farEndRms = 4_000.0,
                micRms = 250.0,
                residual = 0.04,
                similarity = 0.92,
                coherence = 0.90,
            ),
        )
        assertEquals(PlaybackAwareBargeInDetector.EVENT_REJECTED_ECHO, start?.event)
        var confirmed = false
        repeat(12) {
            nextNs += 100_000_000L
            val decision = detector.evaluate(
                input(
                    event = "SILERO_VAD_SPEECH_ACTIVITY",
                    positionMs = 1_300L + it * 100L,
                    farEndRms = 4_000.0,
                    micRms = 250.0,
                    residual = 0.04,
                    similarity = 0.92,
                    coherence = 0.90,
                ),
            )
            confirmed = confirmed || decision?.event == PlaybackAwareBargeInDetector.EVENT_CONFIRMED
        }
        assertFalse(confirmed)
    }

    @Test
    fun nearEndOnlySpeechConfirmsAfterNativeCaptureDuration() {
        detector.evaluate(input(event = "SILERO_VAD_SPEECH_STARTED", positionMs = 0L))
        nextNs += 1_300_000_000L
        val decision = detector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_ACTIVITY",
                positionMs = 1_300L,
                farEndRms = 0.0,
                micRms = 2_000.0,
                residual = 0.80,
            ),
        )
        assertEquals(PlaybackAwareBargeInDetector.EVENT_CONFIRMED, decision?.event)
        assertEquals(BargeInConfig.REASON_NEAR_END_CONFIRMED, decision?.reason)
    }

    @Test
    fun mixedDoubleTalkConfirmsWhenResidualEvidenceIsStrong() {
        detector.evaluate(input(event = "SILERO_VAD_SPEECH_STARTED", positionMs = 1_300L))
        nextNs += 500_000_000L
        val decision = detector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_ACTIVITY",
                positionMs = 1_800L,
                farEndRms = 3_000.0,
                micRms = 2_400.0,
                residual = 0.55,
                similarity = 0.85,
                coherence = 0.80,
            ),
        )
        assertEquals(PlaybackAwareBargeInDetector.EVENT_CONFIRMED, decision?.event)
    }

    @Test
    fun guardStartedRealUtteranceCanConfirmAfterReferenceReady() {
        val initial = detector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                positionMs = 0L,
                referenceReady = false,
                confidence = BargeInConfig.TIMESTAMP_NONE,
                farEndRms = 1_000.0,
                micRms = 2_000.0,
                residual = 0.60,
            ),
        )
        assertEquals(PlaybackAwareBargeInDetector.EVENT_DEGRADED, initial?.event)
        nextNs += 1_400_000_000L
        val confirmed = detector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_ACTIVITY",
                positionMs = 1_400L,
                referenceReady = true,
                confidence = BargeInConfig.TIMESTAMP_AUDIO,
                farEndRms = 1_000.0,
                micRms = 2_000.0,
                residual = 0.60,
            ),
        )
        assertEquals(PlaybackAwareBargeInDetector.EVENT_CONFIRMED, confirmed?.event)
    }

    @Test
    fun continuousEchoDoesNotBecomeSpeechWhenGuardExpires() {
        detector.evaluate(input(event = "SILERO_VAD_SPEECH_STARTED", positionMs = 0L))
        repeat(20) {
            nextNs += 100_000_000L
            val decision = detector.evaluate(
                input(
                    event = "SILERO_VAD_SPEECH_ACTIVITY",
                    positionMs = 1_300L + it * 100L,
                    farEndRms = 3_000.0,
                    micRms = 200.0,
                    residual = 0.02,
                    echoLikely = true,
                ),
            )
            assertTrue(decision == null || decision.event != PlaybackAwareBargeInDetector.EVENT_CONFIRMED)
        }
    }

    @Test
    fun lowEnergyAndLowProbabilityNoiseCannotConfirm() {
        detector.evaluate(input(event = "SILERO_VAD_SPEECH_STARTED", positionMs = 1_300L, probability = 0.91f))
        repeat(10) {
            nextNs += 100_000_000L
            val decision = detector.evaluate(
                input(
                    event = "SILERO_VAD_SPEECH_ACTIVITY",
                    positionMs = 1_300L + it * 100L,
                    probability = 0.20f,
                    farEndRms = 0.0,
                    micRms = 250.0,
                    residual = 0.01,
                ),
            )
            assertTrue(decision == null || decision.event != PlaybackAwareBargeInDetector.EVENT_CONFIRMED)
        }
    }

    @Test
    fun unavailableReferenceUsesDocumentedDegradedPolicy() {
        val decision = detector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                positionMs = 1_300L,
                referenceReady = false,
                confidence = BargeInConfig.TIMESTAMP_NONE,
                micRms = 2_000.0,
                residual = 0.60,
                farEndRms = 0.0,
            ),
        )
        assertNotNull(decision)
        assertEquals(PlaybackAwareBargeInDetector.EVENT_DEGRADED, decision?.event)
        assertEquals(BargeInConfig.REASON_REFERENCE_NOT_READY, decision?.reason)
    }

    @Test
    fun unusableReferenceCannotConfirmSustainedHighEnergyTtsLikeLeakage() {
        val referenceCases = listOf(
            false to BargeInConfig.TIMESTAMP_NONE,
            true to BargeInConfig.TIMESTAMP_WRITE,
        )

        referenceCases.forEach { (referenceReady, confidence) ->
            val noReferenceDetector = PlaybackAwareBargeInDetector()
            val expectedReason = if (referenceReady) {
                BargeInConfig.REASON_REFERENCE_TIMING_UNRELIABLE
            } else {
                BargeInConfig.REASON_REFERENCE_NOT_READY
            }

            repeat(12) { index ->
                if (index > 0) nextNs += 100_000_000L
                val decision = noReferenceDetector.evaluate(
                    input(
                        event = if (index == 0) {
                            "SILERO_VAD_SPEECH_STARTED"
                        } else {
                            "SILERO_VAD_SPEECH_ACTIVITY"
                        },
                        positionMs = 1_300L + index * 100L,
                        probability = 0.99f,
                        referenceReady = referenceReady,
                        confidence = confidence,
                        echoLikely = false,
                        similarity = null,
                        coherence = null,
                        farEndRms = null,
                        micRms = 3_000.0,
                        residual = null,
                    ),
                )

                assertEquals(PlaybackAwareBargeInDetector.EVENT_DEGRADED, decision?.event)
                assertEquals(expectedReason, decision?.reason)
            }
        }
    }

    @Test
    fun unsafeLoudspeakerRouteDisablesAutomaticBargeInWithExplicitReason() {
        val decision = detector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                positionMs = 1_300L,
                routeHealth = BargeInRouteHealth(
                    communicationModeActive = false,
                    aecAvailable = false,
                    aecEnabled = false,
                    aecEffectiveness = "UNAVAILABLE",
                    automaticLoudspeakerBargeInAllowed = false,
                ),
            ),
        )

        assertEquals(PlaybackAwareBargeInDetector.EVENT_DEGRADED, decision?.event)
        assertEquals(BargeInConfig.REASON_NO_SAFE_ACOUSTIC_PATH, decision?.reason)
    }

    @Test
    fun earlyBargeInShortensOnlyThePlaybackGuard() {
        val earlyDetector = PlaybackAwareBargeInDetector(
            BargeInConfig(
                earlyBargeInEnabled = true,
                confirmationMs = 200L,
            ),
        )
        earlyDetector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                positionMs = 0L,
                referenceReady = true,
                confidence = BargeInConfig.TIMESTAMP_AUDIO,
            ),
        )
        nextNs += 300_000_000L

        val decision = earlyDetector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_ACTIVITY",
                positionMs = 300L,
                referenceReady = true,
                confidence = BargeInConfig.TIMESTAMP_AUDIO,
            ),
        )

        assertEquals(PlaybackAwareBargeInDetector.EVENT_CONFIRMED, decision?.event)
        assertEquals(BargeInConfig.REASON_NEAR_END_CONFIRMED, decision?.reason)
    }

    @Test
    fun confirmationIsIdempotentPerResponseAndResponseReplacementResetsIt() {
        detector.evaluate(input(event = "SILERO_VAD_SPEECH_STARTED", responseId = "r1", positionMs = 1_300L))
        nextNs += 500_000_000L
        val first = detector.evaluate(input(event = "SILERO_VAD_SPEECH_ACTIVITY", responseId = "r1", positionMs = 1_800L))
        assertEquals(PlaybackAwareBargeInDetector.EVENT_CONFIRMED, first?.event)
        nextNs += 100_000_000L
        assertNull(detector.evaluate(input(event = "SILERO_VAD_SPEECH_ACTIVITY", responseId = "r1", positionMs = 1_900L)))

        detector.evaluate(input(event = "SILERO_VAD_SPEECH_STARTED", responseId = "r2", positionMs = 1_300L))
        nextNs += 500_000_000L
        val replacement = detector.evaluate(input(event = "SILERO_VAD_SPEECH_ACTIVITY", responseId = "r2", positionMs = 1_800L))
        assertEquals(PlaybackAwareBargeInDetector.EVENT_CONFIRMED, replacement?.event)
    }

    @Test
    fun responseReplacementRestartsThePlaybackGuard() {
        val replacementDetector = PlaybackAwareBargeInDetector()
        replacementDetector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                responseId = "r1",
                positionMs = 1_300L,
            ),
        )

        nextNs += 100_000_000L
        val replacement = replacementDetector.evaluate(
            input(
                event = "SILERO_VAD_SPEECH_STARTED",
                responseId = "r2",
                positionMs = 0L,
            ),
        )

        assertTrue(replacement != null)
        assertEquals(PlaybackAwareBargeInDetector.State.PLAYBACK_GUARD, replacement?.state)
        assertTrue(replacement?.event != PlaybackAwareBargeInDetector.EVENT_CONFIRMED)
    }

    @Test
    fun speechStopResetsPendingConfirmationDuration() {
        val resetDetector = PlaybackAwareBargeInDetector()
        resetDetector.evaluate(
            input(event = "SILERO_VAD_SPEECH_STARTED", positionMs = 1_300L),
        )

        nextNs += 250_000_000L
        assertNull(
            resetDetector.evaluate(
                input(event = "SILERO_VAD_SPEECH_STOPPED", positionMs = 1_550L),
            ),
        )

        nextNs += 250_000_000L
        resetDetector.evaluate(
            input(event = "SILERO_VAD_SPEECH_STARTED", positionMs = 1_800L),
        )

        nextNs += 300_000_000L
        val stillPending = resetDetector.evaluate(
            input(event = "SILERO_VAD_SPEECH_ACTIVITY", positionMs = 2_100L),
        )

        assertTrue(stillPending == null || stillPending.event != PlaybackAwareBargeInDetector.EVENT_CONFIRMED)
    }

    @Test
    fun discontinuityDegradesAndDropsThePendingSegment() {
        detector.evaluate(input(event = "SILERO_VAD_SPEECH_STARTED", positionMs = 1_300L))
        val decision = detector.evaluate(input(event = "SILERO_VAD_SPEECH_ACTIVITY", positionMs = 1_400L, discontinuous = true))
        assertEquals(PlaybackAwareBargeInDetector.EVENT_DEGRADED, decision?.event)
        assertEquals(BargeInConfig.REASON_QUEUE_DISCONTINUITY, decision?.reason)
        assertEquals(PlaybackAwareBargeInDetector.State.PLAYBACK_GUARD, detector.currentState())
    }

    private fun input(
        event: String,
        responseId: String? = "response-1",
        positionMs: Long,
        probability: Float = 0.95f,
        referenceReady: Boolean = true,
        confidence: String = BargeInConfig.TIMESTAMP_AUDIO,
        echoLikely: Boolean = false,
        similarity: Double? = 0.10,
        coherence: Double? = 0.10,
        farEndRms: Double? = 0.0,
        micRms: Double? = 2_000.0,
        residual: Double? = 0.70,
        discontinuous: Boolean = false,
        playbackActive: Boolean = true,
        playbackState: String = "PLAYING",
        routeHealth: BargeInRouteHealth = BargeInRouteHealth(),
    ): BargeInInput {
        return BargeInInput(
            event = event,
            monotonicTimestampNs = nextNs,
            captureStartNs = nextNs,
            captureEndNs = nextNs + 20_000_000L,
            probability = probability,
            playbackState = playbackState,
            playbackActive = playbackActive,
            playbackResponseId = responseId,
            playbackPositionMs = positionMs,
            referenceReady = referenceReady,
            referenceConfidence = confidence,
            echoLikely = echoLikely,
            echoSimilarity = similarity,
            echoCoherence = coherence,
            farEndRms = farEndRms,
            micRms = micRms,
            nearEndResidualRatio = residual,
            discontinuous = discontinuous,
            routeHealth = routeHealth,
        )
    }
}
