package com.voiceaipoc.voice

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Matches backend/app/websocket/binary.py without serializing PCM as JSON. */
object VoiceBinaryFrame {
    private val MAGIC = byteArrayOf('V'.code.toByte(), 'A'.code.toByte(), 'I'.code.toByte(), '1'.code.toByte())
    private const val VERSION: Byte = 1
    private const val CLIENT_PCM_FRAME_TYPE: Byte = 1
    private const val HEADER_BYTES = 24

    fun encode(frame: PcmSendQueue.Frame): ByteArray {
        val encoded = ByteArray(HEADER_BYTES + frame.payload.size)
        val header = ByteBuffer.wrap(encoded).order(ByteOrder.BIG_ENDIAN)
        header.put(MAGIC)
        header.put(VERSION)
        header.put(CLIENT_PCM_FRAME_TYPE)
        header.putShort(0)
        // Keep this layout byte-for-byte aligned with backend's !4sBBHIQI:
        // flags (2), sequence (4), timestamp (8), payload length (4).
        header.putInt(frame.sequenceNo.toInt())
        header.putLong(frame.clientTimestampMs)
        header.putInt(frame.payload.size)
        frame.payload.copyInto(encoded, HEADER_BYTES)
        return encoded
    }
}
