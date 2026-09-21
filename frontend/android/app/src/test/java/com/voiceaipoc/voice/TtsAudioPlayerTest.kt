package com.voiceaipoc.voice

import java.util.Collections
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.CopyOnWriteArrayList
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TtsAudioPlayerTest {
    @Test
    fun playbackWaitsForPrebufferAndUsesOneTrackPerResponse() {
        val factory = FakeTrackFactory()
        val events = Events()
        val player = TtsAudioPlayer(
            trackFactory = factory,
            listener = events,
            startupPrebufferBytes = 8,
            maxQueueBytes = 64,
        )
        val first = UUID.randomUUID()
        val second = UUID.randomUUID()
        try {
            assertTrue(player.start(first, 24_000))
            assertTrue(player.write(first, ByteArray(6)))
            Thread.sleep(50)
            assertEquals(0, factory.tracks.single().playCalls)
            assertTrue(player.write(first, ByteArray(2)))
            assertTrue(events.started.await(1, TimeUnit.SECONDS))
            assertTrue(player.finish(first))
            waitUntil { events.completed.count == 1L }

            assertTrue(player.start(second, 24_000))
            assertTrue(player.write(second, ByteArray(8)))
            assertTrue(player.finish(second))
            assertTrue(events.completed.await(1, TimeUnit.SECONDS))
            assertEquals(2, factory.tracks.size)
            assertEquals(1, factory.tracks[0].playCalls)
            assertEquals(1, factory.tracks[1].playCalls)
        } finally {
            player.shutdown()
        }
    }

    @Test
    fun partialWritesAreRetriedWithoutDroppingBytes() {
        val factory = FakeTrackFactory(writeLimit = 3)
        val player = TtsAudioPlayer(
            trackFactory = factory,
            startupPrebufferBytes = 4,
            maxQueueBytes = 64,
        )
        val responseId = UUID.randomUUID()
        try {
            assertTrue(player.start(responseId, 24_000))
            assertTrue(player.write(responseId, ByteArray(10)))
            assertTrue(player.finish(responseId))
            waitUntil { factory.tracks.singleOrNull()?.released == true }
            assertEquals(10, factory.tracks.single().writtenBytes)
        } finally {
            player.shutdown()
        }
    }

    @Test
    fun queueBackpressureWaitsForAudioTrackToDrain() {
        val writeStarted = CountDownLatch(1)
        val releaseWrite = CountDownLatch(1)
        val factory = FakeTrackFactory(
            writeStarted = writeStarted,
            releaseWrite = releaseWrite,
        )
        val player = TtsAudioPlayer(
            trackFactory = factory,
            startupPrebufferBytes = 4,
            maxQueueBytes = 8,
        )
        val responseId = UUID.randomUUID()
        val writer = Executors.newSingleThreadExecutor()
        try {
            assertTrue(player.start(responseId, 24_000))
            assertTrue(player.write(responseId, ByteArray(4)))
            assertTrue(writeStarted.await(1, TimeUnit.SECONDS))
            assertTrue(player.write(responseId, ByteArray(4)))

            val thirdFrame = writer.submit<Boolean> {
                player.write(responseId, ByteArray(4))
            }
            Thread.sleep(50)
            assertFalse(thirdFrame.isDone)

            releaseWrite.countDown()
            assertTrue(thirdFrame.get(1, TimeUnit.SECONDS))
            assertTrue(player.finish(responseId))
            waitUntil { factory.tracks.singleOrNull()?.released == true }
            assertEquals(12, factory.tracks.single().writtenBytes)
        } finally {
            releaseWrite.countDown()
            writer.shutdownNow()
            player.shutdown()
        }
    }

    @Test
    fun cancellationReleasesTrackAndRejectsStaleQueueWrites() {
        val factory = FakeTrackFactory()
        val player = TtsAudioPlayer(
            trackFactory = factory,
            startupPrebufferBytes = 8,
            maxQueueBytes = 64,
        )
        val responseId = UUID.randomUUID()
        try {
            assertTrue(player.start(responseId, 24_000))
            assertTrue(player.write(responseId, ByteArray(8)))
            assertTrue(player.cancel(responseId))
            assertFalse(player.write(responseId, ByteArray(8)))
            assertTrue(factory.tracks.single().released)
        } finally {
            player.shutdown()
        }
    }

    @Test
    fun completionWaitsForPlaybackHeadDrainAndCancellationRemainsImmediate() {
        val factory = FakeTrackFactory(autoAdvancePlaybackHead = false)
        val events = Events()
        val player = TtsAudioPlayer(
            trackFactory = factory,
            listener = events,
            startupPrebufferBytes = 4,
            maxQueueBytes = 64,
        )
        val responseId = UUID.randomUUID()
        try {
            assertTrue(player.start(responseId, 24_000))
            assertTrue(player.write(responseId, ByteArray(8)))
            assertTrue(player.finish(responseId))
            Thread.sleep(50)
            assertEquals(2L, events.completed.count)

            factory.tracks.single().advancePlaybackHead(4)
            waitUntil { events.completed.count == 1L }
            assertTrue(factory.tracks.single().released)

            val cancelled = UUID.randomUUID()
            assertTrue(player.start(cancelled, 24_000))
            assertTrue(player.write(cancelled, ByteArray(8)))
            assertTrue(player.cancel(cancelled))
            waitUntil { factory.tracks.size == 2 && factory.tracks[1].released }
        } finally {
            player.shutdown()
        }
    }

    @Test
    fun nativeBargeInStopFlushesImmediatelyAndIsResponseScoped() {
        val writeStarted = CountDownLatch(1)
        val releaseWrite = CountDownLatch(1)
        val factory = FakeTrackFactory(
            writeStarted = writeStarted,
            releaseWrite = releaseWrite,
        )
        val player = TtsAudioPlayer(
            trackFactory = factory,
            startupPrebufferBytes = 4,
            maxQueueBytes = 64,
        )
        val responseId = UUID.randomUUID()
        try {
            assertTrue(player.start(responseId, 24_000))
            assertTrue(player.write(responseId, ByteArray(4)))
            assertTrue(writeStarted.await(1, TimeUnit.SECONDS))

            val first = player.stopForBargeIn(responseId)
            assertTrue(first.wasActive)
            assertTrue(first.audioTrackStopped)
            assertTrue(first.audioTrackFlushed)
            assertTrue(first.releasePending)
            assertFalse(player.stopForBargeIn(responseId).wasActive)

            releaseWrite.countDown()
            waitUntil { factory.tracks.single().released }
            assertEquals(1, factory.tracks.single().stopCalls)
            assertEquals(1, factory.tracks.single().flushCalls)
        } finally {
            releaseWrite.countDown()
            player.shutdown()
        }
    }

    @Test
    fun playbackHeadWraparoundIsUnwrappedForDrainAccounting() {
        val factory = FakeTrackFactory(
            autoAdvancePlaybackHead = false,
            initialPlaybackHead = Int.MAX_VALUE - 1,
        )
        val events = Events()
        val player = TtsAudioPlayer(
            trackFactory = factory,
            listener = events,
            startupPrebufferBytes = 4,
            maxQueueBytes = 64,
        )
        val responseId = UUID.randomUUID()
        try {
            assertTrue(player.start(responseId, 24_000))
            assertTrue(player.write(responseId, ByteArray(8)))
            assertTrue(player.finish(responseId))
            Thread.sleep(25)
            assertEquals(2L, events.completed.count)

            factory.tracks.single().advancePlaybackHead(4)
            waitUntil { events.completed.count == 1L }
            assertEquals(4L, events.summaries.single().presentedFrames)
        } finally {
            player.shutdown()
        }
    }

    @Test
    fun summaryReportsMultiChunkWritesAndPartialWrites() {
        val factory = FakeTrackFactory(writeLimit = 3)
        val events = Events()
        val player = TtsAudioPlayer(
            trackFactory = factory,
            listener = events,
            startupPrebufferBytes = 4,
            maxQueueBytes = 64,
        )
        val responseId = UUID.randomUUID()
        try {
            assertTrue(player.start(responseId, 24_000))
            assertTrue(player.write(responseId, 7, ByteArray(10)))
            assertTrue(player.finish(responseId))
            waitUntil { events.summaries.size == 1 }

            val summary = events.summaries.single()
            assertEquals(1, summary.framesQueued)
            assertEquals(1, summary.framesWritten)
            assertEquals(10L, summary.pcmBytesQueued)
            assertEquals(10L, summary.pcmBytesWritten)
            assertEquals(3, summary.partialWrites)
            assertEquals(0, summary.writeErrors)
        } finally {
            player.shutdown()
        }
    }

    @Test
    fun writeErrorIsReportedWithoutRetryingAClosedTrack() {
        val factory = FakeTrackFactory(writeLimit = -6)
        val events = Events()
        val player = TtsAudioPlayer(
            trackFactory = factory,
            listener = events,
            startupPrebufferBytes = 4,
            maxQueueBytes = 64,
        )
        val responseId = UUID.randomUUID()
        try {
            assertTrue(player.start(responseId, 24_000))
            assertTrue(player.write(responseId, 0, ByteArray(4)))
            assertTrue(player.finish(responseId))
            assertTrue(events.error.await(1, TimeUnit.SECONDS))
            waitUntil { events.summaries.size == 1 }
            assertEquals(1, events.summaries.single().writeErrors)
            assertTrue(factory.tracks.single().released)
        } finally {
            player.shutdown()
        }
    }

    @Test
    fun rejectsMicrophoneSampleRateForTts() {
        val factory = FakeTrackFactory()
        val player = TtsAudioPlayer(trackFactory = factory)
        try {
            assertFalse(player.start(UUID.randomUUID(), 16_000))
            assertTrue(factory.tracks.isEmpty())
        } finally {
            player.shutdown()
        }
    }

    private fun waitUntil(condition: () -> Boolean) {
        repeat(100) {
            if (condition()) return
            Thread.sleep(10)
        }
        assertTrue(condition())
    }

    private class Events : TtsAudioPlayer.Listener {
        val started = CountDownLatch(1)
        val completed = CountDownLatch(2)
        val error = CountDownLatch(1)
        val summaries = CopyOnWriteArrayList<TtsAudioPlayer.PlaybackSummary>()

        override fun onPlaybackStarted(responseId: UUID) = started.countDown()
        override fun onPlaybackCompleted(responseId: UUID) = completed.countDown()
        override fun onPlaybackError(responseId: UUID, errorCode: String) = error.countDown()
        override fun onPlaybackSummary(
            responseId: UUID,
            summary: TtsAudioPlayer.PlaybackSummary,
        ) {
            summaries += summary
        }
    }

    private class FakeTrackFactory(
        private val writeLimit: Int = Int.MAX_VALUE,
        private val writeStarted: CountDownLatch? = null,
        private val releaseWrite: CountDownLatch? = null,
        private val autoAdvancePlaybackHead: Boolean = true,
        private val initialPlaybackHead: Int = 0,
    ) :
        TtsAudioTrackFactory {
        val tracks = Collections.synchronizedList(mutableListOf<FakeTrack>())

        override fun minBufferSize(sampleRateHz: Int): Int = 4

        override fun create(sampleRateHz: Int, bufferSizeBytes: Int): TtsAudioTrack =
            FakeTrack(
                writeLimit,
                writeStarted,
                releaseWrite,
                autoAdvancePlaybackHead,
                initialPlaybackHead,
            ).also(tracks::add)
    }

    private class FakeTrack(
        private val writeLimit: Int,
        private val writeStarted: CountDownLatch?,
        private val releaseWrite: CountDownLatch?,
        private val autoAdvancePlaybackHead: Boolean,
        initialPlaybackHead: Int,
    ) : TtsAudioTrack {
        override val isInitialized = true
        var playCalls = 0
        var writtenBytes = 0
        var released = false
        var stopCalls = 0
        var flushCalls = 0
        private var playbackHead = initialPlaybackHead

        override fun play() {
            playCalls += 1
        }

        override fun write(data: ByteArray, offsetInBytes: Int, sizeInBytes: Int, mode: Int): Int {
            writeStarted?.countDown()
            releaseWrite?.await(1, TimeUnit.SECONDS)
            val written = minOf(sizeInBytes, writeLimit)
            writtenBytes += written
            if (autoAdvancePlaybackHead) playbackHead += written / 2
            return written
        }

        override fun stop() {
            stopCalls += 1
        }
        override fun flush() {
            flushCalls += 1
        }
        override fun release() {
            released = true
        }

        override fun underrunCount(): Int = 0
        override fun playbackHeadPosition(): Int = playbackHead
        override fun timestamp(): TtsAudioTimestamp? = null

        fun advancePlaybackHead(frames: Int) {
            playbackHead += frames
        }
    }
}
