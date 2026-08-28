package com.voiceaipoc.phase3

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.voiceaipoc.auth.SecureTokenStorage
import org.json.JSONObject
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.junit.runner.RunWith
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/**
 * Test-only physical-device setup for Phase 3.
 *
 * The production application deliberately has no authentication UI. This test
 * creates a disposable Phase 2 account over the local reverse tunnel and
 * stores its credentials through the production Keystore-backed storage.
 * Token values are never written to test output.
 */
@RunWith(AndroidJUnit4::class)
class SeedPhase3AuthTest {
    @Test
    fun seedDisposableAuthSession() {
        val baseUrl = "http://127.0.0.1:8000"
        val email = "phase3-device-${UUID.randomUUID()}@example.test"
        val password = "phase3-device-validation-password"

        postJson(
            "$baseUrl/auth/register",
            "{\"email\":\"$email\",\"password\":\"$password\"}",
            HttpURLConnection.HTTP_CREATED,
        )
        val login = postJson(
            "$baseUrl/auth/login",
            "{\"email\":\"$email\",\"password\":\"$password\",\"device_identifier\":\"phase3-physical-${UUID.randomUUID()}\",\"platform\":\"android\"}",
            HttpURLConnection.HTTP_OK,
        )
        val tokens = JSONObject(login)
        val storage = SecureTokenStorage(
            InstrumentationRegistry.getInstrumentation().targetContext,
        )
        storage.save(tokens.getString("access_token"), tokens.getString("refresh_token"))
        assertNotNull(storage.read())
    }

    private fun postJson(url: String, body: String, expectedCode: Int): String {
        val connection = URL(url).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.connectTimeout = 5_000
            connection.readTimeout = 10_000
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { output ->
                output.write(body.toByteArray(Charsets.UTF_8))
            }
            val responseBody = (if (connection.responseCode in 200..299) {
                connection.inputStream
            } else {
                connection.errorStream
            }).bufferedReader().use { reader -> reader.readText() }
            check(connection.responseCode == expectedCode) {
                "unexpected auth response ${connection.responseCode}"
            }
            responseBody
        } finally {
            connection.disconnect()
        }
    }
}
