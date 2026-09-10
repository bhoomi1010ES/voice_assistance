package com.voiceaipoc.voice

import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PcmSendQueueTest {
    @Test
    fun binaryFrameUsesBackendHeaderAndLittleEndianPayload() {
        val frame = PcmSendQueue.Frame(
            sequenceNo = 7,
            clientTimestampMs = 1234,
            payload = ByteArray(640) { 0 },
        )

        val encoded = VoiceBinaryFrame.encode(frame)
        assertEquals(664, encoded.size)
        assertEquals('V'.code.toByte(), encoded[0])
        assertEquals('A'.code.toByte(), encoded[1])
        assertEquals('I'.code.toByte(), encoded[2])
        assertEquals('1'.code.toByte(), encoded[3])
        val header = ByteBuffer.wrap(encoded).order(ByteOrder.BIG_ENDIAN)
        header.position(8)
        assertEquals(7, header.int)
        assertEquals(1234L, header.long)
        assertEquals(640, header.int)
        assertEquals(1, encoded[4].toInt())
        assertEquals(1, encoded[5].toInt())
    }

    @Test
    fun offerCopiesReusablePcmBufferAndAssignsSequence() {
        val queue = PcmSendQueue(capacity = 2)
        val buffer = ShortArray(PcmSendQueue.FRAME_SAMPLES) { 1200 }

        assertTrue(queue.offer(buffer, buffer.size, 10))
        buffer[0] = -1200

        val frame = queue.poll()
        requireNotNull(frame)
        assertEquals(0L, frame.sequenceNo)
        assertEquals(10L, frame.clientTimestampMs)
        assertEquals(640, frame.payload.size)
        assertEquals(1200, ByteBuffer.wrap(frame.payload).order(ByteOrder.LITTLE_ENDIAN).short.toInt())
    }

    @Test
    fun overflowDropsOldestFrameAndExposesMetadata() {
        val queue = PcmSendQueue(capacity = 2)
        repeat(3) { index ->
            assertTrue(
                queue.offer(
                    ShortArray(PcmSendQueue.FRAME_SAMPLES) { index.toShort() },
                    PcmSendQueue.FRAME_SAMPLES,
                    index.toLong(),
                ),
            )
        }

        val snapshot = queue.snapshot()
        assertEquals(2, snapshot.depth)
        assertEquals(2, snapshot.highWaterMark)
        assertEquals(1L, snapshot.droppedFrames)
        assertEquals(1L, queue.poll()?.sequenceNo)
    }

    @Test
    fun sequenceCanBeResetBetweenExplicitTurns() {
        val queue = PcmSendQueue(capacity = 1)
        assertTrue(queue.offer(ShortArray(PcmSendQueue.FRAME_SAMPLES), 320, 1))
        assertEquals(0L, queue.poll()?.sequenceNo)
        queue.resetSequence()
        assertTrue(queue.offer(ShortArray(PcmSendQueue.FRAME_SAMPLES), 320, 2))
        assertEquals(0L, queue.poll()?.sequenceNo)
    }
}
