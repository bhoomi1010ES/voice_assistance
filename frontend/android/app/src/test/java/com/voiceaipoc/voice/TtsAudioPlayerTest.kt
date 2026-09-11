package com.voiceaipoc.voice

import java.util.Collections
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
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

        override fun onPlaybackStarted(responseId: UUID) = started.countDown()
        override fun onPlaybackCompleted(responseId: UUID) = completed.countDown()
    }

    private class FakeTrackFactory(private val writeLimit: Int = Int.MAX_VALUE) :
        TtsAudioTrackFactory {
        val tracks = Collections.synchronizedList(mutableListOf<FakeTrack>())

        override fun minBufferSize(sampleRateHz: Int): Int = 4

        override fun create(sampleRateHz: Int, bufferSizeBytes: Int): TtsAudioTrack =
            FakeTrack(writeLimit).also(tracks::add)
    }

    private class FakeTrack(private val writeLimit: Int) : TtsAudioTrack {
        override val isInitialized = true
        var playCalls = 0
        var writtenBytes = 0
        var released = false

        override fun play() {
            playCalls += 1
        }

        override fun write(data: ByteArray, offsetInBytes: Int, sizeInBytes: Int, mode: Int): Int {
            val written = minOf(sizeInBytes, writeLimit)
            writtenBytes += written
            return written
        }

        override fun stop() = Unit
        override fun flush() = Unit
        override fun release() {
            released = true
        }

        override fun underrunCount(): Int = 0
    }
}
