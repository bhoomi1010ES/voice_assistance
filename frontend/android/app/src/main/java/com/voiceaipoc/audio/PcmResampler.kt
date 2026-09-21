package com.voiceaipoc.audio

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.sin

/**
 * Stateful mono PCM16 resampler.
 *
 * The implementation is deliberately allocation-free after construction. A
 * causal windowed-sinc FIR keeps the 24 kHz TTS stream continuous when input
 * chunks do not line up with the resampling ratio and attenuates content above
 * the 16 kHz Nyquist frequency before decimation.
 */
class PcmResampler(
    private val inputSampleRateHz: Int,
    private val outputSampleRateHz: Int,
    tapCount: Int = DEFAULT_TAP_COUNT,
    phaseCount: Int = DEFAULT_PHASE_COUNT,
) {
    init {
        require(inputSampleRateHz > 0) { "inputSampleRateHz must be positive" }
        require(outputSampleRateHz > 0) { "outputSampleRateHz must be positive" }
        require(tapCount >= 8 && tapCount % 2 == 0) { "tapCount must be even and >= 8" }
        require(phaseCount >= 2) { "phaseCount must be >= 2" }
    }

    private val taps = tapCount
    private val phases = phaseCount
    private val history = ShortArray(taps)
    private val coefficients = Array(phases) { FloatArray(taps) }
    private val inputStep = inputSampleRateHz.toDouble() / outputSampleRateHz.toDouble()
    private val cutoff = minOf(1.0, outputSampleRateHz.toDouble() / inputSampleRateHz.toDouble())
    private val filterDelay = (taps - 1).toDouble() / 2.0

    private var historyWriteIndex = 0
    private var inputFramesSeen = 0L
    private var nextOutputInputPosition = (taps - 1).toDouble()
    private var outputFramesProduced = 0L

    init {
        buildCoefficients()
    }

    val outputFramesProducedCount: Long
        get() = outputFramesProduced

    val inputFramesSeenCount: Long
        get() = inputFramesSeen

    fun reset() {
        history.fill(0)
        historyWriteIndex = 0
        inputFramesSeen = 0L
        nextOutputInputPosition = (taps - 1).toDouble()
        outputFramesProduced = 0L
    }

    /** Processes signed PCM16 samples into the caller-owned output buffer. */
    fun process(
        input: ShortArray,
        inputOffset: Int,
        inputCount: Int,
        output: ShortArray,
        outputOffset: Int = 0,
    ): Int {
        require(inputOffset >= 0 && inputCount >= 0 && inputOffset + inputCount <= input.size)
        require(outputOffset >= 0 && outputOffset <= output.size)

        if (inputSampleRateHz == outputSampleRateHz) {
            require(output.size - outputOffset >= inputCount) { "output buffer is too small" }
            input.copyInto(output, outputOffset, inputOffset, inputOffset + inputCount)
            inputFramesSeen += inputCount.toLong()
            outputFramesProduced += inputCount.toLong()
            return inputCount
        }

        var produced = 0
        for (index in inputOffset until inputOffset + inputCount) {
            push(input[index])
            while (canProduce()) {
                require(outputOffset + produced < output.size) { "output buffer is too small" }
                output[outputOffset + produced] = renderNext()
                produced += 1
            }
        }
        return produced
    }

    /** Processes a caller-owned little-endian PCM16 byte range. */
    fun processPcm16(
        input: ByteArray,
        inputOffsetBytes: Int,
        byteCount: Int,
        output: ShortArray,
        outputOffset: Int = 0,
    ): Int {
        require(inputOffsetBytes >= 0 && byteCount >= 0 && inputOffsetBytes + byteCount <= input.size)
        require(byteCount % BYTES_PER_SAMPLE == 0) { "PCM16 byteCount must be even" }
        if (inputSampleRateHz == outputSampleRateHz) {
            require(output.size - outputOffset >= byteCount / BYTES_PER_SAMPLE) {
                "output buffer is too small"
            }
            var out = outputOffset
            var inputIndex = inputOffsetBytes
            val end = inputOffsetBytes + byteCount
            while (inputIndex < end) {
                output[out++] = decodePcm16(input, inputIndex)
                inputIndex += BYTES_PER_SAMPLE
            }
            inputFramesSeen += byteCount / BYTES_PER_SAMPLE.toLong()
            outputFramesProduced += byteCount / BYTES_PER_SAMPLE.toLong()
            return byteCount / BYTES_PER_SAMPLE
        }

        var produced = 0
        var inputIndex = inputOffsetBytes
        val end = inputOffsetBytes + byteCount
        while (inputIndex < end) {
            push(decodePcm16(input, inputIndex))
            while (canProduce()) {
                require(outputOffset + produced < output.size) { "output buffer is too small" }
                output[outputOffset + produced] = renderNext()
                produced += 1
            }
            inputIndex += BYTES_PER_SAMPLE
        }
        return produced
    }

    private fun buildCoefficients() {
        for (phaseIndex in 0 until phases) {
            val fractional = phaseIndex.toDouble() / phases.toDouble()
            var sum = 0.0
            for (tap in 0 until taps) {
                val distance = tap.toDouble() - filterDelay - fractional
                val sinc = if (distance == 0.0) 1.0 else sin(PI * cutoff * distance) / (PI * distance)
                val window = 0.5 - 0.5 * cos(2.0 * PI * tap / (taps - 1).toDouble())
                val coefficient = cutoff * sinc * window
                coefficients[phaseIndex][tap] = coefficient.toFloat()
                sum += coefficient
            }
            if (sum != 0.0) {
                for (tap in 0 until taps) {
                    coefficients[phaseIndex][tap] = (coefficients[phaseIndex][tap] / sum).toFloat()
                }
            }
        }
    }

    private fun push(sample: Short) {
        history[historyWriteIndex] = sample
        historyWriteIndex = (historyWriteIndex + 1) % taps
        inputFramesSeen += 1L
    }

    private fun canProduce(): Boolean =
        inputFramesSeen - 1L >= floor(nextOutputInputPosition).toLong()

    private fun renderNext(): Short {
        val sourceIndex = floor(nextOutputInputPosition).toLong()
        val fractional = nextOutputInputPosition - sourceIndex.toDouble()
        val phaseIndex = (fractional * phases.toDouble()).toInt().coerceIn(0, phases - 1)
        val phase = coefficients[phaseIndex]
        var sum = 0.0
        for (tap in 0 until taps) {
            val historyIndex = mod(historyWriteIndex - 1 - tap, taps)
            sum += history[historyIndex].toDouble() * phase[tap].toDouble()
        }
        nextOutputInputPosition += inputStep
        outputFramesProduced += 1L
        return sum.toInt().coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt()).toShort()
    }

    private fun mod(value: Int, divisor: Int): Int {
        val result = value % divisor
        return if (result < 0) result + divisor else result
    }

    private fun decodePcm16(input: ByteArray, offset: Int): Short = (
        (input[offset].toInt() and 0xff) or
            (input[offset + 1].toInt() shl 8)
        ).toShort()

    companion object {
        const val DEFAULT_TAP_COUNT = 32
        const val DEFAULT_PHASE_COUNT = 64
        private const val BYTES_PER_SAMPLE = 2
    }
}
