package com.voiceaipoc.vad.silero

/** Result of inspecting the configured Android model asset. */
data class SileroVadModelAsset(
    val present: Boolean,
    val assetPath: String,
    val sizeBytes: Long,
    val missingReason: String?,
    val sha256: String? = null,
    val sha256Verified: Boolean = false,
)

/**
 * Native Silero inference boundary.
 *
 * A concrete ONNX Runtime adapter is responsible for loading the model,
 * converting signed PCM16 to normalized float input, carrying the model's
 * 64-sample audio context and recurrent state between calls, and returning a
 * finite speech probability in [0, 1]. The caller reuses [pcm16] and the
 * implementation must not retain it.
 */
interface SileroVadRuntime : AutoCloseable {
    val runtimeName: String
    val runtimeVersion: String

    fun initialize()

    fun infer(pcm16: ShortArray, samplesRead: Int): Float

    fun reset()

    override fun close()
}

fun interface SileroVadRuntimeFactory {
    fun create(): SileroVadRuntime
}

class SileroVadRuntimeException(
    val errorCode: String,
    message: String,
    cause: Throwable? = null,
) : RuntimeException(message, cause)

/** Explicit unavailable path retained for focused failure tests. */
class UnavailableSileroVadRuntimeFactory(
    private val runtimeName: String,
) : SileroVadRuntimeFactory {
    override fun create(): SileroVadRuntime {
        throw SileroVadRuntimeException(
            SileroVadEngine.ERROR_RUNTIME_UNAVAILABLE,
            "$runtimeName is selected but is not packaged for the approved Silero model.",
        )
    }
}
