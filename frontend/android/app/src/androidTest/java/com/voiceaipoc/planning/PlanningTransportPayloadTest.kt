package com.voiceaipoc.planning

import androidx.test.ext.junit.runners.AndroidJUnit4
import com.voiceaipoc.auth.AuthTokenStorage
import com.voiceaipoc.auth.StoredAuthTokens
import com.voiceaipoc.voice.VoiceWebSocketTransport
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

/** Runs the production payload parser with Android's real JSON implementation.
 * No account storage, microphone, network, notification or app records are used.
 * These checks are transport evidence, not an end-to-end conversation trial.
 */
@RunWith(AndroidJUnit4::class)
class PlanningTransportPayloadTest {
    @Test
    fun modeAcknowledgementPreservesOwnedStateAndRequestIdentity() {
        val json = JSONObject(
            """{"planning":{"mode":"plan","state_version":2,"active_plan_id":null,
                "available":true,"automatic_actions_available":true},"request_event_id":"request-1"}""",
        )
        val payload = parse("server.planning.state", json)!!
        val state = JSONObject(payload.planningJson!!)
        assertEquals("plan", state.getString("mode"))
        assertEquals(2, state.getInt("state_version"))
        assertEquals("request-1", payload.requestEventId)
    }

    @Test
    fun resumeSnapshotCarriesPlanningState() {
        val payload = parse(
            "server.session.ready",
            JSONObject("""{"planning":{"mode":"normal","state_version":3,"available":true}}"""),
        )!!
        assertEquals("normal", JSONObject(payload.planningJson!!).getString("mode"))
    }

    @Test
    fun multiActionReceiptPreservesCorrectionsAndDuplicateDecisions() {
        val payload = parse("server.planning.actions", JSONObject(
            """{"receipt":{"batch_id":"batch-1","saved_actions":[
                {"id":"task-1","operation":"CREATE_TASK","title":"Report"},
                {"id":"task-2","operation":"UPDATE_TASK","title":"Présentation"}],
                "duplicate_actions":[{"action_id":"action-3","title":"Report","reason":"duplicate"}],
                "failed_actions":[]}}""",
        ))!!
        val receipt = JSONObject(payload.planningActionsJson!!)
        assertEquals(2, receipt.getJSONArray("saved_actions").length())
        assertEquals("UPDATE_TASK", receipt.getJSONArray("saved_actions").getJSONObject(1).getString("operation"))
        assertEquals("Présentation", receipt.getJSONArray("saved_actions").getJSONObject(1).getString("title"))
        assertEquals(1, receipt.getJSONArray("duplicate_actions").length())
    }

    @Test
    fun errorsAndPayloadBoundsAreRetainedWithoutOversizedContent() {
        val payload = parse("server.planning.error", JSONObject()
            .put("code", "planning_state_conflict")
            .put("request_event_id", "x".repeat(129))
            .put("planning", JSONObject().put("oversized", "x".repeat(4097)))
            .put("receipt", JSONObject().put("oversized", "x".repeat(16385))))!!
        assertEquals("planning_state_conflict", payload.errorCode)
        assertNull(payload.requestEventId)
        assertNull(payload.planningJson)
        assertNull(payload.planningActionsJson)
    }

    @Test
    fun planningPayloadDoesNotRemoveNormalAssistantText() {
        val payload = parse("assistant.text.final", JSONObject().put("text", "Your answer."))
        assertNotNull(payload)
        assertEquals("Your answer.", payload!!.text)
        assertNull(payload.planningActionsJson)
        assertNull(parse("unrecognized.event", JSONObject()))
    }

    private fun parse(type: String, json: JSONObject): VoiceWebSocketTransport.ServerEventPayload? {
        val storage = object : AuthTokenStorage {
            override fun save(accessToken: String, refreshToken: String) = Unit
            override fun read(): StoredAuthTokens? = null
            override fun clear() = Unit
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
