package com.voiceaipoc.voice

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID

/** Small, bounded parser for server-to-device PCM frames. */
internal data class TtsAudioFrame(
    val flags: Int,
    val sampleRateHz: Int,
    val sequence: Long,
    val responseId: UUID,
    val payload: ByteArray,
) {
    val startsResponse: Boolean
        get() = flags and START_FLAG != 0

    val endsResponse: Boolean
        get() = flags and END_FLAG != 0

    companion object {
        private const val HEADER_BYTES = 30
        private const val MAGIC = "VTT1"
        private const val VERSION = 1
        private const val START_FLAG = 1
        private const val END_FLAG = 2
        private const val ALLOWED_FLAGS = START_FLAG or END_FLAG

        fun parse(bytes: ByteArray): TtsAudioFrame? {
            if (bytes.size < HEADER_BYTES) return null
            if (bytes.copyOfRange(0, 4).toString(Charsets.US_ASCII) != MAGIC) return null

            val buffer = ByteBuffer.wrap(bytes).order(ByteOrder.BIG_ENDIAN)
            buffer.position(4)
            if (buffer.get().toInt() != VERSION) return null
            val flags = buffer.get().toInt() and 0xff
            if (flags and ALLOWED_FLAGS.inv() != 0) return null
            val sampleRateHz = buffer.int
            if (sampleRateHz != TTS_SAMPLE_RATE_HZ) return null
            val sequence = buffer.int.toLong() and 0xffffffffL
            val responseId = UUID(buffer.long, buffer.long)
            val payload = bytes.copyOfRange(HEADER_BYTES, bytes.size)
            if (payload.size % BYTES_PER_SAMPLE != 0) return null
            return TtsAudioFrame(
                flags = flags,
                sampleRateHz = sampleRateHz,
                sequence = sequence,
                responseId = responseId,
                payload = payload,
            )
        }

        const val TTS_SAMPLE_RATE_HZ = 24_000
        private const val BYTES_PER_SAMPLE = 2
    }
}
