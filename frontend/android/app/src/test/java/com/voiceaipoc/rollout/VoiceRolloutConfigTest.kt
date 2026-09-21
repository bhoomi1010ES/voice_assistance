package com.voiceaipoc.rollout

import com.voiceaipoc.audio.AudioConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceRolloutConfigTest {
    @Test
    fun defaultPathKeepsNativeDetectorAuthoritative() {
        val config = VoiceRolloutConfig()

        assertTrue(config.nativeBargeInAuthoritative)
        assertFalse(config.nativeBargeInShadowMode)
        assertEquals("NATIVE_AUTHORITATIVE", config.toMap()["rolloutMode"])
    }

    @Test
    fun legacyFlagEnablesShadowModeWithoutNativeLocalStop() {
        val config = VoiceRolloutConfig(
            nativeBargeInDetectorEnabled = true,
            legacyJsBargeInDetectorEnabled = true,
        )

        assertFalse(config.nativeBargeInAuthoritative)
        assertTrue(config.nativeBargeInShadowMode)
        assertEquals("NATIVE_SHADOW_LEGACY_AUTHORITATIVE", config.toMap()["rolloutMode"])
    }

    @Test
    fun bothDetectorsDisabledIsDiagnosticsOnly() {
        val config = VoiceRolloutConfig(
            nativeBargeInDetectorEnabled = false,
            legacyJsBargeInDetectorEnabled = false,
        )

        assertFalse(config.automaticLoudspeakerBargeInEnabled)
        assertEquals("DIAGNOSTICS_ONLY", config.toMap()["rolloutMode"])
    }

    @Test
    fun softwareAecModeIsStrictlyValidated() {
        assertEquals(
            AudioConfig.SoftwareAecMode.WEBRTC_AEC3,
            VoiceRolloutConfig.parseSoftwareAecMode(" webrtc_aec3 "),
        )
        var failed = false
        try {
            VoiceRolloutConfig.parseSoftwareAecMode("unsupported")
        } catch (exception: IllegalStateException) {
            failed = true
        }
        assertTrue(failed)
    }
}
