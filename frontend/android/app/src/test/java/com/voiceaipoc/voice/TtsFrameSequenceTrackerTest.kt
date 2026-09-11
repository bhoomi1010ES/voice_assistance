package com.voiceaipoc.voice

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Test

class TtsFrameSequenceTrackerTest {
    @Test
    fun detectsInOrderGapDuplicateAndStaleFrames() {
        val responseId = UUID.randomUUID()
        val otherResponseId = UUID.randomUUID()
        val tracker = TtsFrameSequenceTracker()

        assertEquals(TtsFrameSequenceTracker.Result.START, tracker.observe(frame(responseId, 0, true)))
        assertEquals(TtsFrameSequenceTracker.Result.IN_ORDER, tracker.observe(frame(responseId, 1)))
        assertEquals(TtsFrameSequenceTracker.Result.GAP, tracker.observe(frame(responseId, 3)))
        assertEquals(
            TtsFrameSequenceTracker.Result.DUPLICATE_OR_OUT_OF_ORDER,
            tracker.observe(frame(responseId, 2)),
        )
        assertEquals(TtsFrameSequenceTracker.Result.STALE, tracker.observe(frame(otherResponseId, 4)))
    }

    private fun frame(responseId: UUID, sequence: Int, starts: Boolean = false): TtsAudioFrame {
        val bytes = ByteBuffer.allocate(32).order(ByteOrder.BIG_ENDIAN)
            .put("VTT1".toByteArray(Charsets.US_ASCII))
            .put(1)
            .put(if (starts) 1 else 0)
            .putInt(24_000)
            .putInt(sequence)
            .putLong(responseId.mostSignificantBits)
            .putLong(responseId.leastSignificantBits)
            .putShort(0)
            .array()
        return requireNotNull(TtsAudioFrame.parse(bytes))
    }
}
