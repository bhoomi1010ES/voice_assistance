package com.voiceaipoc.wakeword

import android.content.Context

/** Loads the exact integrity-checked openWakeWord chain from Android assets. */
class AndroidWakeWordRuntimeFactory(
    context: Context,
    private val config: WakeWordConfig,
) : WakeWordRuntimeFactory {
    private val assets = context.applicationContext.assets

    override fun create(): WakeWordInferenceRuntime = OnnxWakeWordRuntime(
        config = config,
        modelLoader = { artifact ->
            assets.open(artifact.assetPath).use { it.readBytes() }
        },
    )
}
