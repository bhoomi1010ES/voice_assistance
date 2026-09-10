package com.voiceaipoc.vad.silero

import android.content.Context
import java.io.IOException
import java.security.MessageDigest

/** Strictly inspects the configured asset; it never downloads or fabricates a model. */
object AndroidSileroVadModelAsset {
    fun inspect(context: Context, config: SileroVadConfig): SileroVadModelAsset {
        var sizeBytes = 0L
        val digest = MessageDigest.getInstance(SHA256_ALGORITHM)
        return try {
            context.assets.open(config.modelAssetPath).use { input ->
                val buffer = ByteArray(ASSET_SCAN_BUFFER_BYTES)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) {
                        break
                    }
                    sizeBytes += read.toLong()
                    digest.update(buffer, 0, read)
                }
            }
            if (sizeBytes == 0L) {
                SileroVadModelAsset(
                    false,
                    config.modelAssetPath,
                    0L,
                    "Configured Silero VAD model asset is empty.",
                )
            } else {
                val sha256 = digest.digest().toUpperHex()
                val sizeMatches = sizeBytes == ApprovedSileroVadModel.SIZE_BYTES
                val hashMatches = sha256 == ApprovedSileroVadModel.SHA256
                val validationError = when {
                    !sizeMatches -> "Approved Silero VAD model size mismatch: " +
                        "expected=${ApprovedSileroVadModel.SIZE_BYTES}, actual=$sizeBytes."
                    !hashMatches -> "Approved Silero VAD model SHA-256 mismatch."
                    else -> null
                }
                SileroVadModelAsset(
                    present = true,
                    assetPath = config.modelAssetPath,
                    sizeBytes = sizeBytes,
                    missingReason = validationError,
                    sha256 = sha256,
                    sha256Verified = sizeMatches && hashMatches,
                )
            }
        } catch (_: IOException) {
            SileroVadModelAsset(
                false,
                config.modelAssetPath,
                0L,
                "Approved Silero VAD model asset is missing.",
            )
        }
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
