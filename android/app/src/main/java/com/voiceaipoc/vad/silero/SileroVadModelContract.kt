package com.voiceaipoc.vad.silero

/** Immutable identity and graph contract for the approved upstream binary. */
data class SileroVadTensorContract(
    val name: String,
    val type: SileroVadTensorType,
    /** ONNX Runtime represents dynamic graph dimensions as -1. */
    val graphShape: List<Long>,
)

enum class SileroVadTensorType {
    FLOAT32,
    INT64,
}

data class SileroVadModelContract(
    val modelName: String,
    val version: String,
    val gitTag: String,
    val gitCommit: String,
    val fileName: String,
    val assetPath: String,
    val sizeBytes: Long,
    val sha256: String,
    val onnxOpset: Int,
    val onnxIrVersion: Int,
    val producerName: String,
    val inputs: List<SileroVadTensorContract>,
    val outputs: List<SileroVadTensorContract>,
)

object ApprovedSileroVadModel {
    const val MODEL_NAME = "Silero VAD"
    const val VERSION = "6.2.1"
    const val GIT_TAG = "v6.2.1"
    const val GIT_COMMIT = "7e30209"
    const val FILE_NAME = "silero_vad.onnx"
    const val ASSET_DIRECTORY = "silero_vad"
    const val ASSET_PATH = "$ASSET_DIRECTORY/$FILE_NAME"
    const val SIZE_BYTES = 2_327_524L
    const val SHA256 = "1A153A22F4509E292A94E67D6F9B85E8DEB25B4988682B7E174C65279D8788E3"
    const val ONNX_OPSET = 16
    const val ONNX_IR_VERSION = 8
    const val PRODUCER_NAME = "spox"

    const val AUDIO_INPUT_NAME = "input"
    const val STATE_INPUT_NAME = "state"
    const val SAMPLE_RATE_INPUT_NAME = "sr"
    const val PROBABILITY_OUTPUT_NAME = "output"
    const val STATE_OUTPUT_NAME = "stateN"

    val CONTRACT = SileroVadModelContract(
        modelName = MODEL_NAME,
        version = VERSION,
        gitTag = GIT_TAG,
        gitCommit = GIT_COMMIT,
        fileName = FILE_NAME,
        assetPath = ASSET_PATH,
        sizeBytes = SIZE_BYTES,
        sha256 = SHA256,
        onnxOpset = ONNX_OPSET,
        onnxIrVersion = ONNX_IR_VERSION,
        producerName = PRODUCER_NAME,
        inputs = listOf(
            SileroVadTensorContract(
                AUDIO_INPUT_NAME,
                SileroVadTensorType.FLOAT32,
                listOf(-1L, -1L),
            ),
            SileroVadTensorContract(
                STATE_INPUT_NAME,
                SileroVadTensorType.FLOAT32,
                listOf(2L, -1L, 128L),
            ),
            SileroVadTensorContract(
                SAMPLE_RATE_INPUT_NAME,
                SileroVadTensorType.INT64,
                emptyList(),
            ),
        ),
        outputs = listOf(
            SileroVadTensorContract(
                PROBABILITY_OUTPUT_NAME,
                SileroVadTensorType.FLOAT32,
                listOf(-1L, 1L),
            ),
            SileroVadTensorContract(
                STATE_OUTPUT_NAME,
                SileroVadTensorType.FLOAT32,
                listOf(-1L, -1L, -1L),
            ),
        ),
    )
}
