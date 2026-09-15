package com.voiceaipoc.voice

import java.time.Instant
import java.util.Locale
import java.util.TimeZone
import org.junit.Assert.assertEquals
import org.junit.Test

class DeviceTimeContextTest {
    @Test
    fun usesIanaZoneRulesForNewYorkDst() {
        val epochMs = Instant.parse("2026-07-01T12:00:00Z").toEpochMilli()
        val payload = buildDeviceTimeContextJson(
            epochMs = epochMs,
            zone = TimeZone.getTimeZone("America/New_York"),
            locale = Locale.US,
        )

        assertEquals(epochMs, payload.deviceEpochMs)
        assertEquals("America/New_York", payload.timezoneId)
        assertEquals("-04:00", payload.utcOffset)
        assertEquals("en-US", payload.locale)
    }

    @Test
    fun reportsRuntimeLocaleAndZoneWithoutHardcodedCountry() {
        val payload = buildDeviceTimeContextJson(
            epochMs = Instant.parse("2026-09-11T07:49:00Z").toEpochMilli(),
            zone = TimeZone.getTimeZone("Asia/Kolkata"),
            locale = Locale.forLanguageTag("en-IN"),
        )

        assertEquals("Asia/Kolkata", payload.timezoneId)
        assertEquals("+05:30", payload.utcOffset)
        assertEquals("en-IN", payload.locale)
    }
}
