package com.voiceaipoc.auth

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.nio.charset.StandardCharsets
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private const val ANDROID_KEYSTORE = "AndroidKeyStore"

data class StoredAuthTokens(
    val accessToken: String,
    val refreshToken: String,
)

interface AuthTokenStorage {
    fun save(accessToken: String, refreshToken: String)

    fun read(): StoredAuthTokens?

    fun clear()
}

/**
 * Stores tokens encrypted with an AES-GCM key held by Android Keystore.
 * SharedPreferences contains ciphertext only; tokens are never logged or bridged.
 */
class SecureTokenStorage internal constructor(
    private val backend: TokenStorageBackend,
    private val keyProvider: TokenKeyProvider,
    private val encoding: TokenEncoding,
    private val secureRandom: SecureRandom = SecureRandom(),
) : AuthTokenStorage {
    constructor(context: Context) : this(
        backend = SharedPreferencesTokenBackend(
            context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE),
        ),
        keyProvider = AndroidKeystoreTokenKeyProvider(KEY_ALIAS),
        encoding = AndroidBase64TokenEncoding,
    )

    override fun save(accessToken: String, refreshToken: String) {
        require(accessToken.isNotBlank()) { "accessToken must not be blank" }
        require(refreshToken.isNotBlank()) { "refreshToken must not be blank" }
        val key = keyProvider.getOrCreate()
        backend.put(ACCESS_TOKEN_KEY, encrypt(accessToken, key))
        backend.put(REFRESH_TOKEN_KEY, encrypt(refreshToken, key))
    }

    override fun read(): StoredAuthTokens? {
        val accessCiphertext = backend.get(ACCESS_TOKEN_KEY) ?: return null
        val refreshCiphertext = backend.get(REFRESH_TOKEN_KEY) ?: return null
        return try {
            val key = keyProvider.getOrCreate()
            StoredAuthTokens(
                accessToken = decrypt(accessCiphertext, key),
                refreshToken = decrypt(refreshCiphertext, key),
            )
        } catch (_: GeneralSecurityException) {
            clear()
            null
        } catch (_: IllegalArgumentException) {
            clear()
            null
        }
    }

    override fun clear() {
        backend.clear(ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY)
    }

    private fun encrypt(value: String, key: SecretKey): String {
        val iv = ByteArray(GCM_IV_BYTES).also(secureRandom::nextBytes)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(GCM_TAG_BITS, iv))
        val ciphertext = cipher.doFinal(value.toByteArray(StandardCharsets.UTF_8))
        val combined = ByteArray(iv.size + ciphertext.size)
        iv.copyInto(combined)
        ciphertext.copyInto(combined, destinationOffset = iv.size)
        return encoding.encode(combined)
    }

    private fun decrypt(encoded: String, key: SecretKey): String {
        val combined = encoding.decode(encoded)
        require(combined.size > GCM_IV_BYTES) { "Encrypted token is malformed" }
        val iv = combined.copyOfRange(0, GCM_IV_BYTES)
        val ciphertext = combined.copyOfRange(GCM_IV_BYTES, combined.size)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(GCM_TAG_BITS, iv))
        return String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8)
    }

    companion object {
        private const val PREFERENCES_NAME = "voice_assistance_secure_tokens"
        private const val KEY_ALIAS = "voice_assistance_auth_tokens_v1"
        private const val ACCESS_TOKEN_KEY = "access_token"
        private const val REFRESH_TOKEN_KEY = "refresh_token"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val GCM_IV_BYTES = 12
        private const val GCM_TAG_BITS = 128
    }
}

internal interface TokenStorageBackend {
    fun get(key: String): String?

    fun put(key: String, value: String)

    fun clear(vararg keys: String)
}

internal fun interface TokenKeyProvider {
    fun getOrCreate(): SecretKey
}

internal interface TokenEncoding {
    fun encode(value: ByteArray): String

    fun decode(value: String): ByteArray
}

private class SharedPreferencesTokenBackend(
    private val preferences: SharedPreferences,
) : TokenStorageBackend {
    override fun get(key: String): String? = preferences.getString(key, null)

    override fun put(key: String, value: String) {
        check(preferences.edit().putString(key, value).commit()) {
            "Unable to persist secure authentication token"
        }
    }

    override fun clear(vararg keys: String) {
        val editor = preferences.edit()
        keys.forEach(editor::remove)
        check(editor.commit()) { "Unable to clear secure authentication tokens" }
    }
}

private class AndroidKeystoreTokenKeyProvider(
    private val alias: String,
) : TokenKeyProvider {
    override fun getOrCreate(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        val existing = keyStore.getKey(alias, null) as? SecretKey
        if (existing != null) return existing

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }
}

private object AndroidBase64TokenEncoding : TokenEncoding {
    override fun encode(value: ByteArray): String = Base64.encodeToString(value, Base64.NO_WRAP)

    override fun decode(value: String): ByteArray = Base64.decode(value, Base64.NO_WRAP)
}
