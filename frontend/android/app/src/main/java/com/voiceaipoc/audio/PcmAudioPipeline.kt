package com.voiceaipoc.audio

import android.util.Log
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/**
 * Native PCM framing and bounded producer/consumer pipeline.
 *
 * AudioRecord's worker is the sole producer. A dedicated native consumer
 * drains complete frames and invokes the future-processing callback. Both the
 * ring storage and thread buffers are allocated once per AudioEngine instance.
 */
internal class PcmAudioPipeline(
    private val config: AudioConfig,
    private val frameCallback: AudioEngine.PcmDataCallback,
) {
    data class Status(
        val running: Boolean,
        val frameDurationMs: Int,
        val frameSizeSamples: Int,
        val frameSizeBytes: Int,
        val bufferCapacityFrames: Int,
        val bufferCapacityBytes: Int,
        val maxBufferedDurationMs: Int,
        val bufferedFrames: Int,
        val bufferedBytes: Int,
        val maxObservedBufferedFrames: Int,
        val totalPcmBytesProcessed: Long,
        val framesWrittenToRingBuffer: Long,
        val framesConsumedFromRingBuffer: Long,
        val overflowCount: Long,
        val invalidInputCount: Long,
        val processingErrorCount: Long,
        val partialFrameSamples: Int,
    )

    companion object {
        private const val CONSUMER_THREAD_NAME = "VoiceAI-PcmConsumer"
        private const val CONSUMER_WAIT_TIMEOUT_MS = 50L
        private const val STOP_JOIN_TIMEOUT_MS = 2_000L
        private const val OVERFLOW_LOG_INTERVAL = 100L
    }

    private val stateLock = Any()
    private val normalizer = AudioNormalizer(config.channelCount)
    private val ringBuffer = AudioRingBuffer(
        capacityFrames = config.ringBufferCapacityFrames,
        frameSizeSamples = config.frameSizeSamples,
        bytesPerSample = config.bytesPerSample,
    )
    private val partialFrame = ShortArray(config.frameSizeSamples)
    private val consumerFrame = ShortArray(config.frameSizeSamples)

    private val totalPcmBytesProcessed = AtomicLong(0L)
    private val framesWritten = AtomicLong(0L)
    private val framesConsumed = AtomicLong(0L)
    private val overflowCount = AtomicLong(0L)
    private val invalidInputCount = AtomicLong(0L)
    private val processingErrorCount = AtomicLong(0L)
    private val maxObservedBufferedFrames = AtomicInteger(0)

    @Volatile
    private var running = false

    @Volatile
    private var partialFrameSamples = 0
    private var consumerThread: Thread? = null

    fun start() {
        synchronized(stateLock) {
            if (consumerThread?.isAlive == false) {
                consumerThread = null
            }
            check(!running && consumerThread == null) { "PCM pipeline is already running" }
            resetForNewSessionLocked()
            running = true

            try {
                consumerThread = Thread(::consumeLoop, CONSUMER_THREAD_NAME).also { it.start() }
            } catch (exception: RuntimeException) {
                running = false
                consumerThread = null
                ringBuffer.clear()
                throw exception
            }
        }

        Log.i(
            AudioEngine.TAG,
            "PCM pipeline started: frameDurationMs=${config.frameDurationMs}, " +
                "frameSamples=${config.frameSizeSamples}, frameBytes=${config.frameSizeBytes}, " +
                "capacityFrames=${config.ringBufferCapacityFrames}, " +
                "maxBufferedDurationMs=${config.maxBufferedDurationMs}, " +
                "overflowPolicy=DROP_OLDEST",
        )
    }

    /** Called only by the AudioRecord capture worker with its reusable read buffer. */
    fun processSamples(pcmSamples: ShortArray, samplesRead: Int): Boolean {
        if (!running) {
            return false
        }
        if (!normalizer.validatePcm16InPlace(pcmSamples, samplesRead)) {
            invalidInputCount.incrementAndGet()
            Log.e(
                AudioEngine.TAG,
                "Invalid PCM input: samplesRead=$samplesRead, bufferSamples=${pcmSamples.size}, " +
                    "channels=${config.channelCount}",
            )
            return false
        }

        totalPcmBytesProcessed.addAndGet(samplesRead.toLong() * config.bytesPerSample)
        var sourceOffset = 0
        var remainingSamples = samplesRead

        if (partialFrameSamples > 0) {
            val copyCount = minOf(config.frameSizeSamples - partialFrameSamples, remainingSamples)
            System.arraycopy(pcmSamples, sourceOffset, partialFrame, partialFrameSamples, copyCount)
            partialFrameSamples += copyCount
            sourceOffset += copyCount
            remainingSamples -= copyCount

            if (partialFrameSamples == config.frameSizeSamples) {
                writeCompleteFrame(partialFrame, 0)
                partialFrameSamples = 0
            }
        }

        while (remainingSamples >= config.frameSizeSamples) {
            writeCompleteFrame(pcmSamples, sourceOffset)
            sourceOffset += config.frameSizeSamples
            remainingSamples -= config.frameSizeSamples
        }

        if (remainingSamples > 0) {
            System.arraycopy(pcmSamples, sourceOffset, partialFrame, 0, remainingSamples)
            partialFrameSamples = remainingSamples
        }

        return true
    }

    /**
     * Idempotently stops the consumer, invokes [afterConsumerStopped], then
     * clears all buffered/partial PCM. The hook lets downstream native stages
     * reset only after their final callback has completed.
     */
    fun stopAndClear(afterConsumerStopped: (() -> Unit)? = null) {
        val thread: Thread?
        val wasActive: Boolean
        synchronized(stateLock) {
            wasActive = running || consumerThread != null
            running = false
            thread = consumerThread
        }

        if (wasActive) {
            thread?.interrupt()
            if (thread != null && thread !== Thread.currentThread()) {
                try {
                    thread.join(STOP_JOIN_TIMEOUT_MS)
                } catch (interrupted: InterruptedException) {
                    Thread.currentThread().interrupt()
                    Log.w(AudioEngine.TAG, "Interrupted while stopping PCM consumer", interrupted)
                }
            }
        }

        val consumerStillAlive = thread?.isAlive == true
        if (consumerStillAlive) {
            processingErrorCount.incrementAndGet()
            Log.e(
                AudioEngine.TAG,
                "PCM consumer did not stop within ${STOP_JOIN_TIMEOUT_MS}ms; restart is blocked until it exits",
            )
        }

        try {
            afterConsumerStopped?.invoke()
        } catch (exception: RuntimeException) {
            processingErrorCount.incrementAndGet()
            Log.e(AudioEngine.TAG, "Native PCM downstream reset failed", exception)
        }

        synchronized(stateLock) {
            if (!consumerStillAlive && consumerThread === thread) {
                consumerThread = null
            }
            ringBuffer.clear()
            partialFrame.fill(0)
            partialFrameSamples = 0
        }

        if (!wasActive) {
            return
        }

        val status = getStatus()
        Log.i(
            AudioEngine.TAG,
            "PCM pipeline stopped: bytesProcessed=${status.totalPcmBytesProcessed}, " +
                "framesWritten=${status.framesWrittenToRingBuffer}, " +
                "framesConsumed=${status.framesConsumedFromRingBuffer}, " +
                "bufferedFrames=${status.bufferedFrames}, overflowCount=${status.overflowCount}, " +
                "invalidInputCount=${status.invalidInputCount}, " +
                "processingErrorCount=${status.processingErrorCount}",
        )
    }

    fun getStatus(): Status {
        return Status(
            running = running,
            frameDurationMs = config.frameDurationMs,
            frameSizeSamples = config.frameSizeSamples,
            frameSizeBytes = config.frameSizeBytes,
            bufferCapacityFrames = ringBuffer.capacityFrames,
            bufferCapacityBytes = ringBuffer.capacityBytes,
            maxBufferedDurationMs = config.maxBufferedDurationMs,
            bufferedFrames = ringBuffer.currentBufferedFrames(),
            bufferedBytes = ringBuffer.currentBufferedBytes(),
            maxObservedBufferedFrames = maxObservedBufferedFrames.get(),
            totalPcmBytesProcessed = totalPcmBytesProcessed.get(),
            framesWrittenToRingBuffer = framesWritten.get(),
            framesConsumedFromRingBuffer = framesConsumed.get(),
            overflowCount = overflowCount.get(),
            invalidInputCount = invalidInputCount.get(),
            processingErrorCount = processingErrorCount.get(),
            partialFrameSamples = partialFrameSamples,
        )
    }

    private fun consumeLoop() {
        try {
            while (running) {
                val samplesRead = try {
                    ringBuffer.read(
                        destination = consumerFrame,
                        waitTimeoutMs = CONSUMER_WAIT_TIMEOUT_MS,
                    )
                } catch (interrupted: InterruptedException) {
                    if (running) {
                        processingErrorCount.incrementAndGet()
                        Log.w(AudioEngine.TAG, "PCM consumer interrupted unexpectedly", interrupted)
                    }
                    break
                }

                if (!running || samplesRead == 0) {
                    continue
                }

                try {
                    // The callback runs on this native consumer thread and must
                    // not retain the reusable consumerFrame array.
                    frameCallback.onPcmData(consumerFrame, samplesRead)
                    framesConsumed.incrementAndGet()
                } catch (exception: RuntimeException) {
                    processingErrorCount.incrementAndGet()
                    Log.e(AudioEngine.TAG, "Native PCM consumer callback failed", exception)
                }
            }
        } finally {
            Log.i(AudioEngine.TAG, "PCM consumer thread exited")
        }
    }

    private fun writeCompleteFrame(source: ShortArray, sourceOffset: Int) {
        val result = ringBuffer.write(source, sourceOffset, config.frameSizeSamples)
        framesWritten.incrementAndGet()
        updateMaxObservedBufferedFrames(ringBuffer.currentBufferedFrames())

        if (result == AudioRingBuffer.WriteResult.WROTE_AFTER_DROPPING_OLDEST) {
            val overflows = overflowCount.incrementAndGet()
            if (overflows == 1L || overflows % OVERFLOW_LOG_INTERVAL == 0L) {
                Log.w(
                    AudioEngine.TAG,
                    "PCM ring buffer overflow: count=$overflows, policy=DROP_OLDEST, " +
                        "capacityFrames=${config.ringBufferCapacityFrames}",
                )
            }
        }
    }

    private fun updateMaxObservedBufferedFrames(candidate: Int) {
        var current = maxObservedBufferedFrames.get()
        while (candidate > current && !maxObservedBufferedFrames.compareAndSet(current, candidate)) {
            current = maxObservedBufferedFrames.get()
        }
    }

    private fun resetForNewSessionLocked() {
        ringBuffer.clear()
        partialFrame.fill(0)
        partialFrameSamples = 0
        consumerFrame.fill(0)
        totalPcmBytesProcessed.set(0L)
        framesWritten.set(0L)
        framesConsumed.set(0L)
        overflowCount.set(0L)
        invalidInputCount.set(0L)
        processingErrorCount.set(0L)
        maxObservedBufferedFrames.set(0)
    }
}
