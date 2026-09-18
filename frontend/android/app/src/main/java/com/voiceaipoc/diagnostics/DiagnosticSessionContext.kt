package com.voiceaipoc.diagnostics

import java.util.concurrent.atomic.AtomicLong

/**
 * Correlates one native voice/capture run without retaining audio or transcript
 * content. The ID is intentionally metadata-only and is safe to include in
 * logcat and diagnostic status snapshots.
 */
class DiagnosticSessionContext(
    private val idFactory: () -> String = {
        "diag-${System.currentTimeMillis()}-${NEXT_ID.incrementAndGet()}"
    },
) {
    @Volatile
    private var activeId: String? = null

    @Synchronized
    fun begin(requestedId: String? = null): String {
        val requested = requestedId?.trim()?.takeIf { it.isNotEmpty() }
        if (activeId == null) {
            activeId = requested ?: idFactory()
        }
        return activeId!!
    }

    fun ensureActive(): String = activeId ?: begin()

    fun currentId(): String? = activeId

    @Synchronized
    fun end(): String? {
        val ended = activeId
        activeId = null
        return ended
    }

    fun tag(message: String): String = "$TAG_PREFIX=${currentId() ?: NONE} $message"

    companion object {
        const val NONE = "NONE"
        private const val TAG_PREFIX = "diagnostic_session_id"
        private val NEXT_ID = AtomicLong(0L)
    }
}
