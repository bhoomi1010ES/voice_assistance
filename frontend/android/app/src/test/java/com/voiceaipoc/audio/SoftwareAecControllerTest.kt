package com.voiceaipoc.audio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SoftwareAecControllerTest {
    @Test
    fun `normalizes a 20 ms capture frame into two 10 ms render and capture calls`() {
        val fake = FakeEchoCanceller()
        val controller = SoftwareAecController(
            mode = AudioConfig.SoftwareAecMode.WEBRTC_AEC3,
            sampleRateHz = 16_000,
            renderToCaptureDelayMs = 80,
            enableAec = true,
            enableNoiseSuppression = false,
            cancellerFactory = { fake },
        )
        controller.start()

        val input = ShortArray(320) { 1_000 }
        val output = ShortArray(320)
        val processed = controller.processFrame(
            pcm = input,
            samplesRead = input.size,
            captureStartNs = 1_000_000_000L,
            captureEndNs = 1_020_000_000L,
            referenceBuffer = FarEndReferenceBuffer(),
            output = output,
        )

        assertTrue(processed)
        assertEquals(2, fake.renderCalls)
        assertEquals(2, fake.captureCalls)
        assertEquals(2L, controller.status().captureFrames)
        assertEquals(2L, controller.status().referenceMissingFrames)
        assertEquals(500, output[0].toInt())
        assertEquals(500, output[319].toInt())
    }

    @Test
    fun `capture failure enters degraded bypass and preserves original PCM`() {
        val fake = FakeEchoCanceller(failCapture = true)
        val controller = SoftwareAecController(
            mode = AudioConfig.SoftwareAecMode.WEBRTC_AEC3,
            sampleRateHz = 16_000,
            renderToCaptureDelayMs = 80,
            enableAec = true,
            enableNoiseSuppression = false,
            cancellerFactory = { fake },
        )
        controller.start()

        val input = ShortArray(320) { 777 }
        val output = ShortArray(320)
        val processed = controller.processFrame(
            pcm = input,
            samplesRead = input.size,
            captureStartNs = 1_000_000_000L,
            captureEndNs = 1_020_000_000L,
            referenceBuffer = FarEndReferenceBuffer(),
            output = output,
        )

        assertFalse(processed)
        assertEquals(777, output[0].toInt())
        assertEquals(777, output[319].toInt())
        assertEquals(SoftwareAecController.State.DEGRADED, controller.status().state)
        assertTrue(controller.status().droppedFrames > 0)
    }

    @Test
    fun `auto mode can remain platform owned when software selection is disabled`() {
        val controller = SoftwareAecController(
            mode = AudioConfig.SoftwareAecMode.AUTO,
            enabled = false,
            sampleRateHz = 16_000,
            renderToCaptureDelayMs = 80,
            enableAec = true,
            enableNoiseSuppression = false,
        )

        val input = ShortArray(320) { 42 }
        val output = ShortArray(320)
        val processed = controller.processFrame(
            pcm = input,
            samplesRead = input.size,
            captureStartNs = 0L,
            captureEndNs = 20_000_000L,
            referenceBuffer = FarEndReferenceBuffer(),
            output = output,
        )

        assertFalse(processed)
        assertEquals(SoftwareAecController.State.DISABLED, controller.status().state)
        assertFalse(controller.status().platformAecDisabled)
    }

    private class FakeEchoCanceller(
        private val failCapture: Boolean = false,
    ) : EchoCanceller {
        var renderCalls = 0
        var captureCalls = 0
        private var config: EchoCanceller.Config? = null

        override fun start(config: EchoCanceller.Config): Boolean {
            this.config = config
            return true
        }

        override fun processRender(
            pcm: ShortArray,
            offset: Int,
            count: Int,
            presentationTimestampNs: Long,
            referenceReady: Boolean,
        ): Int {
            renderCalls += 1
            return EchoCanceller.PROCESS_OK
        }

        override fun processCapture(
            input: ShortArray,
            inputOffset: Int,
            output: ShortArray,
            outputOffset: Int,
            count: Int,
            captureTimestampNs: Long,
        ): Int {
            captureCalls += 1
            if (failCapture) return EchoCanceller.PROCESS_ERROR
            for (index in 0 until count) {
                output[outputOffset + index] = (input[inputOffset + index] / 2).toShort()
            }
            return EchoCanceller.PROCESS_OK
        }

        override fun reset() = Unit

        override fun stop() = Unit

        override fun metrics(): EchoCanceller.Metrics = EchoCanceller.Metrics(
            state = if (failCapture) EchoCanceller.State.ACTIVE else EchoCanceller.State.ACTIVE,
            implementation = "FAKE_AEC3",
            sampleRateHz = config?.sampleRateHz ?: 16_000,
            frameSizeSamples = config?.frameSizeSamples ?: 160,
            streamDelayMs = config?.streamDelayMs ?: 80,
            aecRequested = true,
            noiseSuppressionRequested = false,
            renderFrames = renderCalls.toLong(),
            captureFrames = captureCalls.toLong(),
            processedFrames = if (failCapture) 0 else captureCalls.toLong(),
            bypassedFrames = 0,
            renderDropCount = 0,
            processingErrorCount = if (failCapture) captureCalls.toLong() else 0,
            lastInputRms = 0.0,
            lastOutputRms = 0.0,
            lastError = if (failCapture) "fake failure" else null,
        )
    }
}
