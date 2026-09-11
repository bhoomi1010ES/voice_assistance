package com.voiceaipoc.voice

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import java.util.UUID

/** Native PCM sink; audio samples never cross the React Native bridge. */
internal class TtsAudioPlayer {
    private var audioTrack: AudioTrack? = null
    private var activeResponseId: UUID? = null
    private var activeSampleRateHz: Int? = null

    @Synchronized
    fun start(responseId: UUID, sampleRateHz: Int) {
        if (activeResponseId == responseId && activeSampleRateHz == sampleRateHz) return
        stopLocked()
        val minBuffer = AudioTrack.getMinBufferSize(
            sampleRateHz,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) return
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
            .setBufferSizeInBytes(maxOf(minBuffer, sampleRateHz / 5))
            .build()
        if (track.state != AudioTrack.STATE_INITIALIZED) {
            track.release()
            return
        }
        track.play()
        audioTrack = track
        activeResponseId = responseId
        activeSampleRateHz = sampleRateHz
    }

    @Synchronized
    fun write(responseId: UUID, payload: ByteArray) {
        if (payload.isEmpty() || activeResponseId != responseId) return
        audioTrack?.write(payload, 0, payload.size, AudioTrack.WRITE_BLOCKING)
    }

    @Synchronized
    fun finish(responseId: UUID) {
        if (activeResponseId != responseId) return
        stopLocked()
    }

    @Synchronized
    fun cancel(responseId: UUID? = null) {
        if (responseId == null || activeResponseId == responseId) stopLocked()
    }

    private fun stopLocked() {
        audioTrack?.let { track ->
            runCatching { track.stop() }
            runCatching { track.flush() }
            track.release()
        }
        audioTrack = null
        activeResponseId = null
        activeSampleRateHz = null
    }
}
