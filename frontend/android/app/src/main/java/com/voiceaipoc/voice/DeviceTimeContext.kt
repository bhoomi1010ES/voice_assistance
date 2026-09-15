package com.voiceaipoc.voice

import java.util.Locale
import java.util.TimeZone
import org.json.JSONObject

internal data class DeviceTimeContextSnapshot(
    val deviceEpochMs: Long,
    val timezoneId: String,
    val utcOffset: String,
    val locale: String,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("device_epoch_ms", deviceEpochMs)
        put("timezone_id", timezoneId)
        put("utc_offset", utcOffset)
        put("locale", locale)
    }
}

/** Builds the trusted device clock snapshot sent on voice session/turn boundaries. */
internal fun buildDeviceTimeContextJson(
    epochMs: Long = System.currentTimeMillis(),
    zone: TimeZone = TimeZone.getDefault(),
    locale: Locale = Locale.getDefault(),
): DeviceTimeContextSnapshot {
    val offsetMinutes = zone.getOffset(epochMs) / 60_000
    val sign = if (offsetMinutes < 0) "-" else "+"
    val absoluteMinutes = kotlin.math.abs(offsetMinutes)
    val utcOffset = String.format(
        Locale.US,
        "%s%02d:%02d",
        sign,
        absoluteMinutes / 60,
        absoluteMinutes % 60,
    )
    return DeviceTimeContextSnapshot(epochMs, zone.id, utcOffset, locale.toLanguageTag())
}
