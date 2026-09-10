package com.voiceaipoc.wakeword

import java.io.File
import java.security.MessageDigest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class WakeWordDiagnosticCaptureTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun explicitCaptureWritesExactLittleEndianInferenceWindowsAndHash() {
        val directory = temporaryFolder.newFolder("capture")
        val capture = newCapture(directory)
        assertTrue(directory.listFiles().orEmpty().isEmpty())
        assertTrue(capture.start("POSITIVE_01", DURATION_MS).succeeded)

        val expected = ArrayList<Short>()
        repeat(WINDOW_COUNT) { window ->
            val samples = ShortArray(INFERENCE_SAMPLES) { index ->
                (window * 17 + index - 700).toShort()
            }
            samples.forEach(expected::add)
            assertTrue(capture.offerInferenceWindow(samples, samples.size))
            Thread.sleep(1)
        }
        waitUntilComplete(capture)

        val status = capture.getStatus()
        assertFalse(status.active)
        assertEquals(1, status.completedCaptureCount)
        assertEquals(0L, status.droppedWindows)
        val record = status.records.single()
        assertTrue(record.valid)
        assertEquals(WINDOW_COUNT, record.inferenceWindowsWritten)
        assertEquals(WINDOW_COUNT.toLong() * INFERENCE_SAMPLES, record.samplesWritten)
        val file = File(directory, record.fileName)
        assertEquals(record.bytesWritten, file.length())
        assertEquals(file.sha256(), record.sha256)

        val decoded = decodePcm(file.readBytes())
        assertArrayEquals(expected.toShortArray(), decoded)
    }

    @Test
    fun partialCaptureIsRejectedAndSensitiveFileIsRemoved() {
        val directory = temporaryFolder.newFolder("partial")
        val capture = newCapture(directory)
        assertTrue(capture.start("NEGATIVE_01", DURATION_MS).succeeded)
        assertTrue(
            capture.offerInferenceWindow(
                ShortArray(INFERENCE_SAMPLES) { 123 },
                INFERENCE_SAMPLES,
            ),
        )

        capture.stop()
        waitUntilComplete(capture)

        val record = capture.getStatus().records.single()
        assertFalse(record.valid)
        assertNotNull(record.error)
        assertFalse(File(directory, record.fileName).exists())
    }

    @Test
    fun malformedInputDoesNotBecomeAValidCaptureAndDeleteClearsMetadata() {
        val directory = temporaryFolder.newFolder("malformed")
        val capture = newCapture(directory)
        assertTrue(capture.start("POSITIVE_02", DURATION_MS).succeeded)
        assertFalse(
            capture.offerInferenceWindow(
                ShortArray(INFERENCE_SAMPLES - 1),
                INFERENCE_SAMPLES - 1,
            ),
        )
        repeat(WINDOW_COUNT) {
            capture.offerInferenceWindow(ShortArray(INFERENCE_SAMPLES), INFERENCE_SAMPLES)
            Thread.sleep(1)
        }
        waitUntilComplete(capture)

        assertFalse(capture.getStatus().records.single().valid)
        capture.deleteAll()
        val status = capture.getStatus()
        assertEquals(0, status.completedCaptureCount)
        assertTrue(directory.listFiles().orEmpty().isEmpty())
    }

    private fun newCapture(directory: File) = WakeWordDiagnosticCapture(
        directory = directory,
        inferenceSamples = INFERENCE_SAMPLES,
        sampleRateHz = SAMPLE_RATE,
        queueCapacityWindows = 8,
    )

    private fun waitUntilComplete(capture: WakeWordDiagnosticCapture) {
        val deadline = System.currentTimeMillis() + 5_000L
        while (capture.getStatus().active && System.currentTimeMillis() < deadline) {
            Thread.sleep(10)
        }
        assertFalse("Diagnostic capture did not complete.", capture.getStatus().active)
    }

    private fun decodePcm(bytes: ByteArray): ShortArray = ShortArray(bytes.size / 2) { index ->
        val low = bytes[index * 2].toInt() and 0xFF
        val high = bytes[index * 2 + 1].toInt()
        ((high shl 8) or low).toShort()
    }

    private fun File.sha256(): String = MessageDigest.getInstance("SHA-256")
        .digest(readBytes())
        .joinToString("") { "%02X".format(it.toInt() and 0xFF) }

    companion object {
        private const val SAMPLE_RATE = 16_000
        private const val INFERENCE_SAMPLES = 1_280
        private const val DURATION_MS = 2_560
        private const val WINDOW_COUNT = DURATION_MS / 80
    }
}
