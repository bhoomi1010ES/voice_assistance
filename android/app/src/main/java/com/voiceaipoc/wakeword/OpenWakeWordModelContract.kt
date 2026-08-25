package com.voiceaipoc.wakeword

/** Immutable graph metadata read from the approved upstream ONNX binaries. */
data class WakeWordTensorContract(
    val name: String,
    val type: WakeWordTensorType,
    /** ONNX Runtime represents dynamic graph dimensions as -1. */
    val graphShape: List<Long>,
)

enum class WakeWordTensorType {
    FLOAT32,
}

data class WakeWordArtifactContract(
    val fileName: String,
    val assetPath: String,
    val officialUrl: String,
    val sizeBytes: Long,
    val sha256: String,
    val onnxIrVersion: Int,
    val onnxOpsets: Map<String, Int>,
    val producerName: String,
    val producerVersion: String,
    val inputs: List<WakeWordTensorContract>,
    val outputs: List<WakeWordTensorContract>,
)

data class OpenWakeWordModelContract(
    val targetPhrase: String,
    val modelName: String,
    val artifactVersion: String,
    val releaseTag: String,
    val gitCommit: String,
    val repositoryUrl: String,
    val license: String,
    val modelFormat: String,
    val sampleRateHz: Int,
    val pcmFormat: String,
    val inferenceSamples: Int,
    val inferenceDurationMs: Int,
    val melContextSamples: Int,
    val classifierHistoryFrames: Int,
    val classifierFeatureSize: Int,
    val baselineThreshold: Float,
    val artifacts: List<WakeWordArtifactContract>,
)

/** Exact official openWakeWord v0.5.1 release chain approved for Phase 0.7. */
object ApprovedOpenWakeWordModel {
    const val TARGET_PHRASE = "hey_jarvis"
    const val MODEL_NAME = "hey_jarvis"
    const val ARTIFACT_VERSION = "v0.1"
    const val RELEASE_TAG = "v0.5.1"
    const val GIT_COMMIT = "1eec2158c5c54150ac5f4c15065adacb1003b1e7"
    const val REPOSITORY_URL = "https://github.com/dscripka/openWakeWord"
    const val RELEASE_BASE_URL =
        "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
    const val LICENSE = "CC BY-NC-SA 4.0"
    const val MODEL_FORMAT = "ONNX"
    const val ASSET_DIRECTORY = "openwakeword"

    const val SAMPLE_RATE_HZ = 16_000
    const val PCM_FORMAT = "PCM16_LE_SIGNED_MONO"
    const val INFERENCE_SAMPLES = 1_280
    const val INFERENCE_DURATION_MS = 80
    const val MEL_CONTEXT_SAMPLES = 480
    const val MEL_BINS = 32
    const val MEL_HISTORY_FRAMES = 76
    const val MEL_FRAMES_FIRST_INFERENCE = 5
    const val MEL_FRAMES_STEADY_INFERENCE = 8
    const val CLASSIFIER_HISTORY_FRAMES = 16
    const val CLASSIFIER_FEATURE_SIZE = 96
    const val INITIAL_SCORE_SUPPRESSION_CALLS = 5
    const val BASELINE_THRESHOLD = 0.5f

    const val MEL_FILE_NAME = "melspectrogram.onnx"
    const val EMBEDDING_FILE_NAME = "embedding_model.onnx"
    const val CLASSIFIER_FILE_NAME = "hey_jarvis_v0.1.onnx"
    const val MEL_ASSET_PATH = "$ASSET_DIRECTORY/$MEL_FILE_NAME"
    const val EMBEDDING_ASSET_PATH = "$ASSET_DIRECTORY/$EMBEDDING_FILE_NAME"
    const val CLASSIFIER_ASSET_PATH = "$ASSET_DIRECTORY/$CLASSIFIER_FILE_NAME"

    const val MEL_INPUT_NAME = "input"
    const val MEL_OUTPUT_NAME = "output"
    const val EMBEDDING_INPUT_NAME = "input_1"
    const val EMBEDDING_OUTPUT_NAME = "conv2d_19"
    const val CLASSIFIER_INPUT_NAME = "x.1"
    const val CLASSIFIER_OUTPUT_NAME = "53"

    val MEL_ARTIFACT = WakeWordArtifactContract(
        fileName = MEL_FILE_NAME,
        assetPath = MEL_ASSET_PATH,
        officialUrl = "$RELEASE_BASE_URL/$MEL_FILE_NAME",
        sizeBytes = 1_087_958L,
        sha256 = "BA2B0E0F8B7B875369A2C89CB13360FF53BAC436F2895CCED9F479FA65EB176F",
        onnxIrVersion = 7,
        onnxOpsets = mapOf("ai.onnx" to 13),
        producerName = "pytorch",
        producerVersion = "1.11.0",
        inputs = listOf(
            WakeWordTensorContract(MEL_INPUT_NAME, WakeWordTensorType.FLOAT32, listOf(-1L, -1L)),
        ),
        outputs = listOf(
            WakeWordTensorContract(
                MEL_OUTPUT_NAME,
                WakeWordTensorType.FLOAT32,
                listOf(-1L, 1L, -1L, MEL_BINS.toLong()),
            ),
        ),
    )

    val EMBEDDING_ARTIFACT = WakeWordArtifactContract(
        fileName = EMBEDDING_FILE_NAME,
        assetPath = EMBEDDING_ASSET_PATH,
        officialUrl = "$RELEASE_BASE_URL/$EMBEDDING_FILE_NAME",
        sizeBytes = 1_326_578L,
        sha256 = "70D164290C1D095D1D4EE149BC5E00543250A7316B59F31D056CFF7BD3075C1F",
        onnxIrVersion = 7,
        onnxOpsets = mapOf("ai.onnx" to 13, "ai.onnx.ml" to 2),
        producerName = "tf2onnx",
        producerVersion = "1.12.1 b6d590",
        inputs = listOf(
            WakeWordTensorContract(
                EMBEDDING_INPUT_NAME,
                WakeWordTensorType.FLOAT32,
                listOf(-1L, MEL_HISTORY_FRAMES.toLong(), MEL_BINS.toLong(), 1L),
            ),
        ),
        outputs = listOf(
            WakeWordTensorContract(
                EMBEDDING_OUTPUT_NAME,
                WakeWordTensorType.FLOAT32,
                listOf(-1L, 1L, 1L, CLASSIFIER_FEATURE_SIZE.toLong()),
            ),
        ),
    )

    val CLASSIFIER_ARTIFACT = WakeWordArtifactContract(
        fileName = CLASSIFIER_FILE_NAME,
        assetPath = CLASSIFIER_ASSET_PATH,
        officialUrl = "$RELEASE_BASE_URL/$CLASSIFIER_FILE_NAME",
        sizeBytes = 1_271_370L,
        sha256 = "94A13CFE60075B132F6A472E7E462E8123EE70861BC3FB58434A73712EE0D2CB",
        onnxIrVersion = 7,
        onnxOpsets = mapOf("ai.onnx" to 13),
        producerName = "pytorch",
        producerVersion = "1.12.1",
        inputs = listOf(
            WakeWordTensorContract(
                CLASSIFIER_INPUT_NAME,
                WakeWordTensorType.FLOAT32,
                listOf(1L, CLASSIFIER_HISTORY_FRAMES.toLong(), CLASSIFIER_FEATURE_SIZE.toLong()),
            ),
        ),
        outputs = listOf(
            WakeWordTensorContract(
                CLASSIFIER_OUTPUT_NAME,
                WakeWordTensorType.FLOAT32,
                listOf(1L, 1L),
            ),
        ),
    )

    val CONTRACT = OpenWakeWordModelContract(
        targetPhrase = TARGET_PHRASE,
        modelName = MODEL_NAME,
        artifactVersion = ARTIFACT_VERSION,
        releaseTag = RELEASE_TAG,
        gitCommit = GIT_COMMIT,
        repositoryUrl = REPOSITORY_URL,
        license = LICENSE,
        modelFormat = MODEL_FORMAT,
        sampleRateHz = SAMPLE_RATE_HZ,
        pcmFormat = PCM_FORMAT,
        inferenceSamples = INFERENCE_SAMPLES,
        inferenceDurationMs = INFERENCE_DURATION_MS,
        melContextSamples = MEL_CONTEXT_SAMPLES,
        classifierHistoryFrames = CLASSIFIER_HISTORY_FRAMES,
        classifierFeatureSize = CLASSIFIER_FEATURE_SIZE,
        baselineThreshold = BASELINE_THRESHOLD,
        artifacts = listOf(MEL_ARTIFACT, EMBEDDING_ARTIFACT, CLASSIFIER_ARTIFACT),
    )
}
