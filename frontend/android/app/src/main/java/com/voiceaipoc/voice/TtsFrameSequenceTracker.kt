package com.voiceaipoc.voice

import java.util.UUID

internal class TtsFrameSequenceTracker {
    enum class Result { START, IN_ORDER, GAP, DUPLICATE_OR_OUT_OF_ORDER, STALE }

    private var responseId: UUID? = null
    private var expectedSequence: Long? = null

    fun reset() {
        responseId = null
        expectedSequence = null
    }

    fun observe(frame: TtsAudioFrame): Result {
        if (frame.startsResponse) {
            if (responseId == frame.responseId && expectedSequence != null) {
                return when {
                    frame.sequence < expectedSequence!! -> Result.DUPLICATE_OR_OUT_OF_ORDER
                    frame.sequence > expectedSequence!! -> Result.GAP
                    else -> Result.IN_ORDER
                }
            }
            responseId = frame.responseId
            expectedSequence = frame.sequence + 1
            return Result.START
        }
        if (responseId != frame.responseId) return Result.STALE
        val expected = expectedSequence ?: return Result.STALE
        val result = when {
            frame.sequence == expected -> Result.IN_ORDER
            frame.sequence > expected -> Result.GAP
            else -> Result.DUPLICATE_OR_OUT_OF_ORDER
        }
        if (result != Result.DUPLICATE_OR_OUT_OF_ORDER) {
            expectedSequence = frame.sequence + 1
        }
        return result
    }
}
