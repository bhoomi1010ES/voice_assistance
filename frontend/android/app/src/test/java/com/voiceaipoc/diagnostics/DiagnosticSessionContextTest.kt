package com.voiceaipoc.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DiagnosticSessionContextTest {
    @Test
    fun beginReusesIdUntilSessionEnds() {
        val context = DiagnosticSessionContext { "diag-test" }

        assertEquals("diag-test", context.begin())
        assertEquals("diag-test", context.begin("ignored-after-start"))
        assertEquals("diag-test", context.ensureActive())
        assertTrue(context.tag("event=started").startsWith("diagnostic_session_id=diag-test "))

        assertEquals("diag-test", context.end())
        assertNull(context.currentId())
        assertEquals("diag-test", context.begin("diag-test"))
    }
}
