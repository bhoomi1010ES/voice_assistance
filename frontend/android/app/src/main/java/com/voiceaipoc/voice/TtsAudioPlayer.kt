package com.voiceaipoc.voice

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import java.util.ArrayDeque
import java.util.UUID
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

internal interface TtsAudioTrack {
    val isInitialized: Boolean
    fun play()
    fun write(data: ByteArray, offsetInBytes: Int, sizeInBytes: Int, mode: Int): Int
    fun stop()
    fun flush()
    fun release()
    fun underrunCount(): Int
}

internal interface TtsAudioTrackFactory {
    fun minBufferSize(sampleRateHz: Int): Int
    fun create(sampleRateHz: Int, bufferSizeBytes: Int): TtsAudioTrack
}

private object AndroidTtsAudioTrackFactory : TtsAudioTrackFactory {
    override fun minBufferSize(sampleRateHz: Int): Int = AudioTrack.getMinBufferSize(
        sampleRateHz,
        AudioFormat.CHANNEL_OUT_MONO,
        AudioFormat.ENCODING_PCM_16BIT,
    )

    override fun create(sampleRateHz: Int, bufferSizeBytes: Int): TtsAudioTrack {
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRateHz)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(bufferSizeBytes)
            .build()
        return AndroidTtsAudioTrack(track)
    }
}

private class AndroidTtsAudioTrack(private val delegate: AudioTrack) : TtsAudioTrack {
    override val isInitialized: Boolean
        get() = delegate.state == AudioTrack.STATE_INITIALIZED

    override fun play() = delegate.play()
    override fun write(data: ByteArray, offsetInBytes: Int, sizeInBytes: Int, mode: Int): Int =
        delegate.write(data, offsetInBytes, sizeInBytes, mode)
    override fun stop() = delegate.stop()
    override fun flush() = delegate.flush()
    override fun release() = delegate.release()
    override fun underrunCount(): Int = delegate.underrunCount
}

/** Queued PCM16 sink for one complete TTS response. */
internal class TtsAudioPlayer(
    private val trackFactory: TtsAudioTrackFactory = AndroidTtsAudioTrackFactory,
    private val listener: Listener = Listener.NONE,
    private val startupPrebufferBytes: Int = STARTUP_PREBUFFER_BYTES,
    private val maxQueueBytes: Int = MAX_QUEUE_BYTES,
    private val writerExecutor: ExecutorService = Executors.newSingleThreadExecutor {
        Thread(it, "VoiceAI-TtsAudioWriter").apply { isDaemon = true }
    },
) {
    data class PlaybackSummary(
        val framesQueued: Int,
        val framesWritten: Int,
        val pcmBytesQueued: Long,
        val pcmBytesWritten: Long,
        val partialWrites: Int,
        val writeErrors: Int,
        val underrunDelta: Int,
    )

    interface Listener {
        fun onPlaybackStarted(responseId: UUID) = Unit
        fun onPlaybackCompleted(responseId: UUID) = Unit
        fun onPlaybackStopped(responseId: UUID) = Unit
        fun onPlaybackError(responseId: UUID, errorCode: String) = Unit
        fun onPcmEnqueued(responseId: UUID, sequence: Long, bytes: Int, queuedBytes: Int) = Unit
        fun onPcmWrite(
            responseId: UUID,
            sequence: Long,
            requestedBytes: Int,
            writtenBytes: Int,
            queueBytesRemaining: Int,
        ) = Unit
        fun onPlaybackSummary(responseId: UUID, summary: PlaybackSummary) = Unit

        companion object {
            val NONE: Listener = object : Listener {}
        }
    }

    private class Session(
        val responseId: UUID,
        val track: TtsAudioTrack,
        val generation: Long,
    ) {
        var playbackStarted = false
        var finishRequested = false
        var queuedBytes = 0
        var underrunsBefore = 0
        var framesQueued = 0
        var framesWritten = 0
        var pcmBytesQueued = 0L
        var pcmBytesWritten = 0L
        var partialWrites = 0
        var writeErrors = 0
        var firstPcmWriteLogged = false
        var writeInProgress = false
        var cancelRequested = false
        var resourcesStopped = false
    }

    private data class Chunk(val sequence: Long, val payload: ByteArray)

    private val lock = Object()
    private val queue = ArrayDeque<Chunk>()
    private var generation = 0L
    private var active: Session? = null

    fun start(responseId: UUID, sampleRateHz: Int): Boolean {
        if (sampleRateHz != TTS_SAMPLE_RATE_HZ) {
            Log.e(TAG, "TTS_PLAYER_REJECTED sample_rate=$sampleRateHz expected=$TTS_SAMPLE_RATE_HZ")
            return false
        }
        var stoppedResponse: UUID? = null
        synchronized(lock) {
            if (active?.responseId == responseId) return true
            stoppedResponse = stopLocked(notifyStopped = true)
            val minBuffer = trackFactory.minBufferSize(sampleRateHz)
            if (minBuffer <= 0) {
                Log.e(TAG, "TTS_PLAYER_REJECTED reason=invalid_min_buffer")
                return false
            }
            val bufferSize = maxOf(minBuffer, startupPrebufferBytes)
            val track = runCatching { trackFactory.create(sampleRateHz, bufferSize) }
                .getOrElse {
                    Log.e(TAG, "TTS_PLAYER_REJECTED reason=track_create_failed")
                    return false
                }
            if (!track.isInitialized) {
                track.release()
                Log.e(TAG, "TTS_PLAYER_REJECTED reason=track_not_initialized")
                return false
            }
            generation += 1
            val session = Session(responseId, track, generation)
            active = session
            queue.clear()
            Log.i(
                TAG,
                "TTS_PLAYER_CREATED sample_rate=$sampleRateHz channels=1 " +
                    "encoding=PCM16 buffer_size_bytes=$bufferSize " +
                    "prebuffer_bytes=$startupPrebufferBytes",
            )
            writerExecutor.execute { runWriter(session) }
            lock.notifyAll()
        }
        stoppedResponse?.let(listener::onPlaybackStopped)
        return true
    }

    /** Enqueue only; this method never writes to AudioTrack. */
    fun write(responseId: UUID, payload: ByteArray): Boolean =
        write(responseId, -1L, payload)

    /** Enqueue only; AudioTrack writes happen on the dedicated writer. */
    fun write(responseId: UUID, sequence: Long, payload: ByteArray): Boolean {
        if (payload.isEmpty()) return true
        if (payload.size % BYTES_PER_SAMPLE != 0) {
            Log.e(TAG, "TTS_PCM_REJECTED reason=odd_pcm_payload bytes=${payload.size}")
            return false
        }
        synchronized(lock) {
            val session = active?.takeIf { it.responseId == responseId } ?: return false
            if (session.queuedBytes + payload.size > maxQueueBytes) {
                Log.e(
                    TAG,
                    "TTS_QUEUE_FULL queued_bytes=${session.queuedBytes} incoming_bytes=${payload.size}",
                )
                return false
            }
            val copy = payload.copyOf()
            queue.addLast(Chunk(sequence, copy))
            session.queuedBytes += copy.size
            session.framesQueued += 1
            session.pcmBytesQueued += copy.size
            Log.i(
                TAG,
                "TTS_PREBUFFER queued_bytes=${session.queuedBytes} " +
                    "queued_duration_ms=${queuedDurationMs(session.queuedBytes)}",
            )
            lock.notifyAll()
            listener.onPcmEnqueued(responseId, sequence, copy.size, session.queuedBytes)
            return true
        }
    }

    fun isActive(responseId: UUID): Boolean = synchronized(lock) {
        active?.responseId == responseId
    }

    fun finish(responseId: UUID): Boolean {
        synchronized(lock) {
            val session = active?.takeIf { it.responseId == responseId } ?: return false
            session.finishRequested = true
            Log.i(
                TAG,
                "TTS_END_RECEIVED response_id=$responseId elapsedMs=${android.os.SystemClock.elapsedRealtime()}",
            )
            lock.notifyAll()
            return true
        }
    }

    fun cancel(responseId: UUID? = null): Boolean {
        var stoppedResponse: UUID? = null
        var wasActive = false
        synchronized(lock) {
            val current = active
            if (responseId != null && current?.responseId != responseId) return false
            wasActive = current != null
            stoppedResponse = stopLocked(notifyStopped = true)
            lock.notifyAll()
        }
        stoppedResponse?.let(listener::onPlaybackStopped)
        return wasActive
    }

    fun shutdown() {
        cancel()
        writerExecutor.shutdownNow()
        writerExecutor.awaitTermination(1, TimeUnit.SECONDS)
    }

    private fun runWriter(session: Session) {
        try {
            while (true) {
                var chunk: Chunk? = null
                var notifyStarted = false
                var shouldComplete = false
                synchronized(lock) {
                    while (active === session && queue.isEmpty() && !session.finishRequested) {
                        lock.wait()
                    }
                    if (active !== session) return
                    if (!session.playbackStarted) {
                        if (session.queuedBytes < startupPrebufferBytes && !session.finishRequested) {
                            continue
                        }
                        if (session.queuedBytes == 0) {
                            shouldComplete = true
                        } else {
                            session.underrunsBefore = session.track.underrunCount()
                            session.track.play()
                            session.playbackStarted = true
                            Log.i(
                                TAG,
                                "TTS_PREBUFFER_READY response_id=${session.responseId} " +
                                    "queued_bytes=${session.queuedBytes} " +
                                    "queued_duration_ms=${queuedDurationMs(session.queuedBytes)} " +
                                    "elapsedMs=${android.os.SystemClock.elapsedRealtime()}",
                            )
                            Log.i(
                                TAG,
                                "TTS_PLAY_STARTED response_id=${session.responseId} " +
                                    "elapsedMs=${android.os.SystemClock.elapsedRealtime()}",
                            )
                            notifyStarted = true
                        }
                    }
                    if (!shouldComplete && queue.isNotEmpty()) {
                        chunk = queue.removeFirst()
                    } else if (!shouldComplete && session.finishRequested) {
                        shouldComplete = true
                    }
                }
                if (notifyStarted) listener.onPlaybackStarted(session.responseId)
                if (shouldComplete) {
                    complete(session)
                    return
                }
                if (chunk != null) writeFully(session, chunk)
            }
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        } catch (error: Throwable) {
            fail(session, "writer_exception_${error::class.java.simpleName}")
        }
    }

    private fun writeFully(session: Session, chunk: Chunk) {
        var offset = 0
        while (offset < chunk.payload.size) {
            val track = synchronized(lock) {
                if (active !== session) return
                session.writeInProgress = true
                session.track
            }
            val written = runCatching {
                track.write(
                    chunk.payload,
                    offset,
                    chunk.payload.size - offset,
                    AudioTrack.WRITE_BLOCKING,
                )
            }.getOrElse {
                synchronized(lock) {
                    session.writeInProgress = false
                }
                fail(session, "write_exception_${it::class.java.simpleName}")
                return
            }
            synchronized(lock) { session.writeInProgress = false }
            if (written < 0) {
                Log.e(TAG, "TTS_PCM_WRITE_ERROR code=$written")
                fail(session, "write_error_$written")
                return
            }
            if (written == 0) {
                Thread.sleep(2)
                continue
            }
            offset += written
            var queueBytesRemaining = 0
            var cancelled = false
            synchronized(lock) {
                session.queuedBytes = (session.queuedBytes - written).coerceAtLeast(0)
                session.pcmBytesWritten += written
                if (offset == chunk.payload.size) session.framesWritten += 1
                if (offset < chunk.payload.size) session.partialWrites += 1
                queueBytesRemaining = session.queuedBytes
                cancelled = session.cancelRequested
            }
            Log.i(
                TAG,
                    "TTS_PCM_WRITE response_id=${session.responseId} seq=${chunk.sequence} " +
                        "requested_bytes=${chunk.payload.size - offset + written} " +
                        "written_bytes=$written queue_bytes_remaining=$queueBytesRemaining " +
                        "elapsedMs=${android.os.SystemClock.elapsedRealtime()}",
            )
            if (!session.firstPcmWriteLogged) {
                session.firstPcmWriteLogged = true
                Log.i(
                    TAG,
                    "TTS_FIRST_PCM_WRITE response_id=${session.responseId} seq=${chunk.sequence} " +
                        "bytes=$written elapsedMs=${android.os.SystemClock.elapsedRealtime()}",
                )
            }
            listener.onPcmWrite(
                session.responseId,
                chunk.sequence,
                chunk.payload.size - offset + written,
                written,
                queueBytesRemaining,
            )
            if (cancelled) {
                synchronized(lock) { stopSessionResourcesLocked(session) }
                return
            }
        }
    }

    private fun complete(session: Session) {
        var notify = false
        var summary: PlaybackSummary? = null
        synchronized(lock) {
            if (active !== session) return
            val underrunsAfter = session.track.underrunCount()
            val underrunDelta = (underrunsAfter - session.underrunsBefore).coerceAtLeast(0)
            if (underrunDelta > 0) {
                Log.w(TAG, "TTS_UNDERRUN count=$underrunsAfter delta=$underrunDelta")
            } else {
                Log.i(TAG, "TTS_UNDERRUN count=$underrunsAfter delta=0")
            }
            summary = summaryLocked(session, underrunDelta)
            stopSessionResourcesLocked(session)
            notify = session.playbackStarted
        }
        if (notify) {
            Log.i(TAG, "TTS_PLAYBACK_COMPLETED response_id=${session.responseId}")
            listener.onPlaybackSummary(session.responseId, summary!!)
            listener.onPlaybackCompleted(session.responseId)
        }
    }

    private fun fail(session: Session, errorCode: String) {
        var notify = false
        synchronized(lock) {
            if (active !== session) return
            session.writeErrors += 1
            stopSessionResourcesLocked(session)
            notify = true
        }
        if (notify) {
            listener.onPlaybackSummary(session.responseId, summary(session))
            listener.onPlaybackError(session.responseId, errorCode)
        }
    }

    private fun stopLocked(notifyStopped: Boolean): UUID? {
        val session = active ?: return null
        generation += 1
        stopSessionResourcesLocked(session)
        return session.responseId.takeIf { notifyStopped && session.playbackStarted }
    }

    private fun stopSessionResourcesLocked(session: Session) {
        if (session.resourcesStopped) return
        if (session.writeInProgress) {
            session.cancelRequested = true
            return
        }
        session.resourcesStopped = true
        if (active === session) active = null
        queue.clear()
        session.queuedBytes = 0
        runCatching { session.track.stop() }
        runCatching { session.track.flush() }
        runCatching { session.track.release() }
    }

    private fun summary(session: Session): PlaybackSummary = synchronized(lock) {
        summaryLocked(session, underrunDelta = 0)
    }

    private fun summaryLocked(session: Session, underrunDelta: Int): PlaybackSummary =
        PlaybackSummary(
            framesQueued = session.framesQueued,
            framesWritten = session.framesWritten,
            pcmBytesQueued = session.pcmBytesQueued,
            pcmBytesWritten = session.pcmBytesWritten,
            partialWrites = session.partialWrites,
            writeErrors = session.writeErrors,
            underrunDelta = underrunDelta,
        )

    private fun queuedDurationMs(bytes: Int): Int =
        (bytes * 1_000L / (TTS_SAMPLE_RATE_HZ * BYTES_PER_SAMPLE)).toInt()

    private companion object {
        const val TAG = "VoiceAI-TTS"
        const val TTS_SAMPLE_RATE_HZ = 24_000
        const val BYTES_PER_SAMPLE = 2
        const val STARTUP_PREBUFFER_BYTES = TTS_SAMPLE_RATE_HZ * BYTES_PER_SAMPLE * 200 / 1_000
        const val MAX_QUEUE_BYTES = TTS_SAMPLE_RATE_HZ * BYTES_PER_SAMPLE * 2
    }
}
