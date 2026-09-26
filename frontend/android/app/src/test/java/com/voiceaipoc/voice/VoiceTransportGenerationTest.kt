package com.voiceaipoc.voice

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceTransportGenerationTest {
    @Test
    fun onlyCurrentSocketAndGenerationMayMutateTransportState() {
        val activeSocket = Any()
        val staleSocket = Any()

        assertTrue(acceptsVoiceTransportCallback(activeSocket, activeSocket, 4L, 4L))
        assertFalse(acceptsVoiceTransportCallback(activeSocket, staleSocket, 4L, 4L))
        assertFalse(acceptsVoiceTransportCallback(activeSocket, activeSocket, 5L, 4L))
    }
}
