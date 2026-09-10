package com.voiceaipoc.wakeword

import android.content.Context
import java.io.IOException
import java.security.MessageDigest

/** Read-only inspection of immutable model files packaged in Android assets. */
object AndroidWakeWordModelAssets {
    fun inspect(context: Context, config: WakeWordConfig): WakeWordModelAssets {
        val requiredPaths = config.requiredAssetPaths()
        val artifactsByPath = ApprovedOpenWakeWordModel.CONTRACT.artifacts.associateBy {
            it.assetPath
        }
        val hashes = mutableMapOf<String, String>()
        val sizes = mutableMapOf<String, Long>()
        val missingPaths = requiredPaths.filterNot { assetPath ->
            try {
                val digest = MessageDigest.getInstance(SHA256_ALGORITHM)
                var size = 0L
                context.assets.open(assetPath).use { stream ->
                    val buffer = ByteArray(ASSET_SCAN_BUFFER_BYTES)
                    while (true) {
                        val read = stream.read(buffer)
                        if (read < 0) {
                            break
                        }
                        size += read.toLong()
                        digest.update(buffer, 0, read)
                    }
                }
                sizes[assetPath] = size
                hashes[assetPath] = digest.digest().toUpperHex()
                size > 0L
            } catch (_: IOException) {
                false
            }
        }

        val validationErrors = if (missingPaths.isEmpty()) {
            requiredPaths.mapNotNull { path ->
                val artifact = artifactsByPath[path]
                    ?: return@mapNotNull "No approved contract for $path."
                when {
                    sizes[path] != artifact.sizeBytes ->
                        "${artifact.fileName} size mismatch."
                    hashes[path] != artifact.sha256 ->
                        "${artifact.fileName} SHA-256 mismatch."
                    else -> null
                }
            }
        } else {
            emptyList()
        }

        return WakeWordModelAssets(
            present = missingPaths.isEmpty(),
            requiredAssetPaths = requiredPaths,
            missingAssetPaths = missingPaths,
            hashVerified = missingPaths.isEmpty() && validationErrors.isEmpty(),
            classifierSha256 = hashes[ApprovedOpenWakeWordModel.CLASSIFIER_ASSET_PATH],
            validationError = validationErrors.takeIf { it.isNotEmpty() }?.joinToString(),
        )
    }

    private fun ByteArray.toUpperHex(): String = buildString(size * 2) {
        for (byte in this@toUpperHex) {
            val value = byte.toInt() and 0xFF
            append(HEX_DIGITS[value ushr 4])
            append(HEX_DIGITS[value and 0x0F])
        }
    }

    private const val ASSET_SCAN_BUFFER_BYTES = 8_192
    private const val SHA256_ALGORITHM = "SHA-256"
    private const val HEX_DIGITS = "0123456789ABCDEF"
}
