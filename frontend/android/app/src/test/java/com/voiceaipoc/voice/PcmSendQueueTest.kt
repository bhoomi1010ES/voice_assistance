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
        queue.prepareTurn(1)
        queue.bindTurn("turn-1", 1)

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
        queue.prepareTurn(1)
        queue.bindTurn("turn-1", 1)
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
        queue.prepareTurn(1)
        queue.bindTurn("turn-1", 1)
        assertTrue(queue.offer(ShortArray(PcmSendQueue.FRAME_SAMPLES), 320, 1))
        assertEquals(0L, queue.poll()?.sequenceNo)
        queue.prepareTurn(2)
        queue.bindTurn("turn-2", 2)
        assertTrue(queue.offer(ShortArray(PcmSendQueue.FRAME_SAMPLES), 320, 2))
        assertEquals(0L, queue.poll()?.sequenceNo)
    }

    @Test
    fun preservesTheLatest160MsForAReplacementTurn() {
        val queue = PcmSendQueue()
        repeat(PcmSendQueue.PRE_ROLL_CAPACITY + 2) { index ->
            assertTrue(
                queue.rememberForPreRoll(
                    ShortArray(PcmSendQueue.FRAME_SAMPLES) { index.toShort() },
                    PcmSendQueue.FRAME_SAMPLES,
                    index.toLong(),
                ),
            )
        }

        val preRoll = queue.takePreRoll()

        assertEquals(PcmSendQueue.PRE_ROLL_CAPACITY, preRoll.size)
        assertEquals(2L, preRoll.first().clientTimestampMs)
        assertEquals(9L, preRoll.last().clientTimestampMs)
        assertEquals(
            2,
            ByteBuffer.wrap(preRoll.first().payload).order(ByteOrder.LITTLE_ENDIAN).short.toInt(),
        )
        assertEquals(
            9,
            ByteBuffer.wrap(preRoll.last().payload).order(ByteOrder.LITTLE_ENDIAN).short.toInt(),
        )
        assertEquals(0, queue.takePreRoll().size)
    }

    @Test
    fun preRollFramesBecomeAZeroBasedNewTurnSequence() {
        val queue = PcmSendQueue()
        queue.rememberForPreRoll(
            ShortArray(PcmSendQueue.FRAME_SAMPLES) { 100 },
            PcmSendQueue.FRAME_SAMPLES,
            1,
        )
        val preRoll = queue.takePreRoll()
        queue.prepareTurn(2, preRoll)
        queue.bindTurn("turn-2", 2)

        val frame = queue.poll()

        requireNotNull(frame)
        assertEquals(0L, frame.sequenceNo)
        assertEquals(1L, frame.clientTimestampMs)
    }

    @Test
    fun pendingFramesCannotBePolledUntilBoundToAFreshTurn() {
        val queue = PcmSendQueue()
        queue.prepareTurn(7)
        val buffer = ShortArray(PcmSendQueue.FRAME_SAMPLES) { 42 }

        assertTrue(queue.offerPending(buffer, buffer.size, 10, 7))
        assertTrue(queue.offerPending(buffer, buffer.size, 30, 7))
        assertEquals(0, queue.snapshot().depth)
        assertEquals(2, queue.pendingSnapshot().depth)

        val binding = queue.bindTurn("new-turn", 7)
        requireNotNull(binding)
        assertEquals(2, binding.frames)
        assertEquals(0, queue.pendingSnapshot().depth)
        assertEquals("new-turn", queue.poll()?.ownerTurnId)
        assertEquals("new-turn", queue.poll()?.ownerTurnId)
    }

    @Test
    fun staleGenerationCannotBeSentIntoTheNewTurn() {
        val queue = PcmSendQueue()
        queue.prepareTurn(1)
        queue.bindTurn("old-turn", 1)
        queue.prepareTurn(2)
        queue.bindTurn("new-turn", 2)

        assertTrue(
            !queue.offerForTurn(
                ShortArray(PcmSendQueue.FRAME_SAMPLES),
                PcmSendQueue.FRAME_SAMPLES,
                1,
                1,
            ),
        )
        assertEquals(0, queue.snapshot().depth)
    }

    @Test
    fun delayedTurnReadyKeepsAllPendingFramesLocal() {
        val delaysMs = listOf(100, 500, 1500, 3000)
        delaysMs.forEachIndexed { index, _delayMs ->
            val queue = PcmSendQueue()
            queue.prepareTurn(index.toLong() + 1L)
            val buffer = ShortArray(PcmSendQueue.FRAME_SAMPLES)
            repeat(150) { frame ->
                assertTrue(
                    queue.offerPending(
                        ShortArray(PcmSendQueue.FRAME_SAMPLES) { frame.toShort() },
                        PcmSendQueue.FRAME_SAMPLES,
                        frame * 20L,
                        index.toLong() + 1L,
                    ),
                )
            }
            assertEquals(0, queue.snapshot().depth)
            assertEquals(150, queue.pendingSnapshot().depth)
            val binding = queue.bindTurn("turn-$index", index.toLong() + 1L)
            requireNotNull(binding)
            assertEquals(150, binding.frames)
            repeat(150) {
                assertEquals("turn-$index", queue.poll()?.ownerTurnId)
            }
            assertTrue(
                queue.offerForTurn(
                    buffer = buffer,
                    samplesRead = PcmSendQueue.FRAME_SAMPLES,
                    timestampMs = 3_000L,
                    expectedGeneration = index.toLong() + 1L,
                ),
            )
            assertEquals("turn-$index", queue.poll()?.ownerTurnId)
        }
    }

    @Test
    fun pendingOverflowIsLocalAndCannotLeakUnownedFramesToNetworkQueue() {
        val queue = PcmSendQueue()
        queue.prepareTurn(11)
        val buffer = ShortArray(PcmSendQueue.FRAME_SAMPLES)

        repeat(PcmSendQueue.PENDING_TURN_CAPACITY) { frame ->
            assertTrue(
                queue.offerPending(
                    buffer,
                    buffer.size,
                    frame * 20L,
                    11,
                ),
            )
        }
        assertTrue(!queue.offerPending(buffer, buffer.size, 5_000L, 11))
        assertEquals(0, queue.snapshot().depth)
        assertEquals(PcmSendQueue.PENDING_TURN_CAPACITY, queue.pendingSnapshot().depth)
        assertTrue(queue.pendingSnapshot().overflowed)
    }

    @Test
    fun speechCanEndBeforeReadyAndCommitBufferRemainsFlushable() {
        val queue = PcmSendQueue()
        queue.prepareTurn(1)
        repeat(30) { frame ->
            assertTrue(
                queue.offerPending(
                    ShortArray(PcmSendQueue.FRAME_SAMPLES),
                    PcmSendQueue.FRAME_SAMPLES,
                    frame * 20L,
                    1,
                ),
            )
        }
        assertEquals(0, queue.snapshot().depth)
        val binding = queue.bindTurn("turn-1", 1)
        requireNotNull(binding)
        assertEquals(30, binding.frames)
        assertEquals(0L, queue.poll()?.sequenceNo)
        assertEquals(29L, generateSequence { queue.poll() }.count().toLong())
    }
}
