package com.voiceaipoc.audio

/**
 * Preallocated, bounded PCM16 frame ring shared by one producer and one
 * consumer. A full ring drops its oldest frame before accepting the newest so
 * scheduling delays cannot turn into unbounded latency or memory growth.
 */
class AudioRingBuffer(
    val capacityFrames: Int,
    val frameSizeSamples: Int,
    val bytesPerSample: Int = PCM16_BYTES_PER_SAMPLE,
) {
    enum class WriteResult {
        WRITTEN,
        WROTE_AFTER_DROPPING_OLDEST,
    }

    private val lock = Object()
    private val storage: ShortArray
    private var readFrameIndex = 0
    private var writeFrameIndex = 0
    private var bufferedFrameCount = 0

    init {
        require(capacityFrames > 0) { "capacityFrames must be positive" }
        require(frameSizeSamples > 0) { "frameSizeSamples must be positive" }
        require(bytesPerSample > 0) { "bytesPerSample must be positive" }
        val storageSamples = capacityFrames.toLong() * frameSizeSamples
        require(storageSamples <= Int.MAX_VALUE) { "Ring buffer capacity is too large" }
        storage = ShortArray(storageSamples.toInt())
    }

    val capacityBytes: Int
        get() = capacityFrames * frameSizeSamples * bytesPerSample

    fun write(
        source: ShortArray,
        sourceOffset: Int = 0,
        sampleCount: Int = frameSizeSamples,
    ): WriteResult {
        require(sampleCount == frameSizeSamples) { "Only complete PCM frames may be written" }
        require(sourceOffset >= 0 && sourceOffset + sampleCount <= source.size) {
            "Source does not contain a complete PCM frame"
        }

        synchronized(lock) {
            val overflowed = bufferedFrameCount == capacityFrames
            if (overflowed) {
                readFrameIndex = (readFrameIndex + 1) % capacityFrames
                bufferedFrameCount -= 1
            }

            System.arraycopy(
                source,
                sourceOffset,
                storage,
                writeFrameIndex * frameSizeSamples,
                frameSizeSamples,
            )
            writeFrameIndex = (writeFrameIndex + 1) % capacityFrames
            bufferedFrameCount += 1
            lock.notifyAll()

            return if (overflowed) {
                WriteResult.WROTE_AFTER_DROPPING_OLDEST
            } else {
                WriteResult.WRITTEN
            }
        }
    }

    /**
     * Copies one complete frame into a caller-owned reusable array.
     * Returns zero if no frame becomes available before [waitTimeoutMs].
     */
    @Throws(InterruptedException::class)
    fun read(
        destination: ShortArray,
        destinationOffset: Int = 0,
        waitTimeoutMs: Long = 0L,
    ): Int {
        require(destinationOffset >= 0 && destinationOffset + frameSizeSamples <= destination.size) {
            "Destination cannot hold a complete PCM frame"
        }
        require(waitTimeoutMs >= 0L) { "waitTimeoutMs cannot be negative" }

        synchronized(lock) {
            if (bufferedFrameCount == 0 && waitTimeoutMs > 0L) {
                lock.wait(waitTimeoutMs)
            }
            if (bufferedFrameCount == 0) {
                return 0
            }

            System.arraycopy(
                storage,
                readFrameIndex * frameSizeSamples,
                destination,
                destinationOffset,
                frameSizeSamples,
            )
            readFrameIndex = (readFrameIndex + 1) % capacityFrames
            bufferedFrameCount -= 1
            return frameSizeSamples
        }
    }

    /** Clears indices and sample storage so a new session cannot observe stale PCM. */
    fun clear() {
        synchronized(lock) {
            storage.fill(0)
            readFrameIndex = 0
            writeFrameIndex = 0
            bufferedFrameCount = 0
            lock.notifyAll()
        }
    }

    fun currentBufferedFrames(): Int = synchronized(lock) { bufferedFrameCount }

    fun currentBufferedBytes(): Int = currentBufferedFrames() * frameSizeSamples * bytesPerSample

    private companion object {
        const val PCM16_BYTES_PER_SAMPLE = 2
    }
}
