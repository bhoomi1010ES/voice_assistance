package com.voiceaipoc.wakeword

import com.voiceaipoc.audio.AudioRingBuffer
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest

/**
 * Temporary, explicit-only PCM capture for cross-runtime diagnostics.
 *
 * Inputs are complete 1,280-sample wake inference hops copied from the wake
 * worker. A bounded preallocated queue isolates file I/O from inference. Files
 * stay in app-private storage and are never exposed through the RN bridge.
 */
class WakeWordDiagnosticCapture(
    private val directory: File,
    val inferenceSamples: Int,
    val sampleRateHz: Int,
    private val queueCapacityWindows: Int = DEFAULT_QUEUE_CAPACITY_WINDOWS,
    private val wallClockMs: () -> Long = System::currentTimeMillis,
) {
    data class Record(
        val captureId: String,
        val label: String,
        val fileName: String,
        val startedAtTimestampMs: Long,
        val completedAtTimestampMs: Long,
        val durationMs: Int,
        val inferenceWindowsWritten: Int,
        val samplesWritten: Long,
        val bytesWritten: Long,
        val droppedWindows: Long,
        val sha256: String?,
        val valid: Boolean,
        val error: String?,
    )

    data class Status(
        val diagnosticOnly: Boolean,
        val active: Boolean,
        val captureId: String?,
        val label: String?,
        val targetDurationMs: Int,
        val targetInferenceWindows: Int,
        val inferenceWindowsAccepted: Int,
        val inferenceWindowsWritten: Int,
        val queueDepthWindows: Int,
        val queueCapacityWindows: Int,
        val queueHighWaterMarkWindows: Int,
        val droppedWindows: Long,
        val completedCaptureCount: Int,
        val lastError: String?,
        val records: List<Record>,
    )

    data class StartResult(
        val succeeded: Boolean,
        val errorCode: String? = null,
        val errorMessage: String? = null,
    )

    private val lock = Any()
    private val queue = AudioRingBuffer(queueCapacityWindows, inferenceSamples)
    private val writerWindow = ShortArray(inferenceSamples)
    private val writerBytes = ByteArray(inferenceSamples * PCM16_BYTES_PER_SAMPLE)
    private val records = ArrayDeque<Record>()

    private var active = false
    private var accepting = false
    private var writerThread: Thread? = null
    private var activeCaptureId: String? = null
    private var activeLabel: String? = null
    private var activeStartedAtMs = 0L
    private var targetDurationMs = 0
    private var targetWindows = 0
    private var acceptedWindows = 0
    private var writtenWindows = 0
    private var queueHighWaterMark = 0
    private var droppedWindows = 0L
    private var sequence = 0
    private var lastError: String? = null

    init {
        require(inferenceSamples > 0) { "inferenceSamples must be positive" }
        require(sampleRateHz > 0) { "sampleRateHz must be positive" }
        require(queueCapacityWindows > 0) { "queueCapacityWindows must be positive" }
    }

    fun start(label: String, durationMs: Int): StartResult {
        val safeLabel = sanitizeLabel(label)
            ?: return StartResult(false, ERROR_INVALID_REQUEST, "Capture label is invalid.")
        if (durationMs !in MIN_CAPTURE_DURATION_MS..MAX_CAPTURE_DURATION_MS ||
            durationMs % INFERENCE_HOP_DURATION_MS != 0
        ) {
            return StartResult(
                false,
                ERROR_INVALID_REQUEST,
                "Capture duration must be $MIN_CAPTURE_DURATION_MS..$MAX_CAPTURE_DURATION_MS ms " +
                    "and divisible by $INFERENCE_HOP_DURATION_MS ms.",
            )
        }

        synchronized(lock) {
            if (active || writerThread?.isAlive == true) {
                return StartResult(false, ERROR_ALREADY_ACTIVE, "A diagnostic PCM capture is active.")
            }
            if (!directory.exists() && !directory.mkdirs()) {
                return StartResult(
                    false,
                    ERROR_STORAGE,
                    "Diagnostic capture directory could not be created.",
                )
            }

            sequence += 1
            val startedAt = wallClockMs()
            val captureId = "${startedAt}_${safeLabel}_$sequence"
            queue.clear()
            writerWindow.fill(0)
            writerBytes.fill(0)
            active = true
            accepting = true
            activeCaptureId = captureId
            activeLabel = safeLabel
            activeStartedAtMs = startedAt
            targetDurationMs = durationMs
            targetWindows = durationMs / INFERENCE_HOP_DURATION_MS
            acceptedWindows = 0
            writtenWindows = 0
            queueHighWaterMark = 0
            droppedWindows = 0L
            lastError = null

            val newThread = Thread(
                { writerLoop(captureId, safeLabel, startedAt, durationMs) },
                WRITER_THREAD_NAME,
            )
            writerThread = newThread
            try {
                newThread.start()
            } catch (exception: RuntimeException) {
                active = false
                accepting = false
                writerThread = null
                lastError = exception.message
                return StartResult(
                    false,
                    ERROR_WRITER,
                    "Diagnostic writer could not start: ${exception.message}",
                )
            }
        }
        return StartResult(true)
    }

    /** Non-blocking bounded copy of one exact wake inference input window. */
    fun offerInferenceWindow(source: ShortArray, samplesRead: Int): Boolean {
        synchronized(lock) {
            if (!accepting) {
                return false
            }
            if (samplesRead != inferenceSamples || source.size < inferenceSamples) {
                lastError = "Malformed diagnostic PCM window: $samplesRead samples."
                droppedWindows += 1L
                return false
            }

            val writeResult = queue.write(source, sampleCount = inferenceSamples)
            acceptedWindows += 1
            queueHighWaterMark = maxOf(queueHighWaterMark, queue.currentBufferedFrames())
            if (writeResult == AudioRingBuffer.WriteResult.WROTE_AFTER_DROPPING_OLDEST) {
                droppedWindows += 1L
            }
            if (acceptedWindows >= targetWindows) {
                accepting = false
            }
            return true
        }
    }

    fun stop() {
        val thread = synchronized(lock) {
            accepting = false
            writerThread
        }
        if (thread != null && thread !== Thread.currentThread()) {
            try {
                thread.join(WRITER_JOIN_TIMEOUT_MS)
            } catch (interrupted: InterruptedException) {
                Thread.currentThread().interrupt()
                synchronized(lock) { lastError = "Interrupted while stopping diagnostic capture." }
            }
        }
    }

    fun getStatus(): Status = synchronized(lock) {
        Status(
            diagnosticOnly = true,
            active = active,
            captureId = activeCaptureId,
            label = activeLabel,
            targetDurationMs = targetDurationMs,
            targetInferenceWindows = targetWindows,
            inferenceWindowsAccepted = acceptedWindows,
            inferenceWindowsWritten = writtenWindows,
            queueDepthWindows = queue.currentBufferedFrames(),
            queueCapacityWindows = queueCapacityWindows,
            queueHighWaterMarkWindows = queueHighWaterMark,
            droppedWindows = droppedWindows,
            completedCaptureCount = records.size,
            lastError = lastError,
            records = records.toList(),
        )
    }

    fun completedFiles(): List<File> = synchronized(lock) {
        records.filter { it.valid }.map { File(directory, it.fileName) }
    }

    fun deleteAll(): Int {
        stop()
        val deleted = directory.listFiles()
            ?.filter { file ->
                file.extension == PCM_EXTENSION ||
                    file.extension == TRACE_EXTENSION ||
                    file.extension == PART_EXTENSION
            }
            ?.count { it.delete() }
            ?: 0
        synchronized(lock) {
            records.clear()
            activeCaptureId = null
            activeLabel = null
            targetDurationMs = 0
            targetWindows = 0
            acceptedWindows = 0
            writtenWindows = 0
            queueHighWaterMark = 0
            droppedWindows = 0L
            lastError = null
            queue.clear()
        }
        return deleted
    }

    private fun writerLoop(
        captureId: String,
        label: String,
        startedAtMs: Long,
        durationMs: Int,
    ) {
        val partFile = File(directory, "$captureId.$PART_EXTENSION")
        val pcmFile = File(directory, "$captureId.$PCM_EXTENSION")
        var error: String? = null
        try {
            FileOutputStream(partFile).use { output ->
                while (true) {
                    val samples = try {
                        queue.read(writerWindow, waitTimeoutMs = QUEUE_WAIT_TIMEOUT_MS)
                    } catch (interrupted: InterruptedException) {
                        Thread.currentThread().interrupt()
                        break
                    }
                    if (samples > 0) {
                        pcm16ToLittleEndian(writerWindow, writerBytes)
                        output.write(writerBytes)
                        synchronized(lock) { writtenWindows += 1 }
                    }
                    val finished = synchronized(lock) {
                        !accepting && queue.currentBufferedFrames() == 0
                    }
                    if (finished) {
                        break
                    }
                }
                output.fd.sync()
            }
            if (!partFile.renameTo(pcmFile)) {
                error = "Diagnostic PCM file could not be finalized."
            }
        } catch (exception: Exception) {
            error = "Diagnostic PCM writer failed: ${exception.message}"
        }

        val completedAt = wallClockMs()
        synchronized(lock) {
            val valid = error == null && droppedWindows == 0L &&
                writtenWindows == targetWindows && pcmFile.isFile
            if (!valid && error == null) {
                error = "Capture incomplete: written=$writtenWindows, target=$targetWindows, " +
                    "dropped=$droppedWindows."
            }
            if (!valid) {
                pcmFile.delete()
                partFile.delete()
            }
            val finalFile = if (valid) pcmFile else null
            records.addLast(
                Record(
                    captureId = captureId,
                    label = label,
                    fileName = pcmFile.name,
                    startedAtTimestampMs = startedAtMs,
                    completedAtTimestampMs = completedAt,
                    durationMs = durationMs,
                    inferenceWindowsWritten = writtenWindows,
                    samplesWritten = writtenWindows.toLong() * inferenceSamples,
                    bytesWritten = finalFile?.length() ?: 0L,
                    droppedWindows = droppedWindows,
                    sha256 = finalFile?.sha256(),
                    valid = valid,
                    error = error,
                ),
            )
            while (records.size > RECORD_HISTORY_CAPACITY) {
                records.removeFirst()
            }
            lastError = error
            active = false
            accepting = false
            writerThread = null
            activeCaptureId = null
            activeLabel = null
            queue.clear()
        }
    }

    private fun sanitizeLabel(label: String): String? {
        val value = label.trim().uppercase()
        return value.takeIf {
            it.isNotEmpty() && it.length <= MAX_LABEL_LENGTH &&
                it.all { character -> character.isLetterOrDigit() || character == '_' || character == '-' }
        }
    }

    private fun pcm16ToLittleEndian(source: ShortArray, destination: ByteArray) {
        for (index in source.indices) {
            val sample = source[index].toInt()
            destination[index * 2] = (sample and 0xFF).toByte()
            destination[index * 2 + 1] = ((sample ushr 8) and 0xFF).toByte()
        }
    }

    private fun File.sha256(): String {
        val digest = MessageDigest.getInstance(SHA256_ALGORITHM)
        inputStream().use { input ->
            val buffer = ByteArray(HASH_BUFFER_BYTES)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02X".format(it.toInt() and 0xFF) }
    }

    companion object {
        const val ERROR_INVALID_REQUEST = "E_WAKE_DIAGNOSTIC_CAPTURE_REQUEST"
        const val ERROR_ALREADY_ACTIVE = "E_WAKE_DIAGNOSTIC_CAPTURE_ACTIVE"
        const val ERROR_STORAGE = "E_WAKE_DIAGNOSTIC_STORAGE"
        const val ERROR_WRITER = "E_WAKE_DIAGNOSTIC_WRITER"
        const val DEFAULT_CAPTURE_DURATION_MS = 5_120

        private const val INFERENCE_HOP_DURATION_MS = 80
        private const val MIN_CAPTURE_DURATION_MS = 2_560
        private const val MAX_CAPTURE_DURATION_MS = 10_240
        private const val DEFAULT_QUEUE_CAPACITY_WINDOWS = 8
        private const val RECORD_HISTORY_CAPACITY = 32
        private const val MAX_LABEL_LENGTH = 40
        private const val PCM16_BYTES_PER_SAMPLE = 2
        private const val QUEUE_WAIT_TIMEOUT_MS = 50L
        private const val WRITER_JOIN_TIMEOUT_MS = 3_000L
        private const val WRITER_THREAD_NAME = "VoiceAI-WakeCapture"
        private const val PCM_EXTENSION = "pcm"
        private const val TRACE_EXTENSION = "owwtrace"
        private const val PART_EXTENSION = "part"
        private const val SHA256_ALGORITHM = "SHA-256"
        private const val HASH_BUFFER_BYTES = 8_192
    }
}
