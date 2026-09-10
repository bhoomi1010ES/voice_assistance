package com.voiceaipoc.wakeword

import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import kotlin.math.max

data class WakeWordReplayResult(
    val captureId: String,
    val pcmFileName: String,
    val pcmSha256: String,
    val repetition: Int,
    val traceFileName: String,
    val inferenceCount: Int,
    val maximumEffectiveScore: Float,
    val maximumRawScore: Float,
    val runtimeErrorCount: Int,
    val elapsedMs: Double,
)

data class WakeWordReplayBatchResult(
    val diagnosticOnly: Boolean,
    val runtimeName: String,
    val runtimeVersion: String,
    val repetitionCount: Int,
    val captureCount: Int,
    val replayCount: Int,
    val results: List<WakeWordReplayResult>,
)

/** Replays app-private PCM through the exact production ONNX runtime. */
class WakeWordDiagnosticReplay(
    private val capture: WakeWordDiagnosticCapture,
    private val runtimeFactory: WakeWordRuntimeFactory,
    private val sampleRateHz: Int,
    private val inferenceSamples: Int,
) {
    fun replayAll(repetitions: Int): WakeWordReplayBatchResult {
        require(repetitions in 1..MAX_REPETITIONS) {
            "Replay repetitions must be in 1..$MAX_REPETITIONS."
        }
        val files = capture.completedFiles().sortedBy(File::getName)
        require(files.isNotEmpty()) { "No valid diagnostic PCM captures are available." }

        val results = ArrayList<WakeWordReplayResult>(files.size * repetitions)
        for (file in files) {
            validatePcmFile(file)
            for (repetition in 1..repetitions) {
                results += replayFile(file, repetition)
            }
        }
        return WakeWordReplayBatchResult(
            diagnosticOnly = true,
            runtimeName = OnnxWakeWordRuntime.RUNTIME_NAME,
            runtimeVersion = OnnxWakeWordRuntime.RUNTIME_VERSION,
            repetitionCount = repetitions,
            captureCount = files.size,
            replayCount = results.size,
            results = results,
        )
    }

    private fun replayFile(file: File, repetition: Int): WakeWordReplayResult {
        val runtime = runtimeFactory.create()
        val inferenceCount = (file.length() / BYTES_PER_INFERENCE_WINDOW).toInt()
        val traceFile = File(
            file.parentFile,
            "${file.nameWithoutExtension}_android_run$repetition.$TRACE_EXTENSION",
        )
        val pcmBytes = ByteArray(BYTES_PER_INFERENCE_WINDOW)
        val pcmSamples = ShortArray(inferenceSamples)
        var maximumEffectiveScore = 0f
        var maximumRawScore = 0f
        var runtimeErrors = 0
        var completedInferences = 0
        val startedNanos = System.nanoTime()

        try {
            runtime.initialize()
            runtime.reset()
            val onnxRuntime = runtime as? OnnxWakeWordRuntime
                ?: error("Diagnostic replay requires OnnxWakeWordRuntime.")
            FileInputStream(file).use { input ->
                WakeWordTraceWriter(
                    file = traceFile,
                    sampleRateHz = sampleRateHz,
                    inferenceSamples = inferenceSamples,
                    inferenceCount = inferenceCount,
                ).use { trace ->
                    for (index in 1..inferenceCount) {
                        readFully(input, pcmBytes)
                        decodeLittleEndianPcm16(pcmBytes, pcmSamples)
                        val effectiveScore = runtime.predict(pcmSamples, inferenceSamples)
                        val rawScore = onnxRuntime.lastRawConfidenceSnapshot()
                            ?: error("Runtime did not expose a raw classifier score.")
                        trace.writeInference(
                            inferenceIndex = index,
                            effectiveScore = effectiveScore,
                            rawScore = rawScore,
                            melHistory = onnxRuntime.melHistorySnapshot(),
                            embedding = onnxRuntime.latestEmbeddingSnapshot(),
                        )
                        maximumEffectiveScore = max(maximumEffectiveScore, effectiveScore)
                        maximumRawScore = max(maximumRawScore, rawScore)
                        completedInferences += 1
                    }
                }
            }
        } catch (exception: Exception) {
            runtimeErrors += 1
            traceFile.delete()
            throw IllegalStateException(
                "Android wake replay failed for ${file.name}, run $repetition: ${exception.message}",
                exception,
            )
        } finally {
            runtime.close()
        }

        check(completedInferences == inferenceCount) {
            "Replay completed $completedInferences of $inferenceCount inferences."
        }
        return WakeWordReplayResult(
            captureId = file.nameWithoutExtension,
            pcmFileName = file.name,
            pcmSha256 = file.sha256(),
            repetition = repetition,
            traceFileName = traceFile.name,
            inferenceCount = completedInferences,
            maximumEffectiveScore = maximumEffectiveScore,
            maximumRawScore = maximumRawScore,
            runtimeErrorCount = runtimeErrors,
            elapsedMs = (System.nanoTime() - startedNanos) / NANOS_PER_MILLISECOND,
        )
    }

    private fun validatePcmFile(file: File) {
        require(file.isFile && file.length() > 0L) { "PCM file is missing or empty: ${file.name}" }
        require(file.length() % BYTES_PER_INFERENCE_WINDOW == 0L) {
            "PCM file ${file.name} is not aligned to $inferenceSamples-sample hops."
        }
    }

    private fun readFully(input: FileInputStream, destination: ByteArray) {
        var offset = 0
        while (offset < destination.size) {
            val read = input.read(destination, offset, destination.size - offset)
            check(read > 0) { "Unexpected end of diagnostic PCM file." }
            offset += read
        }
    }

    private fun decodeLittleEndianPcm16(source: ByteArray, destination: ShortArray) {
        for (index in destination.indices) {
            val low = source[index * 2].toInt() and 0xFF
            val high = source[index * 2 + 1].toInt()
            destination[index] = ((high shl 8) or low).toShort()
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

    private val BYTES_PER_INFERENCE_WINDOW: Int
        get() = inferenceSamples * PCM16_BYTES_PER_SAMPLE

    private class WakeWordTraceWriter(
        file: File,
        sampleRateHz: Int,
        inferenceSamples: Int,
        inferenceCount: Int,
    ) : AutoCloseable {
        private val output = FileOutputStream(file)
        private val recordBuffer = ByteBuffer.allocate(
            (RECORD_PREFIX_VALUES + MEL_HISTORY_VALUES + EMBEDDING_VALUES) * Int.SIZE_BYTES,
        ).order(ByteOrder.LITTLE_ENDIAN)

        init {
            output.write(TRACE_MAGIC)
            val header = ByteBuffer.allocate(HEADER_INTEGER_COUNT * Int.SIZE_BYTES)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(TRACE_VERSION)
                .putInt(sampleRateHz)
                .putInt(inferenceSamples)
                .putInt(MEL_HISTORY_VALUES)
                .putInt(EMBEDDING_VALUES)
                .putInt(inferenceCount)
            output.write(header.array())
        }

        fun writeInference(
            inferenceIndex: Int,
            effectiveScore: Float,
            rawScore: Float,
            melHistory: FloatArray,
            embedding: FloatArray,
        ) {
            require(melHistory.size == MEL_HISTORY_VALUES)
            require(embedding.size == EMBEDDING_VALUES)
            recordBuffer.clear()
            recordBuffer.putInt(inferenceIndex)
            recordBuffer.putFloat(effectiveScore)
            recordBuffer.putFloat(rawScore)
            melHistory.forEach(recordBuffer::putFloat)
            embedding.forEach(recordBuffer::putFloat)
            output.write(recordBuffer.array(), 0, recordBuffer.position())
        }

        override fun close() {
            output.fd.sync()
            output.close()
        }
    }

    companion object {
        private val TRACE_MAGIC = byteArrayOf(
            'O'.code.toByte(), 'W'.code.toByte(), 'W'.code.toByte(), 'T'.code.toByte(),
            'R'.code.toByte(), 'C'.code.toByte(), '1'.code.toByte(), 0,
        )
        private const val TRACE_VERSION = 1
        private const val HEADER_INTEGER_COUNT = 6
        private const val RECORD_PREFIX_VALUES = 3
        private const val MEL_HISTORY_VALUES =
            ApprovedOpenWakeWordModel.MEL_HISTORY_FRAMES * ApprovedOpenWakeWordModel.MEL_BINS
        private const val EMBEDDING_VALUES = ApprovedOpenWakeWordModel.CLASSIFIER_FEATURE_SIZE
        private const val PCM16_BYTES_PER_SAMPLE = 2
        private const val TRACE_EXTENSION = "owwtrace"
        private const val MAX_REPETITIONS = 3
        private const val SHA256_ALGORITHM = "SHA-256"
        private const val HASH_BUFFER_BYTES = 8_192
        private const val NANOS_PER_MILLISECOND = 1_000_000.0
    }
}
