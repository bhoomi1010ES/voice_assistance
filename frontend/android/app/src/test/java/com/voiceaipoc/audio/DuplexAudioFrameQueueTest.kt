package com.voiceaipoc.audio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DuplexAudioFrameQueueTest {
    @Test
    fun overflowDropsOldestPcmAndItsMetadataAsOneUnit() {
        val queue = DuplexAudioFrameQueue(capacityFrames = 2, frameSizeSamples = 4)

        queue.offer(frame(1), 4, 1L, 1_000L, 2_000L, assessment("old"))
        queue.offer(frame(2), 4, 2L, 2_000L, 3_000L, assessment("middle"))
        assertEquals(
            DuplexAudioFrameQueue.WriteResult.WROTE_AFTER_DROPPING_OLDEST,
            queue.offer(frame(3), 4, 3L, 3_000L, 4_000L, assessment("new")),
        )

        val pcm = ShortArray(4)
        val metadata = DuplexAudioFrameQueue.FrameMetadata()
        queue.read(pcm, metadata)
        assertEquals(2, pcm[0].toInt())
        assertEquals(2L, metadata.frameSequence)
        assertEquals("middle", metadata.playbackResponseId)
        assertEquals(2_000L, metadata.captureStartNs)

        queue.read(pcm, metadata)
        assertEquals(3, pcm[0].toInt())
        assertEquals(3L, metadata.frameSequence)
        assertEquals("new", metadata.playbackResponseId)
        assertEquals(3_000L, metadata.captureStartNs)
    }

    @Test
    fun clearRemovesPcmAndMetadataBeforeTheNextSession() {
        val queue = DuplexAudioFrameQueue(capacityFrames = 1, frameSizeSamples = 2)
        queue.offer(frame(9), 2, 17L, 10L, 20L, assessment("stale"))
        queue.clear()
        assertEquals(0, queue.currentBufferedFrames())

        queue.offer(frame(4), 2, 0L, 30L, 40L, assessment("fresh"))
        val pcm = ShortArray(2)
        val metadata = DuplexAudioFrameQueue.FrameMetadata()
        assertTrue(queue.read(pcm, metadata) > 0)
        assertEquals(4, pcm[0].toInt())
        assertEquals(0L, metadata.frameSequence)
        assertEquals("fresh", metadata.playbackResponseId)
    }

    private fun assessment(responseId: String): FarEndReferenceBuffer.Assessment =
        FarEndReferenceBuffer.Assessment.stopped(20L).copy(
            responseId = responseId,
            referenceAvailable = true,
            timestampConfidence = "AUDIO_TIMESTAMP",
        )

    private fun frame(value: Int): ShortArray = ShortArray(4) { value.toShort() }
}
