package com.voiceaipoc.wakeword

import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeWordEngineTest {
    private class RecordingListener(
        startedCount: Int = 1,
        errorCount: Int = 1,
    ) : WakeWordEngine.Listener {
        val startedLatch = CountDownLatch(startedCount)
        val stoppedLatch = CountDownLatch(1)
        val errorLatch = CountDownLatch(errorCount)
        val detections = Collections.synchronizedList(
            mutableListOf<WakeWordEngine.DetectionEvent>(),
        )

        override fun onEngineStarted(status: WakeWordEngine.Status) {
            startedLatch.countDown()
        }

        override fun onEngineStopped(status: WakeWordEngine.Status) {
            stoppedLatch.countDown()
        }

        override fun onWakeWordDetected(event: WakeWordEngine.DetectionEvent) {
            detections += event
        }

        override fun onEngineError(status: WakeWordEngine.Status) {
            errorLatch.countDown()
        }
    }

    private class FakeRuntime(
        private val confidence: Float = 0.1f,
        private val initializationFailure: RuntimeException? = null,
        private val inferenceFailure: WakeWordRuntimeException? = null,
    ) : WakeWordInferenceRuntime {
        override val runtimeName = "FAKE_TEST_RUNTIME"
        val predictLatch = CountDownLatch(1)
        val closeLatch = CountDownLatch(1)
        val inputs = Collections.synchronizedList(mutableListOf<ShortArray>())
        var lastInput: ShortArray? = null
        var initializeCount = 0
        var closeCount = 0

        override fun initialize() {
            initializeCount += 1
            initializationFailure?.let { throw it }
        }

        override fun predict(pcm16: ShortArray, samplesRead: Int): Float {
            lastInput = pcm16.copyOf(samplesRead)
            inputs += requireNotNull(lastInput)
            predictLatch.countDown()
            inferenceFailure?.let { throw it }
            return confidence
        }

        override fun close() {
            closeCount += 1
            closeLatch.countDown()
        }
    }

    private val config = WakeWordConfig(modelName = "test_wake_word")

    @Test
    fun missingApprovedModelReportsUnavailableWithoutStartingWorker() {
        val listener = RecordingListener()
        val engine = newEngine(
            modelAssets = MODEL_MISSING,
            runtimeAvailable = false,
            runtimeFactory = WakeWordRuntimeFactory { FakeRuntime() },
            listener = listener,
        )

        val result = engine.startSession()

        assertFalse(result.succeeded)
        assertEquals(WakeWordEngine.ERROR_MODEL_MISSING, result.errorCode)
        assertTrue(listener.errorLatch.await(1, TimeUnit.SECONDS))
        assertEquals("ERROR", engine.getStatus().state)
        assertFalse(engine.getStatus().workerThreadAlive)
        assertFalse(engine.getStatus().modelPresent)
        engine.stopSession()
    }

    @Test
    fun malformedPcmFrameIsRejectedAndObservable() {
        val runtime = FakeRuntime()
        val listener = RecordingListener()
        val engine = newRunningEngine(runtime, listener)

        assertFalse(engine.offerPcmFrame(ShortArray(FRAME_SAMPLES - 1), FRAME_SAMPLES - 1))

        val status = engine.getStatus()
        assertEquals(1L, status.malformedFrameCount)
        assertEquals(WakeWordEngine.ERROR_MALFORMED_PCM, status.lastErrorCode)
        assertTrue(listener.errorLatch.await(1, TimeUnit.SECONDS))
        engine.stopSession()
    }

    @Test
    fun fourTwentyMillisecondFramesBecomeOneOrderedEightyMillisecondInference() {
        val runtime = FakeRuntime()
        val listener = RecordingListener()
        val engine = newRunningEngine(runtime, listener)

        repeat(4) { frameIndex ->
            val frame = ShortArray(FRAME_SAMPLES) { (frameIndex + 1).toShort() }
            assertTrue(engine.offerPcmFrame(frame, frame.size))
        }

        assertTrue(runtime.predictLatch.await(1, TimeUnit.SECONDS))
        val input = runtime.lastInput
        assertNotNull(input)
        assertEquals(INFERENCE_SAMPLES, input?.size)
        assertEquals(1, input?.get(0)?.toInt())
        assertEquals(2, input?.get(FRAME_SAMPLES)?.toInt())
        assertEquals(3, input?.get(FRAME_SAMPLES * 2)?.toInt())
        assertEquals(4, input?.get(FRAME_SAMPLES * 3)?.toInt())
        assertEquals(1L, engine.getStatus().inferenceCount)
        engine.stopSession()
    }

    @Test
    fun consecutiveInferenceWindowsAdvanceExactly1280SamplesWithoutOverlapOrGap() {
        val runtime = FakeRuntime()
        val listener = RecordingListener()
        val engine = newRunningEngine(runtime, listener)
        assertFalse(engine.getStatus().acousticDiagnostics.enabled)
        assertTrue(engine.setAcousticCalibrationEnabled(true).succeeded)

        repeat(8) { frameIndex ->
            val frame = ShortArray(FRAME_SAMPLES) { sampleIndex ->
                (frameIndex * FRAME_SAMPLES + sampleIndex).toShort()
            }
            assertTrue(engine.offerPcmFrame(frame, frame.size))
        }

        waitUntil { runtime.inputs.size == 2 }
        assertArrayEquals(
            ShortArray(INFERENCE_SAMPLES) { it.toShort() },
            runtime.inputs[0],
        )
        assertArrayEquals(
            ShortArray(INFERENCE_SAMPLES) { (INFERENCE_SAMPLES + it).toShort() },
            runtime.inputs[1],
        )
        val status = engine.getStatus()
        assertEquals(2L, status.inferenceCount)
        assertEquals(2L, status.acousticDiagnostics.inferenceWindowCount)
        assertEquals(8L, status.framesConsumed)
        engine.stopSession()
    }

    @Test
    fun runtimeDetectionEmitsSemanticEventWithoutPcm() {
        val runtime = FakeRuntime(confidence = 0.9f)
        val listener = RecordingListener()
        val engine = newRunningEngine(runtime, listener)
        repeat(4) { engine.offerPcmFrame(ShortArray(FRAME_SAMPLES), FRAME_SAMPLES) }

        assertTrue(runtime.predictLatch.await(1, TimeUnit.SECONDS))
        waitUntil { listener.detections.size == 1 }

        assertEquals(1, listener.detections.size)
        assertEquals("test_wake_word", listener.detections.single().modelName)
        assertEquals(0.9f, listener.detections.single().confidence)
        engine.stopSession()
    }

    @Test
    fun inferenceFailureStopsWorkerAndReportsError() {
        val runtime = FakeRuntime(
            inferenceFailure = WakeWordRuntimeException(
                WakeWordEngine.ERROR_INFERENCE,
                "synthetic test failure",
            ),
        )
        val listener = RecordingListener()
        val engine = newRunningEngine(runtime, listener)
        repeat(4) { engine.offerPcmFrame(ShortArray(FRAME_SAMPLES), FRAME_SAMPLES) }

        assertTrue(listener.errorLatch.await(1, TimeUnit.SECONDS))
        waitUntil { !engine.getStatus().workerThreadAlive }

        assertEquals("ERROR", engine.getStatus().state)
        assertEquals(WakeWordEngine.ERROR_INFERENCE, engine.getStatus().lastErrorCode)
        assertTrue(runtime.closeLatch.await(1, TimeUnit.SECONDS))
        engine.stopSession()
    }

    @Test
    fun initializationFailureIsReportedAndRuntimeIsClosed() {
        val runtime = FakeRuntime(
            initializationFailure = IllegalStateException("synthetic invalid model"),
        )
        val listener = RecordingListener()
        val engine = newEngine(
            runtimeFactory = WakeWordRuntimeFactory { runtime },
            listener = listener,
        )

        assertFalse(engine.startSession().succeeded)
        assertTrue(listener.errorLatch.await(1, TimeUnit.SECONDS))
        waitUntil { !engine.getStatus().workerThreadAlive }

        assertEquals(
            WakeWordEngine.ERROR_RUNTIME_INITIALIZATION,
            engine.getStatus().lastErrorCode,
        )
        assertEquals("ERROR", engine.getStatus().state)
        assertTrue(runtime.closeLatch.await(1, TimeUnit.SECONDS))
        engine.stopSession()
    }

    @Test
    fun workerFactoryFailureIsReportedWithoutCrashing() {
        val listener = RecordingListener()
        val engine = newEngine(
            runtimeFactory = WakeWordRuntimeFactory {
                throw IllegalStateException("synthetic worker failure")
            },
            listener = listener,
        )

        assertFalse(engine.startSession().succeeded)
        assertTrue(listener.errorLatch.await(1, TimeUnit.SECONDS))
        waitUntil { !engine.getStatus().workerThreadAlive }

        assertEquals(WakeWordEngine.ERROR_WORKER_FAILURE, engine.getStatus().lastErrorCode)
        assertEquals("ERROR", engine.getStatus().state)
        engine.stopSession()
    }

    @Test
    fun stopJoinsWorkerClosesRuntimeAndClearsQueue() {
        val runtime = FakeRuntime()
        val listener = RecordingListener()
        val engine = newRunningEngine(runtime, listener)
        engine.offerPcmFrame(ShortArray(FRAME_SAMPLES), FRAME_SAMPLES)

        engine.stopSession()

        assertTrue(runtime.closeLatch.await(1, TimeUnit.SECONDS))
        assertTrue(listener.stoppedLatch.await(1, TimeUnit.SECONDS))
        assertEquals(1, runtime.closeCount)
        assertFalse(engine.getStatus().workerThreadAlive)
        assertFalse(engine.getStatus().running)
        assertEquals(0, engine.getStatus().queuedFrames)
        assertEquals("STOPPED", engine.getStatus().state)
    }

    @Test
    fun duplicateStartDoesNotCreateSecondWorker() {
        val createdCount = AtomicInteger(0)
        val runtime = FakeRuntime()
        val listener = RecordingListener()
        val engine = newEngine(
            runtimeFactory = WakeWordRuntimeFactory {
                createdCount.incrementAndGet()
                runtime
            },
            listener = listener,
        )
        assertTrue(engine.startSession().succeeded)
        assertTrue(listener.startedLatch.await(1, TimeUnit.SECONDS))

        val duplicate = engine.startSession()

        assertFalse(duplicate.succeeded)
        assertEquals(WakeWordEngine.ERROR_ALREADY_RUNNING, duplicate.errorCode)
        assertEquals(1, createdCount.get())
        engine.stopSession()
    }

    @Test
    fun restartCreatesFreshWorkerAndResetsSessionCounters() {
        val runtimes = Collections.synchronizedList(mutableListOf<FakeRuntime>())
        val listener = RecordingListener(startedCount = 2)
        val engine = newEngine(
            runtimeFactory = WakeWordRuntimeFactory {
                FakeRuntime().also { runtimes += it }
            },
            listener = listener,
        )

        assertTrue(engine.startSession().succeeded)
        waitUntil { runtimes.size == 1 && engine.getStatus().runtimeInitialized }
        engine.offerPcmFrame(ShortArray(FRAME_SAMPLES), FRAME_SAMPLES)
        engine.stopSession()

        assertTrue(engine.startSession().succeeded)
        assertTrue(listener.startedLatch.await(1, TimeUnit.SECONDS))

        val restarted = engine.getStatus()
        assertEquals(2, runtimes.size)
        assertEquals(0L, restarted.framesOffered)
        assertEquals(0L, restarted.inferenceCount)
        assertEquals(0L, restarted.detectionCount)
        engine.stopSession()
        assertEquals(1, runtimes[0].closeCount)
        assertEquals(1, runtimes[1].closeCount)
    }

    private fun newRunningEngine(
        runtime: FakeRuntime,
        listener: RecordingListener,
    ): WakeWordEngine {
        val engine = newEngine(
            runtimeFactory = WakeWordRuntimeFactory { runtime },
            listener = listener,
        )
        assertTrue(engine.startSession().succeeded)
        assertTrue(listener.startedLatch.await(1, TimeUnit.SECONDS))
        return engine
    }

    private fun newEngine(
        modelAssets: WakeWordModelAssets = MODEL_PRESENT,
        runtimeAvailable: Boolean = true,
        runtimeFactory: WakeWordRuntimeFactory,
        listener: WakeWordEngine.Listener,
    ): WakeWordEngine = WakeWordEngine(
        config = config,
        frameDurationMs = FRAME_DURATION_MS,
        frameSizeSamples = FRAME_SAMPLES,
        modelAssets = modelAssets,
        runtimeName = "FAKE_TEST_RUNTIME",
        runtimeAvailable = runtimeAvailable,
        runtimeFactory = runtimeFactory,
        listener = listener,
        monotonicClockMs = { 0L },
        wallClockMs = { 1_000L },
    )

    private fun waitUntil(condition: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(1)
        while (!condition() && System.nanoTime() < deadline) {
            Thread.yield()
        }
        assertTrue(condition())
    }

    companion object {
        private const val FRAME_DURATION_MS = 20
        private const val FRAME_SAMPLES = 320
        private const val INFERENCE_SAMPLES = 1_280
        private val REQUIRED_ASSETS = listOf(
            "openwakeword/melspectrogram.tflite",
            "openwakeword/embedding_model.tflite",
            "openwakeword/wakeword.tflite",
        )
        private val MODEL_PRESENT = WakeWordModelAssets(
            present = true,
            requiredAssetPaths = REQUIRED_ASSETS,
            missingAssetPaths = emptyList(),
            hashVerified = true,
        )
        private val MODEL_MISSING = WakeWordModelAssets(
            present = false,
            requiredAssetPaths = REQUIRED_ASSETS,
            missingAssetPaths = REQUIRED_ASSETS,
        )
    }
}
