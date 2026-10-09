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
    fun rapidPauseResumeRetainsOneTrackWithoutRewritingConsumedPcm() {
        val factory = FakeTrackFactory(autoAdvancePlaybackHead = false)
        val player = TtsAudioPlayer(trackFactory = factory, startupPrebufferBytes = 4)
        val id = UUID.randomUUID()
        try {
            assertTrue(player.start(id, 24_000)); assertTrue(player.write(id, ByteArray(4)))
            waitUntil { factory.tracks.single().writtenBytes == 4 }
            val track = factory.tracks.single()
            repeat(50) { player.setPaused(true); player.setPaused(false) }
            assertEquals(1, factory.tracks.size)
            assertEquals(4, track.writtenBytes)
            assertEquals(51, track.playCalls)
            assertEquals(50, track.pauseCalls)
            assertEquals(0, track.flushCalls)
            assertEquals(0, track.stopCalls)
            assertTrue(player.finish(id)); track.advancePlaybackHead(2)
            waitUntil { track.released }
        } finally { player.shutdown() }
    }

    @Test
    fun pauseAtPlaybackStartCannotOvertakeStartNotification() {
        val started = CountDownLatch(1)
        val releaseStart = CountDownLatch(1)
        val callbacks = CopyOnWriteArrayList<String>()
        val factory = FakeTrackFactory()
        val player = TtsAudioPlayer(trackFactory = factory, startupPrebufferBytes = 4,
            listener = object : TtsAudioPlayer.Listener {
                override fun onPlaybackStarted(responseId: UUID) {
                    started.countDown()
                    releaseStart.await(1, TimeUnit.SECONDS)
                    callbacks += "started"
                }
                override fun onPlaybackPaused(responseId: UUID) { callbacks += "paused" }
            })
        val executor = Executors.newSingleThreadExecutor()
        try {
            val id = UUID.randomUUID()
            assertTrue(player.start(id, 24_000)); assertTrue(player.write(id, ByteArray(4)))
            assertTrue(started.await(1, TimeUnit.SECONDS))
            val pausing = executor.submit { player.setPaused(true) }
            releaseStart.countDown()
            pausing.get(1, TimeUnit.SECONDS)
            assertEquals(listOf("started", "paused"), callbacks.toList())
            assertEquals(1, factory.tracks.single().pauseCalls)
        } finally { releaseStart.countDown(); executor.shutdownNow(); player.shutdown() }
    }

    @Test
    fun pausedPlaybackKeepsTrackHeadAndContinuesOnlyRemainingPcm() {
        val factory = FakeTrackFactory(autoAdvancePlaybackHead = false)
        val events = Events()
        val player = TtsAudioPlayer(trackFactory = factory, listener = events, startupPrebufferBytes = 4, maxQueueBytes = 64)
        val id = UUID.randomUUID()
        try {
            assertTrue(player.start(id, 24_000))
            assertTrue(player.write(id, byteArrayOf(1, 2, 3, 4)))
            waitUntil { factory.tracks.single().writtenBytes == 4 }
            val track = factory.tracks.single()
            track.advancePlaybackHead(1) // One PCM sample already heard.
            player.setPaused(true)
            player.setPaused(true)
            assertTrue(player.write(id, byteArrayOf(5, 6, 7, 8)))
            assertTrue(player.finish(id))
            Thread.sleep(30)
            assertEquals(4, track.writtenBytes)
            assertEquals(1, track.playbackHeadPosition())
            assertEquals(1, track.pauseCalls)
            assertEquals(0, track.stopCalls)
            assertEquals(0, track.flushCalls)
            assertFalse(track.released)
            player.setPaused(false)
            player.setPaused(false)
            waitUntil { track.writtenBytes == 8 }
            assertEquals(listOf<Byte>(1, 2, 3, 4, 5, 6, 7, 8), track.pcm.toList())
            assertEquals(1, factory.tracks.size)
            assertEquals(2, track.playCalls)
            assertEquals(1, track.playbackHeadPosition())
            track.advancePlaybackHead(3)
            waitUntil { events.completed.count == 1L }
            assertTrue(track.released)
            assertEquals(1L, events.completed.count)
        } finally { player.shutdown() }
    }

    @Test
    fun responseArrivingWhileHiddenBuffersUntilVisibleIncludingFinish() {
        val factory = FakeTrackFactory()
        val events = Events()
        val player = TtsAudioPlayer(trackFactory = factory, listener = events, startupPrebufferBytes = 4, maxQueueBytes = 8)
        val id = UUID.randomUUID()
        try {
            player.setPaused(true)
            assertTrue(player.start(id, 24_000))
            repeat(8) { assertTrue(player.write(id, ByteArray(4) { (it + 1).toByte() })) }
            assertTrue(player.finish(id))
            Thread.sleep(30)
            val track = factory.tracks.single()
            assertEquals(0, track.playCalls)
            assertEquals(0, track.writtenBytes)
            assertFalse(track.released)
            player.setPaused(false)
            waitUntil { events.completed.count == 1L }
            assertTrue(track.released)
            assertEquals(32, track.writtenBytes)
            assertEquals(1, track.playCalls)
            assertEquals(1L, events.completed.count)
        } finally { player.shutdown() }
    }

    @Test
    fun pauseDuringPartialWritePreservesOffsetWithoutDuplicatedPcm() {
        val writeStarted = CountDownLatch(1)
        val releaseWrite = CountDownLatch(1)
        val factory = FakeTrackFactory(writeLimit = 2, writeStarted = writeStarted, releaseWrite = releaseWrite)
        val player = TtsAudioPlayer(trackFactory = factory, startupPrebufferBytes = 4, maxQueueBytes = 64)
        val id = UUID.randomUUID()
        try {
            assertTrue(player.start(id, 24_000))
            assertTrue(player.write(id, byteArrayOf(1, 2, 3, 4, 5, 6)))
            assertTrue(writeStarted.await(1, TimeUnit.SECONDS))
            player.setPaused(true)
            releaseWrite.countDown()
            waitUntil { factory.tracks.single().writtenBytes == 2 }
            Thread.sleep(30)
            assertEquals(2, factory.tracks.single().writtenBytes)
            player.setPaused(false)
            assertTrue(player.finish(id))
            waitUntil { factory.tracks.single().released }
            assertEquals(listOf<Byte>(1, 2, 3, 4, 5, 6), factory.tracks.single().pcm.toList())
        } finally { releaseWrite.countDown(); player.shutdown() }
    }

    @Test
    fun pausedBufferIsBoundedAndFailureDoesNotResumeAbandonedAudio() {
        val factory = FakeTrackFactory()
        val events = Events()
        val player = TtsAudioPlayer(trackFactory = factory, listener = events, startupPrebufferBytes = 4, maxQueueBytes = 8, maxPausedQueueBytes = 12)
        val id = UUID.randomUUID()
        try {
            player.setPaused(true)
            assertTrue(player.start(id, 24_000))
            repeat(3) { assertTrue(player.write(id, ByteArray(4))) }
            assertFalse(player.write(id, ByteArray(4)))
            assertTrue(events.error.await(1, TimeUnit.SECONDS))
            player.setPaused(false)
            assertFalse(player.isActive(id))
            assertEquals(0, factory.tracks.single().playCalls)
            assertTrue(factory.tracks.single().released)
        } finally { player.shutdown() }
    }

    @Test
    fun pausedOldResponseCannotWriteIntoSupersedingResponseOrSurviveLogout() {
        val factory = FakeTrackFactory()
        val player = TtsAudioPlayer(trackFactory = factory, startupPrebufferBytes = 4, maxQueueBytes = 64)
        val old = UUID.randomUUID()
        val current = UUID.randomUUID()
        try {
            player.setPaused(true)
            assertTrue(player.start(old, 24_000)); assertTrue(player.write(old, ByteArray(4)))
            assertTrue(player.start(current, 24_000))
            assertFalse(player.write(old, ByteArray(4))); assertFalse(player.finish(old))
            assertTrue(player.write(current, ByteArray(4))); assertTrue(player.finish(current))
            repeat(20) { player.setPaused(true) }
            player.setPaused(false)
            waitUntil { factory.tracks[1].released }
            assertEquals(0, factory.tracks[0].playCalls)
            assertEquals(4, factory.tracks[1].writtenBytes)
            player.setPaused(true)
            val logout = UUID.randomUUID()
            assertTrue(player.start(logout, 24_000)); assertTrue(player.write(logout, ByteArray(4)))
            assertTrue(player.cancel(logout)); player.setPaused(false)
            assertEquals(0, factory.tracks[2].playCalls)
        } finally { player.shutdown() }
    }

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
            assertTrue(player.write(responseId, ByteArray(4)))
            assertTrue(writeStarted.await(1, TimeUnit.SECONDS))

            val first = player.stopForBargeIn(responseId)
            assertTrue(first.wasActive)
            assertTrue(first.audioTrackStopped)
            assertTrue(first.audioTrackFlushed)
            assertTrue(first.releasePending)
            assertEquals(listOf("barge_in"), events.stopReasons)
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
            waitUntil { factory.tracks.single().writtenBytes == 8 }
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
        repeat(500) {
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
        val stopReasons = CopyOnWriteArrayList<String?>()

        override fun onPlaybackStarted(responseId: UUID) = started.countDown()
        override fun onPlaybackCompleted(responseId: UUID) = completed.countDown()
        override fun onPlaybackStopped(responseId: UUID, reason: String?) {
            stopReasons += reason
        }
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
        @Volatile var playCalls = 0
        @Volatile var pauseCalls = 0
        val pcm = CopyOnWriteArrayList<Byte>()
        @Volatile var writtenBytes = 0
        @Volatile var released = false
        @Volatile var stopCalls = 0
        @Volatile var flushCalls = 0
        @Volatile private var playbackHead = initialPlaybackHead

        override fun play() {
            playCalls += 1
        }

        override fun pause() { pauseCalls += 1 }

        override fun write(data: ByteArray, offsetInBytes: Int, sizeInBytes: Int, mode: Int): Int {
            writeStarted?.countDown()
            releaseWrite?.await(1, TimeUnit.SECONDS)
            val written = minOf(sizeInBytes, writeLimit)
            pcm.addAll(data.slice(offsetInBytes until offsetInBytes + written))
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
