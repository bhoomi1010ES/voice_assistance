package com.voiceaipoc.audio

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioPipelineTest {
    @Test
    fun configProducesTwentyMillisecondPcm16Frames() {
        val config = AudioConfig()

        assertEquals(20, config.frameDurationMs)
        assertEquals(320, config.frameSizeSamples)
        assertEquals(640, config.frameSizeBytes)
        assertEquals(25, config.ringBufferCapacityFrames)
        assertEquals(500, config.maxBufferedDurationMs)
    }

    @Test
    fun ringBufferPreservesWriteReadOrdering() {
        val ring = AudioRingBuffer(capacityFrames = 3, frameSizeSamples = 4)
        val first = shortArrayOf(1, 2, 3, 4)
        val second = shortArrayOf(5, 6, 7, 8)
        val output = ShortArray(4)

        assertEquals(AudioRingBuffer.WriteResult.WRITTEN, ring.write(first))
        assertEquals(AudioRingBuffer.WriteResult.WRITTEN, ring.write(second))
        assertEquals(4, ring.read(output))
        assertArrayEquals(first, output)
        assertEquals(4, ring.read(output))
        assertArrayEquals(second, output)
        assertEquals(0, ring.currentBufferedFrames())
    }

    @Test
    fun ringBufferIsBoundedAndDropsOldestOnOverflow() {
        val ring = AudioRingBuffer(capacityFrames = 2, frameSizeSamples = 2)
        val output = ShortArray(2)

        ring.write(shortArrayOf(1, 1))
        ring.write(shortArrayOf(2, 2))
        assertEquals(
            AudioRingBuffer.WriteResult.WROTE_AFTER_DROPPING_OLDEST,
            ring.write(shortArrayOf(3, 3)),
        )

        assertEquals(2, ring.capacityFrames)
        assertEquals(2, ring.currentBufferedFrames())
        ring.read(output)
        assertArrayEquals(shortArrayOf(2, 2), output)
        ring.read(output)
        assertArrayEquals(shortArrayOf(3, 3), output)
    }

    @Test
    fun ringBufferClearRemovesAllBufferedPcm() {
        val ring = AudioRingBuffer(capacityFrames = 2, frameSizeSamples = 3)
        ring.write(shortArrayOf(7, 8, 9))

        ring.clear()

        assertEquals(0, ring.currentBufferedFrames())
        assertEquals(0, ring.currentBufferedBytes())
        assertEquals(0, ring.read(ShortArray(3)))
    }

    @Test
    fun normalizerValidatesWithoutChangingSignedPcm16() {
        val normalizer = AudioNormalizer(channelCount = 1)
        val samples = shortArrayOf(Short.MIN_VALUE, -1, 0, 1, Short.MAX_VALUE)
        val original = samples.copyOf()

        assertTrue(normalizer.validatePcm16InPlace(samples, samples.size))
        assertArrayEquals(original, samples)
        assertFalse(normalizer.validatePcm16InPlace(samples, 0))
        assertFalse(normalizer.validatePcm16InPlace(samples, samples.size + 1))
    }

    @Test
    fun pipelineClearsBetweenMultipleStartStopCycles() {
        val config = AudioConfig()
        val callbackCount = AtomicInteger(0)
        var callbackLatch = CountDownLatch(1)
        val pipeline = PcmAudioPipeline(
            config,
            AudioEngine.PcmDataCallback { _, samplesRead ->
                assertEquals(config.frameSizeSamples, samplesRead)
                callbackCount.incrementAndGet()
                callbackLatch.countDown()
            },
        )
        val frame = ShortArray(config.frameSizeSamples) { it.toShort() }

        pipeline.start()
        assertTrue(pipeline.processSamples(frame, frame.size))
        assertTrue(callbackLatch.await(1, TimeUnit.SECONDS))
        pipeline.stopAndClear()
        assertFalse(pipeline.getStatus().running)
        assertEquals(0, pipeline.getStatus().bufferedFrames)
        assertEquals(0, pipeline.getStatus().partialFrameSamples)

        callbackLatch = CountDownLatch(1)
        pipeline.start()
        assertTrue(pipeline.processSamples(frame, frame.size))
        assertTrue(callbackLatch.await(1, TimeUnit.SECONDS))
        pipeline.stopAndClear()

        assertEquals(2, callbackCount.get())
        assertFalse(pipeline.getStatus().running)
        assertEquals(0, pipeline.getStatus().bufferedFrames)
    }
}
