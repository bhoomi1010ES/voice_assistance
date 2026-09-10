package com.voiceaipoc.voice

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.ArrayDeque

/**
 * Bounded native-only queue for encoded PCM frames.
 *
 * The AudioEngine callback supplies a reusable ShortArray. This class copies
 * it immediately and never retains the caller's buffer. Queue overflow drops
 * the oldest frame and records the event for metadata diagnostics.
 */
class PcmSendQueue(
    private val capacity: Int = DEFAULT_CAPACITY,
    private val frameSamples: Int = FRAME_SAMPLES,
) {
    data class Frame(
        val sequenceNo: Long,
        val clientTimestampMs: Long,
        val payload: ByteArray,
    )

    data class Snapshot(
        val depth: Int,
        val highWaterMark: Int,
        val droppedFrames: Long,
        val invalidFrames: Long,
    )

    private val lock = Any()
    private val frames = ArrayDeque<Frame>(capacity)
    private var nextSequenceNo = 0L
    private var highWaterMark = 0
    private var droppedFrames = 0L
    private var invalidFrames = 0L

    fun offer(buffer: ShortArray, samplesRead: Int, timestampMs: Long): Boolean = synchronized(lock) {
        if (samplesRead != frameSamples || buffer.size < samplesRead) {
            invalidFrames += 1
            return@synchronized false
        }

        val payload = ByteArray(samplesRead * BYTES_PER_SAMPLE)
        val pcm = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        for (index in 0 until samplesRead) {
            pcm.putShort(buffer[index])
        }

        if (frames.size >= capacity) {
            frames.removeFirst()
            droppedFrames += 1
        }
        frames.addLast(
            Frame(
                sequenceNo = nextSequenceNo++,
                clientTimestampMs = timestampMs,
                payload = payload,
            ),
        )
        highWaterMark = maxOf(highWaterMark, frames.size)
        true
    }

    fun poll(): Frame? = synchronized(lock) {
        if (frames.isEmpty()) null else frames.removeFirst()
    }

    fun clear() = synchronized(lock) {
        frames.clear()
    }

    fun resetSequence() = synchronized(lock) {
        check(frames.isEmpty()) { "Cannot reset PCM sequence while frames are queued" }
        nextSequenceNo = 0L
    }

    fun snapshot(): Snapshot = synchronized(lock) {
        Snapshot(
            depth = frames.size,
            highWaterMark = highWaterMark,
            droppedFrames = droppedFrames,
            invalidFrames = invalidFrames,
        )
    }

    companion object {
        const val DEFAULT_CAPACITY = 100
        const val FRAME_SAMPLES = 320
        const val BYTES_PER_SAMPLE = 2
    }
}
