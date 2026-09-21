package com.voiceaipoc.vad.silero

import com.voiceaipoc.audio.FarEndReferenceBuffer
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SileroVadEngineTest {
    private class RecordingListener(
        startedCount: Int = 1,
        errorCount: Int = 1,
    ) : SileroVadEngine.Listener {
        val startedLatch = CountDownLatch(startedCount)
        val stoppedLatch = CountDownLatch(1)
        val errorLatch = CountDownLatch(errorCount)
        val speechStarted = Collections.synchronizedList(
            mutableListOf<SileroVadEngine.Event>(),
        )
        val speechStopped = Collections.synchronizedList(
            mutableListOf<SileroVadEngine.Event>(),
        )

        override fun onEngineStarted(status: SileroVadEngine.Status) {
            startedLatch.countDown()
        }

        override fun onEngineStopped(status: SileroVadEngine.Status) {
            stoppedLatch.countDown()
        }

        override fun onSpeechStarted(event: SileroVadEngine.Event) {
            speechStarted += event
        }

        override fun onSpeechStopped(event: SileroVadEngine.Event) {
            speechStopped += event
        }

        override fun onEngineError(status: SileroVadEngine.Status) {
            errorLatch.countDown()
        }
    }

    private class ScriptedRuntime(
        probabilities: List<Float> = listOf(0.1f),
        private val initializationFailure: RuntimeException? = null,
        private val inferenceFailure: SileroVadRuntimeException? = null,
        private val initializationGate: CountDownLatch? = null,
    ) : SileroVadRuntime {
        override val runtimeName = "SCRIPTED_TEST_RUNTIME"
        override val runtimeVersion = "TEST_ONLY"
        private val probabilities = probabilities.toMutableList()
        val initializationEntered = CountDownLatch(1)
        val inferenceLatch = CountDownLatch(1)
        val closeLatch = CountDownLatch(1)
        var initializeCount = 0
        var inferCount = 0
        var resetCount = 0
        var closeCount = 0
        var lastInput: ShortArray? = null

        override fun initialize() {
            initializeCount += 1
            initializationEntered.countDown()
            initializationGate?.await(2, TimeUnit.SECONDS)
            initializationFailure?.let { throw it }
        }

        override fun infer(pcm16: ShortArray, samplesRead: Int): Float {
            inferCount += 1
            lastInput = pcm16.copyOf(samplesRead)
            inferenceLatch.countDown()
            inferenceFailure?.let { throw it }
            return synchronized(probabilities) {
                if (probabilities.size > 1) probabilities.removeAt(0) else probabilities.first()
            }
        }

        override fun reset() {
            resetCount += 1
        }

        override fun close() {
            closeCount += 1
            closeLatch.countDown()
        }
    }

    @Test
    fun missingApprovedModelReportsUnavailableWithoutWorker() {
        val listener = RecordingListener()
        val engine = newEngine(
            modelAsset = MODEL_MISSING,
            runtimeAvailable = false,
            runtimeFactory = SileroVadRuntimeFactory { ScriptedRuntime() },
            listener = listener,
        )

        val result = engine.startSession()

        assertFalse(result.succeeded)
        assertEquals(SileroVadEngine.ERROR_MODEL_MISSING, result.errorCode)
        assertTrue(listener.errorLatch.await(1, TimeUnit.SECONDS))
        assertFalse(engine.getStatus().modelPresent)
        assertFalse(engine.getStatus().inferenceAvailable)
        assertFalse(engine.getStatus().workerThreadAlive)
        engine.stopSession()
    }

    @Test
    fun unavailableRuntimeIsReportedWithoutClaimingInference() {
        val listener = RecordingListener()
        val engine = newEngine(
            runtimeAvailable = false,
            runtimeFactory = SileroVadRuntimeFactory { ScriptedRuntime() },
            listener = listener,
        )

        val result = engine.startSession()

        assertFalse(result.succeeded)
        assertEquals(SileroVadEngine.ERROR_RUNTIME_UNAVAILABLE, result.errorCode)
        assertFalse(engine.getStatus().runtimeAvailable)
        assertEquals(0L, engine.getStatus().inferenceCount)
        engine.stopSession()
    }

    @Test
    fun existingTwentyMillisecondFramesAssembleContinuousFiveHundredTwelveSamples() {
        val runtime = ScriptedRuntime()
        val listener = RecordingListener()
        val engine = newRunningEngine(runtime, listener)
        val first = ShortArray(FRAME_SAMPLES) { 1 }
        val second = ShortArray(FRAME_SAMPLES) { 2 }

        assertTrue(engine.offerPcmFrame(first, first.size))
        assertTrue(engine.offerPcmFrame(second, second.size))
        assertTrue(runtime.inferenceLatch.await(1, TimeUnit.SECONDS))

        val input = runtime.lastInput
        assertNotNull(input)
        assertEquals(INFERENCE_SAMPLES, input?.size)
        assertEquals(1, input?.get(0)?.toInt())
        assertEquals(1, input?.get(FRAME_SAMPLES - 1)?.toInt())
        assertEquals(2, input?.get(FRAME_SAMPLES)?.toInt())
        assertEquals(2, input?.get(INFERENCE_SAMPLES - 1)?.toInt())
        assertEquals(1L, engine.getStatus().successfulInferenceCount)
        engine.stopSession()
    }

    @Test
    fun inferenceObservationCarriesTheExactSourceIntervalAndMetadataSnapshot() {
        val runtime = ScriptedRuntime(probabilities = listOf(0.9f))
        val listener = RecordingListener()
        val engine = newEngine(
            config = CONFIG.copy(speechStartConfirmationMs = 32),
            runtimeFactory = SileroVadRuntimeFactory { runtime },
            listener = listener,
        )
        assertTrue(engine.startSession().succeeded)
        assertTrue(listener.startedLatch.await(1, TimeUnit.SECONDS))

        val oldAssessment = FarEndReferenceBuffer.Assessment.stopped(1_020_000_000L).copy(
            state = FarEndReferenceBuffer.State.PLAYING,
            playbackActive = true,
            playbackPositionMs = 120L,
            responseId = "old-response",
            referenceAvailable = true,
            timestampConfidence = "AUDIO_TIMESTAMP",
            estimatedDelayMs = 40,
            similarity = 0.8,
        )
        val newAssessment = oldAssessment.copy(
            responseId = "new-response",
            playbackPositionMs = 140L,
            estimatedDelayMs = 60,
        )
        assertTrue(
            engine.offerPcmFrame(
                ShortArray(FRAME_SAMPLES) { 1 },
                FRAME_SAMPLES,
                frameSequence = 10L,
                captureStartNs = 1_000_000_000L,
                captureEndNs = 1_020_000_000L,
                assessment = oldAssessment,
            ),
        )
        assertTrue(
            engine.offerPcmFrame(
                ShortArray(FRAME_SAMPLES) { 2 },
                FRAME_SAMPLES,
                frameSequence = 11L,
                captureStartNs = 1_020_000_000L,
                captureEndNs = 1_040_000_000L,
                assessment = newAssessment,
            ),
        )

        waitUntil { listener.speechStarted.size == 1 }
        val event = listener.speechStarted.single()
        assertEquals(10L, event.sourceFrameSequenceStart)
        assertEquals(11L, event.sourceFrameSequenceEnd)
        assertEquals(1_000_000_000L, event.captureStartNs)
        assertEquals(1_032_000_000L, event.captureEndNs)
        assertEquals("AUDIO_TIMESTAMP", event.referenceConfidence)
        assertEquals("MIXED", event.playbackResponseId ?: "MIXED")
        assertEquals(48, event.estimatedEchoDelayMs)
        assertEquals(128L, event.playbackPositionMs)

        assertTrue(
            engine.offerPcmFrame(
                ShortArray(FRAME_SAMPLES) { 3 },
                FRAME_SAMPLES,
                frameSequence = 12L,
                captureStartNs = 1_040_000_000L,
                captureEndNs = 1_060_000_000L,
                assessment = oldAssessment.copy(responseId = "newer-response"),
            ),
        )
        assertEquals("AUDIO_TIMESTAMP", event.referenceConfidence)
        assertEquals(10L, event.sourceFrameSequenceStart)
        engine.stopSession()
    }

    @Test
    fun queueOverflowMarksNextObservationDiscontinuousAndKeepsFrameMetadataAligned() {
        val releaseInitialization = CountDownLatch(1)
        val runtime = ScriptedRuntime(
            probabilities = listOf(0.9f),
            initializationGate = releaseInitialization,
        )
        val listener = RecordingListener()
        val engine = newEngine(
            config = CONFIG.copy(
                speechStartConfirmationMs = 32,
                queueCapacityFrames = 2,
            ),
            runtimeFactory = SileroVadRuntimeFactory { runtime },
            listener = listener,
        )
        var startResult: SileroVadEngine.StartResult? = null
        val startThread = Thread { startResult = engine.startSession() }
        startThread.start()
        assertTrue(runtime.initializationEntered.await(1, TimeUnit.SECONDS))

        repeat(3) { index ->
            val sequence = (index + 1).toLong()
            assertTrue(
                engine.offerPcmFrame(
                    ShortArray(FRAME_SAMPLES) { (index + 1).toShort() },
                    FRAME_SAMPLES,
                    sequence,
                    sequence * 20_000_000L,
                    (sequence + 1L) * 20_000_000L,
                    FarEndReferenceBuffer.Assessment.stopped(sequence).copy(
                        responseId = "response-$sequence",
                        referenceAvailable = true,
                        timestampConfidence = "AUDIO_TIMESTAMP",
                    ),
                ),
            )
        }
        assertEquals(1L, engine.getStatus().droppedFrames)
        releaseInitialization.countDown()
        startThread.join(1_000L)
        assertTrue(startResult?.succeeded == true)

        waitUntil { listener.speechStarted.size == 1 }
        val event = listener.speechStarted.single()
        assertEquals(2L, event.sourceFrameSequenceStart)
        assertEquals(3L, event.sourceFrameSequenceEnd)
        assertTrue(event.discontinuous)
        assertTrue(runtime.resetCount >= 2)
        assertEquals(2, runtime.lastInput?.firstOrNull()?.toInt())
        assertEquals(2, runtime.lastInput?.get(FRAME_SAMPLES - 1)?.toInt())
        assertEquals(3, runtime.lastInput?.get(FRAME_SAMPLES)?.toInt())
        engine.stopSession()
    }

    @Test
    fun sessionRestartClearsQueuedMetadataAndSequenceOwnership() {
        val runtimes = Collections.synchronizedList(mutableListOf<ScriptedRuntime>())
        val listener = RecordingListener(startedCount = 2)
        val engine = newEngine(
            config = CONFIG.copy(speechStartConfirmationMs = 32),
            runtimeFactory = SileroVadRuntimeFactory {
                ScriptedRuntime(probabilities = listOf(0.9f)).also { runtimes += it }
            },
            listener = listener,
        )

        assertTrue(engine.startSession().succeeded)
        waitUntil { engine.getStatus().runtimeInitialized }
        offerMetadataFrames(engine, 100L, "old-response")
        waitUntil { listener.speechStarted.size == 1 }
        assertEquals("old-response", listener.speechStarted.single().playbackResponseId)
        engine.stopSession()

        assertTrue(engine.startSession().succeeded)
        waitUntil { engine.getStatus().runtimeInitialized }
        offerMetadataFrames(engine, 0L, "new-response")
        waitUntil { listener.speechStarted.size == 2 }

        val restartedEvent = listener.speechStarted.last()
        assertEquals(0L, restartedEvent.sourceFrameSequenceStart)
        assertEquals(1L, restartedEvent.sourceFrameSequenceEnd)
        assertEquals("new-response", restartedEvent.playbackResponseId)
        assertFalse(restartedEvent.discontinuous)
        assertEquals(2, runtimes.size)
        assertTrue(listener.startedLatch.await(1, TimeUnit.SECONDS))
        engine.stopSession()
    }

    @Test
    fun malformedInputIsRejectedAndObservable() {
        val runtime = ScriptedRuntime()
        val listener = RecordingListener()
        val engine = newRunningEngine(runtime, listener)

        assertFalse(engine.offerPcmFrame(ShortArray(FRAME_SAMPLES - 1), FRAME_SAMPLES - 1))

        assertEquals(1L, engine.getStatus().malformedFrames)
        assertEquals(SileroVadEngine.ERROR_MALFORMED_PCM, engine.getStatus().lastErrorCode)
        assertTrue(listener.errorLatch.await(1, TimeUnit.SECONDS))
        engine.stopSession()
    }

    @Test
    fun runtimeInitializationFailureClosesRuntimeAndReportsActualError() {
        val runtime = ScriptedRuntime(
            initializationFailure = IllegalStateException("synthetic invalid model"),
        )
        val listener = RecordingListener()
        val engine = newEngine(
            runtimeFactory = SileroVadRuntimeFactory { runtime },
            listener = listener,
        )

        val result = engine.startSession()
        assertFalse(result.succeeded)
        assertEquals(SileroVadEngine.ERROR_RUNTIME_INITIALIZATION, result.errorCode)
        assertTrue(listener.errorLatch.await(1, TimeUnit.SECONDS))
        waitUntil { !engine.getStatus().workerThreadAlive }

        assertEquals(
            SileroVadEngine.ERROR_RUNTIME_INITIALIZATION,
            engine.getStatus().lastErrorCode,
        )
        assertFalse(engine.getStatus().inferenceAvailable)
        assertTrue(runtime.closeLatch.await(1, TimeUnit.SECONDS))
        engine.stopSession()
    }

    @Test
    fun inferenceFailureStopsOnlySileroWorkerAndCountsFailedCall() {
        val runtime = ScriptedRuntime(
            inferenceFailure = SileroVadRuntimeException(
                SileroVadEngine.ERROR_INFERENCE,
                "synthetic inference failure",
            ),
        )
        val listener = RecordingListener()
        val engine = newRunningEngine(runtime, listener)
        offerFrames(engine, 2)

        assertTrue(listener.errorLatch.await(1, TimeUnit.SECONDS))
        waitUntil { !engine.getStatus().workerThreadAlive }

        val status = engine.getStatus()
        assertEquals(1L, status.inferenceCount)
        assertEquals(0L, status.successfulInferenceCount)
        assertEquals(1L, status.failedInferenceCount)
        assertEquals(SileroVadEngine.ERROR_INFERENCE, status.lastErrorCode)
        assertFalse(status.inferenceAvailable)
        engine.stopSession()
    }

    @Test
    fun boundedQueueDropsOldestWhileRuntimeInitializationIsBlocked() {
        val releaseInitialization = CountDownLatch(1)
        val runtime = ScriptedRuntime(initializationGate = releaseInitialization)
        val listener = RecordingListener()
        val engine = newEngine(
            runtimeFactory = SileroVadRuntimeFactory { runtime },
            listener = listener,
        )
        var startResult: SileroVadEngine.StartResult? = null
        val startThread = Thread { startResult = engine.startSession() }
        startThread.start()
        assertTrue(runtime.initializationEntered.await(1, TimeUnit.SECONDS))

        offerFrames(engine, QUEUE_CAPACITY + 5)

        assertEquals(5L, engine.getStatus().droppedFrames)
        assertEquals(QUEUE_CAPACITY, engine.getStatus().queueHighWaterMarkFrames)
        assertTrue(engine.getStatus().queueDepthFrames <= QUEUE_CAPACITY)
        releaseInitialization.countDown()
        startThread.join(1_000L)
        assertTrue(startResult?.succeeded == true)
        engine.stopSession()
    }

    @Test
    fun scriptedProbabilitiesValidateSemanticTransitionsWithoutClaimingOnnxInference() {
        val probabilities = buildList {
            repeat(CONFIG.speechStartConfirmationChunks) { add(0.9f) }
            repeat(10) { add(0.1f) }
        }
        val runtime = ScriptedRuntime(probabilities)
        val listener = RecordingListener()
        val engine = newEngine(
            config = CONFIG.copy(queueCapacityFrames = 32),
            runtimeFactory = SileroVadRuntimeFactory { runtime },
            listener = listener,
        )
        assertTrue(engine.startSession().succeeded)
        assertTrue(listener.startedLatch.await(1, TimeUnit.SECONDS))

        offerFrames(engine, 31)
        waitUntil { listener.speechStarted.size == 1 && listener.speechStopped.size == 1 }

        assertEquals(1, listener.speechStarted.size)
        assertEquals(1, listener.speechStopped.size)
        assertTrue(engine.getStatus().inferenceCount >= 15L)
        engine.stopSession()
    }

    @Test
    fun repeatedStartStopCreatesFreshRuntimeAndClearsState() {
        val runtimes = Collections.synchronizedList(mutableListOf<ScriptedRuntime>())
        val listener = RecordingListener(startedCount = 2)
        val engine = newEngine(
            runtimeFactory = SileroVadRuntimeFactory {
                ScriptedRuntime().also { runtimes += it }
            },
            listener = listener,
        )

        assertTrue(engine.startSession().succeeded)
        waitUntil { runtimes.size == 1 && engine.getStatus().runtimeInitialized }
        offerFrames(engine, 2)
        waitUntil { engine.getStatus().inferenceCount == 1L }
        engine.stopSession()

        assertTrue(engine.startSession().succeeded)
        assertTrue(listener.startedLatch.await(1, TimeUnit.SECONDS))
        val restarted = engine.getStatus()
        assertEquals(2, runtimes.size)
        assertEquals(0L, restarted.inferenceCount)
        assertEquals(0, restarted.queueDepthFrames)
        assertEquals("SILENCE", restarted.state)
        engine.stopSession()

        assertEquals(1, runtimes[0].closeCount)
        assertEquals(1, runtimes[1].closeCount)
        assertTrue(runtimes.all { it.resetCount >= 2 })
    }

    @Test
    fun semanticEventAndStatusTypesContainNoPcmArrays() {
        val exposedTypes = listOf(
            SileroVadEngine.Event::class.java,
            SileroVadEngine.Status::class.java,
        )

        exposedTypes.flatMap { it.declaredFields.toList() }.forEach { field ->
            assertFalse("Unexpected array field ${field.name}", field.type.isArray)
        }
    }

    private fun newRunningEngine(
        runtime: ScriptedRuntime,
        listener: RecordingListener,
    ): SileroVadEngine = newEngine(
        runtimeFactory = SileroVadRuntimeFactory { runtime },
        listener = listener,
    ).also { engine ->
        assertTrue(engine.startSession().succeeded)
        assertTrue(listener.startedLatch.await(1, TimeUnit.SECONDS))
    }

    private fun newEngine(
        config: SileroVadConfig = CONFIG,
        modelAsset: SileroVadModelAsset = MODEL_PRESENT,
        runtimeAvailable: Boolean = true,
        runtimeFactory: SileroVadRuntimeFactory,
        listener: SileroVadEngine.Listener,
    ): SileroVadEngine = SileroVadEngine(
        config = config,
        frameDurationMs = FRAME_DURATION_MS,
        frameSizeSamples = FRAME_SAMPLES,
        modelAsset = modelAsset,
        runtimeName = "SCRIPTED_TEST_RUNTIME",
        runtimeVersion = "TEST_ONLY",
        runtimeAvailable = runtimeAvailable,
        runtimeFactory = runtimeFactory,
        listener = listener,
    )

    private fun offerFrames(engine: SileroVadEngine, count: Int) {
        repeat(count) {
            assertTrue(engine.offerPcmFrame(ShortArray(FRAME_SAMPLES), FRAME_SAMPLES))
        }
    }

    private fun offerMetadataFrames(
        engine: SileroVadEngine,
        firstSequence: Long,
        responseId: String,
    ) {
        repeat(2) { offset ->
            val sequence = firstSequence + offset
            assertTrue(
                engine.offerPcmFrame(
                    ShortArray(FRAME_SAMPLES) { (offset + 1).toShort() },
                    FRAME_SAMPLES,
                    sequence,
                    sequence * 20_000_000L,
                    (sequence + 1L) * 20_000_000L,
                    FarEndReferenceBuffer.Assessment.stopped(sequence).copy(
                        responseId = responseId,
                        referenceAvailable = true,
                        timestampConfidence = "AUDIO_TIMESTAMP",
                    ),
                ),
            )
        }
    }

    private fun waitUntil(condition: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2)
        while (!condition() && System.nanoTime() < deadline) {
            Thread.yield()
        }
        assertTrue(condition())
    }

    companion object {
        private const val FRAME_DURATION_MS = 20
        private const val FRAME_SAMPLES = 320
        private const val INFERENCE_SAMPLES = 512
        private const val QUEUE_CAPACITY = 8
        private val CONFIG = SileroVadConfig(queueCapacityFrames = QUEUE_CAPACITY)
        private val MODEL_PRESENT = SileroVadModelAsset(
            present = true,
            assetPath = CONFIG.modelAssetPath,
            sizeBytes = 1_024L,
            missingReason = null,
            sha256 = "TEST_ONLY",
            sha256Verified = true,
        )
        private val MODEL_MISSING = SileroVadModelAsset(
            present = false,
            assetPath = CONFIG.modelAssetPath,
            sizeBytes = 0L,
            missingReason = "test asset missing",
        )
    }
}
