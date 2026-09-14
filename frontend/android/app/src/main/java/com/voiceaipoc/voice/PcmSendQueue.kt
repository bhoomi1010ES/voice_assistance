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
        val ownerTurnId: String? = null,
        val generation: Long = 0L,
    )

    data class Snapshot(
        val depth: Int,
        val highWaterMark: Int,
        val droppedFrames: Long,
        val invalidFrames: Long,
    )

    data class PendingSnapshot(
        val depth: Int,
        val highWaterMark: Int,
        val droppedFrames: Long,
        val overflowed: Boolean,
    )

    data class Binding(
        val frames: Int,
        val bytes: Long,
    )

    data class PreRollFrame(
        val clientTimestampMs: Long,
        val payload: ByteArray,
    )

    private val lock = Any()
    private val frames = ArrayDeque<Frame>(capacity)
    private val pendingCapacity: Int = maxOf(capacity, PENDING_TURN_CAPACITY)
    private val pendingFrames = ArrayDeque<PendingFrame>(pendingCapacity)
    private val preRollFrames = ArrayDeque<PreRollFrame>(PRE_ROLL_CAPACITY)
    private val pendingPreRollFrames = ArrayDeque<PreRollFrame>(PRE_ROLL_CAPACITY)
    private var nextSequenceNo = 0L
    private var generation = 0L
    private var ownerTurnId: String? = null
    private var highWaterMark = 0
    private var droppedFrames = 0L
    private var invalidFrames = 0L
    private var pendingHighWaterMark = 0
    private var pendingDroppedFrames = 0L
    private var pendingOverflowed = false

    private data class PendingFrame(
        val clientTimestampMs: Long,
        val payload: ByteArray,
    )

    /** Starts a local turn buffer. Nothing is network-sendable until bindTurn. */
    fun prepareTurn(newGeneration: Long, preRoll: List<PreRollFrame> = emptyList()) = synchronized(lock) {
        frames.clear()
        pendingFrames.clear()
        preRollFrames.clear()
        pendingPreRollFrames.clear()
        pendingPreRollFrames.addAll(preRoll)
        nextSequenceNo = 0L
        generation = newGeneration
        ownerTurnId = null
        pendingHighWaterMark = 0
        pendingDroppedFrames = 0L
        pendingOverflowed = false
    }

    /** Binds all locally buffered audio to the server-issued turn identity. */
    fun bindTurn(turnId: String, expectedGeneration: Long): Binding? = synchronized(lock) {
        if (turnId.isBlank() || generation != expectedGeneration || ownerTurnId != null) {
            return@synchronized null
        }
        ownerTurnId = turnId
        nextSequenceNo = 0L
        var bytes = 0L
        pendingPreRollFrames.forEach { frame ->
            appendReadyFrameLocked(frame.payload, frame.clientTimestampMs)
            bytes += frame.payload.size
        }
        pendingFrames.forEach { frame ->
            appendReadyFrameLocked(frame.payload, frame.clientTimestampMs)
            bytes += frame.payload.size
        }
        val frameCount = frames.size
        pendingPreRollFrames.clear()
        pendingFrames.clear()
        Binding(frameCount, bytes)
    }

    /** Retains only recent native PCM metadata and payload bytes for barge-in. */
    fun rememberForPreRoll(
        buffer: ShortArray,
        samplesRead: Int,
        timestampMs: Long,
    ): Boolean = synchronized(lock) {
        val payload = encode(buffer, samplesRead) ?: return@synchronized false
        if (preRollFrames.size >= PRE_ROLL_CAPACITY) {
            preRollFrames.removeFirst()
        }
        preRollFrames.addLast(PreRollFrame(timestampMs, payload))
        true
    }

    fun takePreRoll(): List<PreRollFrame> = synchronized(lock) {
        val snapshot = preRollFrames.toList()
        preRollFrames.clear()
        snapshot
    }

    fun clearPreRoll() = synchronized(lock) {
        preRollFrames.clear()
    }

    fun offer(buffer: ShortArray, samplesRead: Int, timestampMs: Long): Boolean = synchronized(lock) {
        val payload = encode(buffer, samplesRead)
            ?: run {
            invalidFrames += 1
            return@synchronized false
        }

        return@synchronized offerEncodedLocked(payload, timestampMs, generation)
    }

    fun offerForTurn(
        buffer: ShortArray,
        samplesRead: Int,
        timestampMs: Long,
        expectedGeneration: Long,
    ): Boolean = synchronized(lock) {
        val payload = encode(buffer, samplesRead)
            ?: run {
            invalidFrames += 1
            return@synchronized false
        }
        offerEncodedLocked(payload, timestampMs, expectedGeneration)
    }

    /** Stores PCM locally while waiting for server.turn.ready. */
    fun offerPending(
        buffer: ShortArray,
        samplesRead: Int,
        timestampMs: Long,
        expectedGeneration: Long,
    ): Boolean = synchronized(lock) {
        val payload = encode(buffer, samplesRead)
            ?: run {
            invalidFrames += 1
            return@synchronized false
        }
        if (generation != expectedGeneration || ownerTurnId != null) {
            return@synchronized false
        }
        if (pendingFrames.size >= pendingCapacity) {
            pendingDroppedFrames += 1
            pendingOverflowed = true
            return@synchronized false
        }
        pendingFrames.addLast(PendingFrame(timestampMs, payload))
        pendingHighWaterMark = maxOf(pendingHighWaterMark, pendingFrames.size)
        true
    }

    private fun offerEncodedLocked(payload: ByteArray, timestampMs: Long, expectedGeneration: Long): Boolean {
        if (generation != expectedGeneration || ownerTurnId == null) {
            invalidFrames += 1
            return false
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
                ownerTurnId = ownerTurnId,
                generation = generation,
            ),
        )
        highWaterMark = maxOf(highWaterMark, frames.size)
        return true
    }

    private fun appendReadyFrameLocked(payload: ByteArray, timestampMs: Long) {
        frames.addLast(
            Frame(
                sequenceNo = nextSequenceNo++,
                clientTimestampMs = timestampMs,
                payload = payload,
                ownerTurnId = ownerTurnId,
                generation = generation,
            ),
        )
        highWaterMark = maxOf(highWaterMark, frames.size)
    }

    private fun encode(buffer: ShortArray, samplesRead: Int): ByteArray? {
        if (samplesRead != frameSamples || buffer.size < samplesRead) {
            return null
        }

        val payload = ByteArray(samplesRead * BYTES_PER_SAMPLE)
        val pcm = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        for (index in 0 until samplesRead) {
            pcm.putShort(buffer[index])
        }
        return payload
    }

    fun poll(): Frame? = synchronized(lock) {
        if (frames.isEmpty()) null else frames.removeFirst()
    }

    fun clear() = synchronized(lock) {
        frames.clear()
        pendingFrames.clear()
        pendingPreRollFrames.clear()
        ownerTurnId = null
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

    fun pendingSnapshot(): PendingSnapshot = synchronized(lock) {
        PendingSnapshot(
            depth = pendingFrames.size,
            highWaterMark = pendingHighWaterMark,
            droppedFrames = pendingDroppedFrames,
            overflowed = pendingOverflowed,
        )
    }

    fun boundOwnerTurnId(): String? = synchronized(lock) { ownerTurnId }

    companion object {
        const val DEFAULT_CAPACITY = 100
        const val PENDING_TURN_CAPACITY = 250
        const val PRE_ROLL_CAPACITY = 8
        const val FRAME_SAMPLES = 320
        const val BYTES_PER_SAMPLE = 2
    }

}
