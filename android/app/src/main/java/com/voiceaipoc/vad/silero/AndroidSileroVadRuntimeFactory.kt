package com.voiceaipoc.vad.silero

import android.content.Context

/** Loads only the configured, integrity-checked model from packaged Android assets. */
class AndroidSileroVadRuntimeFactory(
    context: Context,
    private val config: SileroVadConfig,
) : SileroVadRuntimeFactory {
    private val assets = context.applicationContext.assets

    override fun create(): SileroVadRuntime = OnnxSileroVadRuntime(
        config = config,
        modelLoader = {
            assets.open(config.modelAssetPath).use { it.readBytes() }
        },
    )
}
