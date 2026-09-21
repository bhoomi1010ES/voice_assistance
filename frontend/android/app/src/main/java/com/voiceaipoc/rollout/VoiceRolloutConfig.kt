package com.voiceaipoc.rollout

import com.voiceaipoc.BuildConfig
import com.voiceaipoc.audio.AudioConfig

/**
 * Release-controlled switches for the Phase 9 rollout.
 *
 * Both native and JavaScript detector flags may be enabled during the shadow
 * phase. Native decisions are authoritative only when the native detector is
 * enabled and the legacy JavaScript detector is disabled.
 */
data class VoiceRolloutConfig(
    val duplexCommunicationRouteEnabled: Boolean = true,
    val presentationTimedReferenceEnabled: Boolean = true,
    val nativeBargeInDetectorEnabled: Boolean = true,
    val legacyJsBargeInDetectorEnabled: Boolean = false,
    val platformAecEnabled: Boolean = true,
    val platformNsEnabled: Boolean = true,
    val softwareAecMode: AudioConfig.SoftwareAecMode = AudioConfig.SoftwareAecMode.PLATFORM,
    val earlyBargeInEnabled: Boolean = false,
) {
    val nativeBargeInAuthoritative: Boolean
        get() = nativeBargeInDetectorEnabled && !legacyJsBargeInDetectorEnabled

    val nativeBargeInShadowMode: Boolean
        get() = nativeBargeInDetectorEnabled && legacyJsBargeInDetectorEnabled

    val automaticLoudspeakerBargeInEnabled: Boolean
        get() = nativeBargeInDetectorEnabled || legacyJsBargeInDetectorEnabled

    fun toMap(): Map<String, Any> = mapOf(
        "duplexCommunicationRouteEnabled" to duplexCommunicationRouteEnabled,
        "presentationTimedReferenceEnabled" to presentationTimedReferenceEnabled,
        "nativeBargeInDetectorEnabled" to nativeBargeInDetectorEnabled,
        "legacyJsBargeInDetectorEnabled" to legacyJsBargeInDetectorEnabled,
        "platformAecEnabled" to platformAecEnabled,
        "platformNsEnabled" to platformNsEnabled,
        "softwareAecMode" to softwareAecMode.name,
        "earlyBargeInEnabled" to earlyBargeInEnabled,
        "nativeBargeInAuthoritative" to nativeBargeInAuthoritative,
        "nativeBargeInShadowMode" to nativeBargeInShadowMode,
        "automaticLoudspeakerBargeInEnabled" to automaticLoudspeakerBargeInEnabled,
        "rolloutMode" to when {
            nativeBargeInAuthoritative -> "NATIVE_AUTHORITATIVE"
            nativeBargeInShadowMode -> "NATIVE_SHADOW_LEGACY_AUTHORITATIVE"
            legacyJsBargeInDetectorEnabled -> "LEGACY_JS_AUTHORITATIVE"
            else -> "DIAGNOSTICS_ONLY"
        },
    )

    companion object {
        fun fromBuildConfig(): VoiceRolloutConfig = fromValues(
            duplexCommunicationRouteEnabled = BuildConfig.VOICE_DUPLEX_COMMUNICATION_ROUTE_ENABLED,
            presentationTimedReferenceEnabled = BuildConfig.VOICE_PRESENTATION_TIMED_REFERENCE_ENABLED,
            nativeBargeInDetectorEnabled = BuildConfig.VOICE_NATIVE_BARGE_IN_DETECTOR_ENABLED,
            legacyJsBargeInDetectorEnabled = BuildConfig.VOICE_LEGACY_JS_BARGE_IN_DETECTOR_ENABLED,
            platformAecEnabled = BuildConfig.VOICE_PLATFORM_AEC_ENABLED,
            platformNsEnabled = BuildConfig.VOICE_PLATFORM_NS_ENABLED,
            softwareAecModeName = BuildConfig.VOICE_SOFTWARE_AEC_MODE,
            earlyBargeInEnabled = BuildConfig.VOICE_EARLY_BARGE_IN_ENABLED,
        )

        fun fromValues(
            duplexCommunicationRouteEnabled: Boolean,
            presentationTimedReferenceEnabled: Boolean,
            nativeBargeInDetectorEnabled: Boolean,
            legacyJsBargeInDetectorEnabled: Boolean,
            platformAecEnabled: Boolean,
            platformNsEnabled: Boolean,
            softwareAecModeName: String,
            earlyBargeInEnabled: Boolean,
        ): VoiceRolloutConfig = VoiceRolloutConfig(
            duplexCommunicationRouteEnabled = duplexCommunicationRouteEnabled,
            presentationTimedReferenceEnabled = presentationTimedReferenceEnabled,
            nativeBargeInDetectorEnabled = nativeBargeInDetectorEnabled,
            legacyJsBargeInDetectorEnabled = legacyJsBargeInDetectorEnabled,
            platformAecEnabled = platformAecEnabled,
            platformNsEnabled = platformNsEnabled,
            softwareAecMode = parseSoftwareAecMode(softwareAecModeName),
            earlyBargeInEnabled = earlyBargeInEnabled,
        )

        fun parseSoftwareAecMode(value: String): AudioConfig.SoftwareAecMode = when (
            value.trim().uppercase()
        ) {
            "PLATFORM" -> AudioConfig.SoftwareAecMode.PLATFORM
            "AUTO" -> AudioConfig.SoftwareAecMode.AUTO
            "WEBRTC_AEC3" -> AudioConfig.SoftwareAecMode.WEBRTC_AEC3
            else -> error("Unsupported VOICE_SOFTWARE_AEC_MODE: $value")
        }
    }
}
