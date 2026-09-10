package com.voiceaipoc.auth

import java.util.Base64
import javax.crypto.spec.SecretKeySpec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class SecureTokenStorageTest {
    @Test
    fun saveAndReadRoundTripKeepsOnlyCiphertextInTheBackend() {
        val backend = MemoryBackend()
        val storage = testStorage(backend)

        storage.save("access-secret", "refresh-secret")

        assertEquals(
            StoredAuthTokens("access-secret", "refresh-secret"),
            storage.read(),
        )
        assertNotEquals("access-secret", backend.values["access_token"])
        assertNotEquals("refresh-secret", backend.values["refresh_token"])
    }

    @Test
    fun corruptedCiphertextFailsClosedAndClearsStoredValues() {
        val backend = MemoryBackend()
        val storage = testStorage(backend)
        storage.save("access-secret", "refresh-secret")
        backend.values["access_token"] = "corrupted"

        assertNull(storage.read())
        assertNull(backend.values["access_token"])
        assertNull(backend.values["refresh_token"])
    }

    @Test
    fun blankTokensAreRejected() {
        val storage = testStorage(MemoryBackend())

        assertThrows(IllegalArgumentException::class.java) { storage.save("", "refresh") }
        assertThrows(IllegalArgumentException::class.java) { storage.save("access", " ") }
    }

    private fun testStorage(backend: MemoryBackend): SecureTokenStorage = SecureTokenStorage(
        backend = backend,
        keyProvider = TokenKeyProvider {
            SecretKeySpec(ByteArray(32) { it.toByte() }, "AES")
        },
        encoding = object : TokenEncoding {
            override fun encode(value: ByteArray): String = Base64.getEncoder().encodeToString(value)

            override fun decode(value: String): ByteArray = Base64.getDecoder().decode(value)
        },
    )

    private class MemoryBackend : TokenStorageBackend {
        val values = mutableMapOf<String, String>()

        override fun get(key: String): String? = values[key]

        override fun put(key: String, value: String) {
            values[key] = value
        }

        override fun clear(vararg keys: String) {
            keys.forEach(values::remove)
        }
    }
}
