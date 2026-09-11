package com.voiceaipoc.voice

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID

class TtsAudioFrameTest {
    @Test
    fun parsesStartAndEndFramesWithResponseIdentity() {
        val responseId = UUID.randomUUID()
        val payload = byteArrayOf(1, 2, 3, 4)
        val bytes = ByteBuffer.allocate(30 + payload.size).order(ByteOrder.BIG_ENDIAN)
            .put("VTT1".toByteArray(Charsets.US_ASCII))
            .put(1)
            .put(3)
            .putInt(24_000)
            .putInt(7)
            .putLong(responseId.mostSignificantBits)
            .putLong(responseId.leastSignificantBits)
            .put(payload)
            .array()

        val frame = TtsAudioFrame.parse(bytes)

        assertEquals(responseId, frame?.responseId)
        assertEquals(24_000, frame?.sampleRateHz)
        assertEquals(7L, frame?.sequence)
        assertTrue(frame?.startsResponse == true)
        assertTrue(frame?.endsResponse == true)
        assertArrayEquals(payload, frame?.payload)
    }

    @Test
    fun rejectsMalformedFrames() {
        assertNull(TtsAudioFrame.parse(byteArrayOf(1, 2, 3)))
        val bytes = ByteBuffer.allocate(30).order(ByteOrder.BIG_ENDIAN)
            .put("BAD1".toByteArray(Charsets.US_ASCII))
            .put(1)
            .put(1)
            .putInt(24_000)
            .putInt(0)
            .putLong(0)
            .putLong(0)
            .array()
        assertNull(TtsAudioFrame.parse(bytes))
    }

    @Test
    fun rejectsMicrophoneRateAndOddPcmPayload() {
        val responseId = UUID.randomUUID()
        val bytes = ByteBuffer.allocate(31).order(ByteOrder.BIG_ENDIAN)
            .put("VTT1".toByteArray(Charsets.US_ASCII))
            .put(1)
            .put(1)
            .putInt(16_000)
            .putInt(0)
            .putLong(responseId.mostSignificantBits)
            .putLong(responseId.leastSignificantBits)
            .put(1)
            .array()

        assertNull(TtsAudioFrame.parse(bytes))
    }
}
