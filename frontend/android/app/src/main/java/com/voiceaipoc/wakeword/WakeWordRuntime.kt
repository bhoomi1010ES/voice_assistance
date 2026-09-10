package com.voiceaipoc.wakeword

/** Immutable result of inspecting the packaged openWakeWord model bundle. */
data class WakeWordModelAssets(
    val present: Boolean,
    val requiredAssetPaths: List<String>,
    val missingAssetPaths: List<String>,
    val hashVerified: Boolean = false,
    val classifierSha256: String? = null,
    val validationError: String? = null,
)

/**
 * Model-specific inference boundary. Implementations own feature extraction,
 * embeddings, and classifier sessions; callers provide reusable 16 kHz PCM16.
 */
interface WakeWordInferenceRuntime : AutoCloseable {
    val runtimeName: String
    val runtimeVersion: String
        get() = "UNSPECIFIED"
    /** True only after the implementation validates actual loaded graph metadata. */
    val tensorContractVerified: Boolean
        get() = false

    @Throws(WakeWordRuntimeException::class)
    fun initialize()

    /** Returns one classifier confidence in the inclusive range [0, 1]. */
    @Throws(WakeWordRuntimeException::class)
    fun predict(pcm16: ShortArray, samplesRead: Int): Float

    /** Clears streaming context for a new microphone session. */
    @Throws(WakeWordRuntimeException::class)
    fun reset() = Unit

    override fun close()
}

fun interface WakeWordRuntimeFactory {
    @Throws(WakeWordRuntimeException::class)
    fun create(): WakeWordInferenceRuntime
}

class WakeWordRuntimeException(
    val errorCode: String,
    message: String,
    cause: Throwable? = null,
) : RuntimeException(message, cause)

/**
 * Explicit unavailable runtime used by failure-path tests and unsupported
 * configurations. Production uses [OnnxWakeWordRuntime].
 */
class UnavailableWakeWordRuntimeFactory(
    private val runtimeName: String,
) : WakeWordRuntimeFactory {
    override fun create(): WakeWordInferenceRuntime {
        throw WakeWordRuntimeException(
            WakeWordEngine.ERROR_RUNTIME_UNAVAILABLE,
            "$runtimeName is selected but unavailable.",
        )
    }
}
