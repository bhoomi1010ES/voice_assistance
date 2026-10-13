package com.voiceaipoc.voice

import androidx.test.ext.junit.runners.AndroidJUnit4
import com.voiceaipoc.auth.AuthTokenStorage
import com.voiceaipoc.auth.StoredAuthTokens
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

/** Checks the production parser with Android JSON, without account or microphone access. */
@RunWith(AndroidJUnit4::class)
class StopListeningTransportPayloadTest {
    @Test
    fun stopAcknowledgementPreservesControlRouteAndText() {
        val payload = parse(
            "assistant.text.final",
            JSONObject("""{"route":"CONTROL","text":"Stopped listening."}"""),
        )!!
        assertEquals("CONTROL", payload.route)
        assertEquals("Stopped listening.", payload.text)
    }

    @Test
    fun completionPreservesStopSignalAndOrdinaryCompletionFalse() {
        val stopped = parse(
            "server.turn.completed",
            JSONObject("""{"suppress_auto_listen":true}"""),
        )!!
        assertEquals(true, stopped.suppressAutoListen)
        val ordinary = parse(
            "server.turn.completed",
            JSONObject("""{"suppress_auto_listen":false}"""),
        )!!
        assertEquals(false, ordinary.suppressAutoListen)
    }

    @Test
    fun missingOrInvalidControlMetadataDoesNotManufactureStopSignal() {
        val missing = parse("server.turn.completed", JSONObject())!!
        assertNull(missing.route)
        assertNull(missing.suppressAutoListen)
        val invalid = parse(
            "assistant.text.final",
            JSONObject().put("route", "x".repeat(33)).put("suppress_auto_listen", "true"),
        )!!
        assertNull(invalid.route)
        assertNull(invalid.suppressAutoListen)
        assertNull(parse("unrecognized.event", JSONObject()))
    }

    private fun parse(type: String, json: JSONObject): VoiceWebSocketTransport.ServerEventPayload? {
        val storage = object : AuthTokenStorage {
            override fun save(accessToken: String, refreshToken: String) = Unit
            override fun read(): StoredAuthTokens? = null
            override fun clear() = Unit
            override fun saveDeviceId(deviceId: String) = Unit
            override fun readDeviceId(): String? = null
            override fun clearDeviceId() = Unit
        }
        val listener = object : VoiceWebSocketTransport.Listener {
            override fun onStatus(status: VoiceWebSocketTransport.Status) = Unit
            override fun onServerEvent(
                eventType: String, sessionId: String?, turnId: String?, responseId: String?,
            ) = Unit
        }
        val transport = VoiceWebSocketTransport(storage, listener)
        try {
            val method = VoiceWebSocketTransport::class.java.getDeclaredMethod(
                "extractServerEventPayload", String::class.java, JSONObject::class.java,
            )
            method.isAccessible = true
            return method.invoke(transport, type, json) as VoiceWebSocketTransport.ServerEventPayload?
        } finally {
            transport.shutdown()
        }
    }
}
