package com.voiceaipoc.phase3

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.voiceaipoc.auth.AuthTokenStorage
import com.voiceaipoc.auth.SecureTokenStorage
import com.voiceaipoc.auth.StoredAuthTokens
import com.voiceaipoc.voice.VoiceWebSocketTransport
import java.util.Collections
import java.util.concurrent.TimeUnit
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Test-only physical-device exercise of the production native transport.
 *
 * The PCM is read from the physical device microphone into one reusable,
 * bounded frame buffer and immediately offered to the production transport.
 * No microphone data is retained. This test-only harness validates the real
 * Android microphone-to-transport path without adding a production automation
 * path.
 */
@RunWith(AndroidJUnit4::class)
class PhysicalVoiceGatewayTest {
    @Test
    fun nativeTransportCompletesTenConsecutiveTurns() {
        val eventLog = EventLog()
        val transport = createTransport(eventLog)
        val recorder = createMicrophone()

        try {
            assertTrue(
                "native transport did not start connect",
                transport.connect("ws://127.0.0.1:8000/v1/voice").succeeded,
            )
            eventLog.awaitState(VoiceWebSocketTransport.State.CONNECTED, 5)
            assertTrue("voice session did not become ready", transport.startSession().succeeded)
            val session = eventLog.await("server.session.ready", 5)
            assertNotNull(session.sessionId)
            recorder.startRecording()
            assertEquals(AudioRecord.RECORDSTATE_RECORDING, recorder.recordingState)

            repeat(TURN_COUNT) { turnIndex ->
                assertTrue("turn start was rejected", transport.startTurn().succeeded)
                val ready = eventLog.awaitCount("server.turn.ready", turnIndex + 1, 5)
                val captured = captureFrames(recorder, transport, FRAMES_PER_TURN)
                assertTrue("turn commit was rejected", transport.commitAudio(100).succeeded)
                val completed = eventLog.awaitCount("server.turn.completed", turnIndex + 1, 5)
                assertEquals(ready.turnId, completed.turnId)
                assertEquals(ready.responseId, completed.responseId)
                assertEquals(FRAMES_PER_TURN, captured)
                val queue = transport.getStatus()
                Log.i(
                    TAG,
                    "turn=${turnIndex + 1} session=${session.sessionId} turn=${completed.turnId} " +
                        "response=${completed.responseId} frames=$captured bytes=${captured * FRAME_BYTES} " +
                        "first_sequence=0 last_sequence=${captured - 1} result=committed " +
                        "queue_high_water=${queue.queueHighWaterMark} drops=${queue.droppedFrames} " +
                        "invalid_frames=${queue.invalidFrames} websocket_errors=${queue.websocketErrorCount}",
                )
            }

            assertTrue("server errors were reported", eventLog.errors.isEmpty())
            assertTrue("session end was rejected", transport.endSession().succeeded)
            eventLog.await("server.session.ended", 5)
        } finally {
            if (recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) recorder.stop()
            recorder.release()
            transport.shutdown()
        }
    }

    @Test
    fun physicalMicrophoneCancellationDoesNotLeakIntoNextTurn() {
        val eventLog = EventLog()
        val transport = createTransport(eventLog)
        val recorder = createMicrophone()
        try {
            connectAndStartSession(transport, eventLog)
            recorder.startRecording()
            assertEquals(AudioRecord.RECORDSTATE_RECORDING, recorder.recordingState)

            assertTrue(transport.startTurn().succeeded)
            val cancelledReady = eventLog.await("server.turn.ready", 5)
            captureFrames(recorder, transport, 1)
            assertTrue("cancel request was rejected", transport.cancelResponse("physical_test_cancel").succeeded)
            val cancelled = eventLog.await("response.cancelled", 5)
            assertEquals(cancelledReady.turnId, cancelled.turnId)
            assertEquals(cancelledReady.responseId, cancelled.responseId)

            assertTrue("next turn start was rejected", transport.startTurn().succeeded)
            val nextReady = eventLog.awaitCount("server.turn.ready", 2, 5)
            val captured = captureFrames(recorder, transport, FRAMES_PER_TURN)
            assertTrue("next turn commit was rejected", transport.commitAudio(100).succeeded)
            val completed = eventLog.await("server.turn.completed", 5)
            assertEquals(nextReady.turnId, completed.turnId)
            assertEquals(nextReady.responseId, completed.responseId)
            assertTrue("cancelled response was reused", cancelled.responseId != completed.responseId)
            assertEquals(FRAMES_PER_TURN, captured)
            assertTrue("server errors were reported", eventLog.errors.isEmpty())
            assertTrue(transport.endSession().succeeded)
            eventLog.await("server.session.ended", 5)
        } finally {
            if (recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) recorder.stop()
            recorder.release()
            transport.shutdown()
        }
    }

    @Test
    fun physicalMicrophoneDisconnectAndReconnectCompletesTurn() {
        val firstLog = EventLog()
        val firstTransport = createTransport(firstLog)
        val firstRecorder = createMicrophone()
        try {
            connectAndStartSession(firstTransport, firstLog)
            firstRecorder.startRecording()
            assertEquals(AudioRecord.RECORDSTATE_RECORDING, firstRecorder.recordingState)
            assertTrue(firstTransport.startTurn().succeeded)
            firstLog.await("server.turn.ready", 5)
            val firstCaptured = captureFrames(firstRecorder, firstTransport, 2)
            assertTrue(firstTransport.commitAudio(100).succeeded)
            firstLog.await("server.turn.completed", 5)
            assertEquals(2, firstCaptured)
        } finally {
            if (firstRecorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) firstRecorder.stop()
            firstRecorder.release()
            firstTransport.disconnect()
            Thread.sleep(1_000)
            firstTransport.shutdown()
        }

        val secondLog = EventLog()
        val secondTransport = createTransport(secondLog)
        val secondRecorder = createMicrophone()
        try {
            connectAndStartSession(secondTransport, secondLog)
            secondRecorder.startRecording()
            assertEquals(AudioRecord.RECORDSTATE_RECORDING, secondRecorder.recordingState)
            assertTrue(secondTransport.startTurn().succeeded)
            val ready = secondLog.await("server.turn.ready", 5)
            val captured = captureFrames(secondRecorder, secondTransport, 2)
            assertTrue(secondTransport.commitAudio(100).succeeded)
            val completed = secondLog.await("server.turn.completed", 5)
            assertEquals(ready.turnId, completed.turnId)
            assertEquals(ready.responseId, completed.responseId)
            assertEquals(2, captured)
            assertTrue("server errors were reported", secondLog.errors.isEmpty())
            assertTrue(secondTransport.endSession().succeeded)
            secondLog.await("server.session.ended", 5)
        } finally {
            if (secondRecorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) secondRecorder.stop()
            secondRecorder.release()
            secondTransport.shutdown()
        }
    }

    @Test
    fun physicalHeartbeatIsReceivedAndStopsAfterDisconnect() {
        val eventLog = EventLog()
        val transport = createTransport(eventLog)
        try {
            connectAndStartSession(transport, eventLog)
            eventLog.await("server.pong", 20)
            val pongsBeforeDisconnect = eventLog.count("server.pong")

            transport.disconnect()
            eventLog.awaitState(VoiceWebSocketTransport.State.DISCONNECTED, 5)
            Thread.sleep(2_500)

            assertEquals(
                "heartbeat events continued after disconnect",
                pongsBeforeDisconnect,
                eventLog.count("server.pong"),
            )
        } finally {
            transport.shutdown()
        }
    }

    @Test
    fun physicalInvalidCredentialIsRejected() {
        val eventLog = EventLog()
        val transport = VoiceWebSocketTransport(
            tokenStorage = StaticTokenStorage("invalid-physical-test-token"),
            listener = eventLog,
        )
        try {
            assertTrue(transport.connect(GATEWAY_URL).succeeded)
            eventLog.awaitState(VoiceWebSocketTransport.State.ERROR, 5)
            assertTrue("invalid credential did not produce a transport error", eventLog.errors.isNotEmpty())
        } finally {
            transport.shutdown()
        }
    }

    @Test
    fun physicalMissingCredentialIsRejectedBeforeHandshake() {
        val transport = VoiceWebSocketTransport(
            tokenStorage = StaticTokenStorage(null),
            listener = EventLog(),
        )
        try {
            val result = transport.connect(GATEWAY_URL)
            assertFalse(result.succeeded)
            assertEquals("E_VOICE_AUTH", result.errorCode)
        } finally {
            transport.shutdown()
        }
    }

    private fun connectAndStartSession(
        transport: VoiceWebSocketTransport,
        eventLog: EventLog,
    ) {
        assertTrue("native transport did not start connect", transport.connect(GATEWAY_URL).succeeded)
        eventLog.awaitState(VoiceWebSocketTransport.State.CONNECTED, 5)
        assertTrue("voice session start was rejected", transport.startSession().succeeded)
        eventLog.await("server.session.ready", 5)
    }

    private fun captureFrames(
        recorder: AudioRecord,
        transport: VoiceWebSocketTransport,
        frameCount: Int,
    ): Int {
        val buffer = ShortArray(FRAME_SAMPLES)
        repeat(frameCount) {
            val read = recorder.read(buffer, 0, FRAME_SAMPLES)
            assertEquals("physical microphone did not return one PCM16 frame", FRAME_SAMPLES, read)
            assertTrue("native PCM frame was not accepted", transport.offerPcmFrame(buffer, read))
        }
        return frameCount
    }

    private fun createTransport(eventLog: EventLog) = VoiceWebSocketTransport(
        tokenStorage = SecureTokenStorage(
            InstrumentationRegistry.getInstrumentation().targetContext,
        ),
        listener = eventLog,
    )

    private fun createMicrophone(): AudioRecord {
        val minimum = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        assertTrue("physical microphone buffer size unavailable", minimum > 0)
        return AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minimum, FRAME_BYTES * 4),
        ).also {
            assertEquals(AudioRecord.STATE_INITIALIZED, it.state)
        }
    }

    private class EventLog : VoiceWebSocketTransport.Listener {
        val events = Collections.synchronizedList(mutableListOf<ServerEvent>())
        val errors = Collections.synchronizedList(mutableListOf<String>())
        val states = Collections.synchronizedList(mutableListOf<VoiceWebSocketTransport.State>())
        private val lock = Any()

        override fun onStatus(status: VoiceWebSocketTransport.Status) {
            synchronized(lock) {
                states += status.state
            }
            if (status.state == VoiceWebSocketTransport.State.ERROR) {
                status.lastError?.let(errors::add)
            }
        }

        override fun onServerEvent(
            eventType: String,
            sessionId: String?,
            turnId: String?,
            responseId: String?,
        ) {
            synchronized(lock) {
                events += ServerEvent(eventType, sessionId, turnId, responseId)
            }
        }

        fun await(type: String, timeoutSeconds: Long): ServerEvent = awaitCount(type, 1, timeoutSeconds)

        fun count(type: String): Int = synchronized(lock) { events.count { it.type == type } }

        fun awaitState(state: VoiceWebSocketTransport.State, timeoutSeconds: Long) {
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(timeoutSeconds)
            while (true) {
                synchronized(lock) {
                    if (states.contains(state)) return
                }
                val remaining = deadline - System.nanoTime()
                assertTrue(
                    "did not reach state $state; states=$states errors=$errors",
                    remaining > 0,
                )
                Thread.sleep(minOf(25L, TimeUnit.NANOSECONDS.toMillis(remaining).coerceAtLeast(1L)))
            }
        }

        fun awaitCount(type: String, occurrence: Int, timeoutSeconds: Long): ServerEvent {
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(timeoutSeconds)
            while (true) {
                synchronized(lock) {
                    val matching = events.filter { it.type == type }
                    if (matching.size >= occurrence) return matching[occurrence - 1]
                }
                val remaining = deadline - System.nanoTime()
                assertTrue("did not receive $type", remaining > 0)
                Thread.sleep(minOf(25L, TimeUnit.NANOSECONDS.toMillis(remaining).coerceAtLeast(1L)))
            }
        }
    }

    private data class ServerEvent(
        val type: String,
        val sessionId: String?,
        val turnId: String?,
        val responseId: String?,
    )

    private class StaticTokenStorage(private val accessToken: String?) : AuthTokenStorage {
        override fun save(accessToken: String, refreshToken: String) = Unit

        override fun read(): StoredAuthTokens? = accessToken?.let {
            StoredAuthTokens(it, "physical-test-refresh-token")
        }

        override fun clear() = Unit
    }

    companion object {
        private const val TAG = "Phase3Physical"
        private const val GATEWAY_URL = "ws://127.0.0.1:8000/v1/voice"
        private const val TURN_COUNT = 10
        private const val FRAMES_PER_TURN = 5
        private const val FRAME_SAMPLES = 320
        private const val FRAME_BYTES = FRAME_SAMPLES * 2
        private const val SAMPLE_RATE = 16_000
    }
}
