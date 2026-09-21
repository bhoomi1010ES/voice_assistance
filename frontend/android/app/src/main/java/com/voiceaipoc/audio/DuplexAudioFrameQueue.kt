package com.voiceaipoc.audio

/**
 * Fixed-capacity single-producer/single-consumer queue for PCM frames and
 * their frame-owned acoustic metadata.
 *
 * PCM storage and metadata slots are allocated once. [read] copies both into
 * caller-owned reusable buffers, so queue backpressure never causes a frame
 * to be paired with the metadata of a newer capture interval.
 */
class DuplexAudioFrameQueue(
    val capacityFrames: Int,
    val frameSizeSamples: Int,
) {
    enum class WriteResult {
        WRITTEN,
        WROTE_AFTER_DROPPING_OLDEST,
    }

    /** Reusable metadata destination populated by [read]. */
    class FrameMetadata {
        var frameSequence: Long = 0L
        var captureStartNs: Long = 0L
        var captureEndNs: Long = 0L
        var playbackState: FarEndReferenceBuffer.State = FarEndReferenceBuffer.State.STOPPED
        var playbackActive: Boolean = false
        var playbackPositionMs: Long = 0L
        var playbackResponseId: String? = null
        var referenceReady: Boolean = false
        var referenceConfidence: String = "NONE"
        var estimatedEchoDelayMs: Int? = null
        var echoSimilarity: Double? = null
        var echoLagMs: Int? = null
        var echoCoherence: Double? = null
        var farEndRms: Double? = null
        var micRms: Double? = null
        var nearEndResidualRatio: Double? = null
        var nearEndFarEndEnergyRatio: Double? = null
        var echoLikely: Boolean = false
        var timestampNs: Long = 0L
        var discontinuous: Boolean = false

        fun copyFrom(other: FrameMetadata) {
            frameSequence = other.frameSequence
            captureStartNs = other.captureStartNs
            captureEndNs = other.captureEndNs
            playbackState = other.playbackState
            playbackActive = other.playbackActive
            playbackPositionMs = other.playbackPositionMs
            playbackResponseId = other.playbackResponseId
            referenceReady = other.referenceReady
            referenceConfidence = other.referenceConfidence
            estimatedEchoDelayMs = other.estimatedEchoDelayMs
            echoSimilarity = other.echoSimilarity
            echoLagMs = other.echoLagMs
            echoCoherence = other.echoCoherence
            farEndRms = other.farEndRms
            micRms = other.micRms
            nearEndResidualRatio = other.nearEndResidualRatio
            nearEndFarEndEnergyRatio = other.nearEndFarEndEnergyRatio
            echoLikely = other.echoLikely
            timestampNs = other.timestampNs
            discontinuous = other.discontinuous
        }

        fun clear() {
            frameSequence = 0L
            captureStartNs = 0L
            captureEndNs = 0L
            playbackState = FarEndReferenceBuffer.State.STOPPED
            playbackActive = false
            playbackPositionMs = 0L
            playbackResponseId = null
            referenceReady = false
            referenceConfidence = "NONE"
            estimatedEchoDelayMs = null
            echoSimilarity = null
            echoLagMs = null
            echoCoherence = null
            farEndRms = null
            micRms = null
            nearEndResidualRatio = null
            nearEndFarEndEnergyRatio = null
            echoLikely = false
            timestampNs = 0L
            discontinuous = false
        }

        fun copyFrom(
            frameSequence: Long,
            captureStartNs: Long,
            captureEndNs: Long,
            assessment: FarEndReferenceBuffer.Assessment,
            discontinuous: Boolean,
        ) {
            this.frameSequence = frameSequence
            this.captureStartNs = captureStartNs
            this.captureEndNs = captureEndNs
            playbackState = assessment.state
            playbackActive = assessment.playbackActive
            playbackPositionMs = assessment.playbackPositionMs
            playbackResponseId = assessment.responseId
            referenceReady = assessment.referenceAvailable
            referenceConfidence = assessment.timestampConfidence
            estimatedEchoDelayMs = assessment.estimatedDelayMs
            echoSimilarity = assessment.similarity
            echoLagMs = assessment.lagMs
            echoCoherence = assessment.coherence
            farEndRms = assessment.farEndRms
            micRms = assessment.micRms
            nearEndResidualRatio = assessment.nearEndResidualRatio
            nearEndFarEndEnergyRatio = assessment.nearEndFarEndEnergyRatio
            echoLikely = assessment.echoLikely
            timestampNs = assessment.timestampNs
            this.discontinuous = discontinuous
        }
    }

    private val lock = Object()
    private val pcmStorage = ShortArray(capacityFrames * frameSizeSamples)
    private val metadataStorage = Array(capacityFrames) { FrameMetadata() }
    private var readFrameIndex = 0
    private var writeFrameIndex = 0
    private var bufferedFrameCount = 0

    init {
        require(capacityFrames > 0) { "capacityFrames must be positive" }
        require(frameSizeSamples > 0) { "frameSizeSamples must be positive" }
    }

    fun offer(
        source: ShortArray,
        sampleCount: Int,
        frameSequence: Long,
        captureStartNs: Long,
        captureEndNs: Long,
        assessment: FarEndReferenceBuffer.Assessment,
        discontinuous: Boolean = false,
    ): WriteResult {
        require(sampleCount == frameSizeSamples) {
            "Only complete PCM frames may be written"
        }
        require(source.size >= sampleCount) { "Source does not contain a complete PCM frame" }
        require(frameSequence >= 0L) { "frameSequence must be non-negative" }
        require(captureStartNs <= captureEndNs) { "capture interval must be ordered" }

        synchronized(lock) {
            val overflowed = bufferedFrameCount == capacityFrames
            if (overflowed) {
                readFrameIndex = (readFrameIndex + 1) % capacityFrames
                bufferedFrameCount -= 1
            }

            val sampleOffset = writeFrameIndex * frameSizeSamples
            System.arraycopy(source, 0, pcmStorage, sampleOffset, frameSizeSamples)
            metadataStorage[writeFrameIndex].copyFrom(
                frameSequence = frameSequence,
                captureStartNs = captureStartNs,
                captureEndNs = captureEndNs,
                assessment = assessment,
                discontinuous = discontinuous,
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

    @Throws(InterruptedException::class)
    fun read(
        destination: ShortArray,
        metadata: FrameMetadata,
        waitTimeoutMs: Long = 0L,
    ): Int {
        require(destination.size >= frameSizeSamples) {
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

            val sampleOffset = readFrameIndex * frameSizeSamples
            System.arraycopy(pcmStorage, sampleOffset, destination, 0, frameSizeSamples)
            metadata.copyFrom(metadataStorage[readFrameIndex])
            readFrameIndex = (readFrameIndex + 1) % capacityFrames
            bufferedFrameCount -= 1
            return frameSizeSamples
        }
    }

    fun clear() {
        synchronized(lock) {
            pcmStorage.fill(0)
            metadataStorage.forEach { it.clear() }
            readFrameIndex = 0
            writeFrameIndex = 0
            bufferedFrameCount = 0
            lock.notifyAll()
        }
    }

    fun currentBufferedFrames(): Int = synchronized(lock) { bufferedFrameCount }
}
