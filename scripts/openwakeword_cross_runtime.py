#!/usr/bin/env python3
"""Cross-runtime replay for the approved openWakeWord v0.5.1 model chain.

The reference runtime is the official package pinned to tag v0.5.1 / commit
1eec2158c5c54150ac5f4c15065adacb1003b1e7. This script never records,
uploads, or transforms PCM; it reads explicitly captured little-endian PCM
files and temporary Android feature traces from local ignored directories.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
import openwakeword


OFFICIAL_TAG = "v0.5.1"
OFFICIAL_COMMIT = "1eec2158c5c54150ac5f4c15065adacb1003b1e7"
OFFICIAL_VERSION = "0.5.1"
SAMPLE_RATE = 16_000
INFERENCE_SAMPLES = 1_280
MEL_HISTORY_SHAPE = (76, 32)
EMBEDDING_SIZE = 96
CLASSIFIER_HISTORY = 16
STARTUP_SUPPRESSION = 5
REFERENCE_SEED = 0
TRACE_MAGIC = b"OWWTRC1\x00"
TRACE_VERSION = 1
MODEL_HASHES = {
    "melspectrogram.onnx": "BA2B0E0F8B7B875369A2C89CB13360FF53BAC436F2895CCED9F479FA65EB176F",
    "embedding_model.onnx": "70D164290C1D095D1D4EE149BC5E00543250A7316B59F31D056CFF7BD3075C1F",
    "hey_mycroft_v0.1.onnx": "C2A311E8FA1338DE89C31B3B46DC4DFFD4AF2F9A8D6DDEAD48893C2D301B1F18",
}


@dataclass(frozen=True)
class Trace:
    sample_rate: int
    inference_samples: int
    effective_scores: np.ndarray
    raw_scores: np.ndarray
    mel_histories: np.ndarray
    embeddings: np.ndarray


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def verify_environment(model_dir: Path) -> dict[str, Any]:
    installed_version = importlib.metadata.version("openwakeword")
    if installed_version != OFFICIAL_VERSION:
        raise RuntimeError(
            f"Expected openwakeword {OFFICIAL_VERSION}, found {installed_version}."
        )
    hashes: dict[str, str] = {}
    for name, expected in MODEL_HASHES.items():
        path = model_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Approved model is missing: {path}")
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Approved model hash mismatch for {name}: {actual}")
        hashes[name] = actual
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "onnxruntime": ort.__version__,
        "openwakeword": installed_version,
        "openwakewordTag": OFFICIAL_TAG,
        "openwakewordCommit": OFFICIAL_COMMIT,
        "modelHashes": hashes,
    }


def create_official_model(model_dir: Path) -> Any:
    # v0.5.1 seeds AudioFeatures.feature_buffer from random PCM. Pinning NumPy's
    # seed makes the unmodified official initialization deterministic.
    np.random.seed(REFERENCE_SEED)
    return openwakeword.Model(
        wakeword_models=[str(model_dir / "hey_mycroft_v0.1.onnx")],
        inference_framework="onnx",
        melspec_model_path=str(model_dir / "melspectrogram.onnx"),
        embedding_model_path=str(model_dir / "embedding_model.onnx"),
        ncpu=1,
    )


def run_official_reference(pcm: np.ndarray, model_dir: Path) -> Trace:
    if pcm.dtype != np.int16 or pcm.ndim != 1:
        raise ValueError("Reference PCM must be a one-dimensional int16 array.")
    if pcm.size == 0 or pcm.size % INFERENCE_SAMPLES != 0:
        raise ValueError("Reference PCM must contain complete 1,280-sample hops.")

    model = create_official_model(model_dir)
    model_name = next(iter(model.models))
    effective_scores: list[float] = []
    raw_scores: list[float] = []
    mel_histories: list[np.ndarray] = []
    embeddings: list[np.ndarray] = []

    for offset in range(0, pcm.size, INFERENCE_SAMPLES):
        chunk = pcm[offset : offset + INFERENCE_SAMPLES]
        prediction = model.predict(chunk)
        effective_scores.append(float(prediction[model_name]))
        features = model.preprocessor.get_features(model.model_inputs[model_name])
        raw = model.model_prediction_function[model_name](features)
        raw_scores.append(float(np.asarray(raw).reshape(-1)[0]))
        mel_histories.append(
            np.asarray(
                model.preprocessor.melspectrogram_buffer[-MEL_HISTORY_SHAPE[0] :],
                dtype=np.float32,
            ).copy()
        )
        embeddings.append(
            np.asarray(model.preprocessor.feature_buffer[-1], dtype=np.float32).copy()
        )

    return Trace(
        sample_rate=SAMPLE_RATE,
        inference_samples=INFERENCE_SAMPLES,
        effective_scores=np.asarray(effective_scores, dtype=np.float32),
        raw_scores=np.asarray(raw_scores, dtype=np.float32),
        mel_histories=np.asarray(mel_histories, dtype=np.float32),
        embeddings=np.asarray(embeddings, dtype=np.float32),
    )


def read_android_trace(path: Path) -> Trace:
    data = memoryview(path.read_bytes())
    if data[: len(TRACE_MAGIC)].tobytes() != TRACE_MAGIC:
        raise ValueError(f"Invalid Android trace magic: {path}")
    offset = len(TRACE_MAGIC)
    version, sample_rate, inference_samples, mel_values, embedding_values, count = (
        struct.unpack_from("<6i", data, offset)
    )
    offset += struct.calcsize("<6i")
    if version != TRACE_VERSION:
        raise ValueError(f"Unsupported trace version {version} in {path}")
    if mel_values != int(np.prod(MEL_HISTORY_SHAPE)) or embedding_values != EMBEDDING_SIZE:
        raise ValueError(f"Unexpected feature dimensions in {path}")

    effective = np.empty(count, dtype=np.float32)
    raw = np.empty(count, dtype=np.float32)
    mel = np.empty((count, *MEL_HISTORY_SHAPE), dtype=np.float32)
    embeddings = np.empty((count, EMBEDDING_SIZE), dtype=np.float32)
    record_float_count = 2 + mel_values + embedding_values
    record_bytes = 4 + record_float_count * 4
    for expected_index in range(1, count + 1):
        index = struct.unpack_from("<i", data, offset)[0]
        if index != expected_index:
            raise ValueError(f"Non-sequential trace index {index} in {path}")
        values = np.frombuffer(
            data[offset + 4 : offset + record_bytes], dtype="<f4", count=record_float_count
        )
        effective[expected_index - 1] = values[0]
        raw[expected_index - 1] = values[1]
        mel[expected_index - 1] = values[2 : 2 + mel_values].reshape(MEL_HISTORY_SHAPE)
        embeddings[expected_index - 1] = values[2 + mel_values :]
        offset += record_bytes
    if offset != len(data):
        raise ValueError(f"Unexpected trailing bytes in {path}")
    return Trace(sample_rate, inference_samples, effective, raw, mel, embeddings)


def difference(actual: np.ndarray, reference: np.ndarray) -> dict[str, float]:
    if actual.shape != reference.shape:
        raise ValueError(f"Shape mismatch: {actual.shape} != {reference.shape}")
    absolute = np.abs(actual.astype(np.float64) - reference.astype(np.float64))
    denominator = np.maximum(np.abs(reference.astype(np.float64)), 1e-7)
    relative = absolute / denominator
    return {
        "maximumAbsolute": float(absolute.max(initial=0.0)),
        "meanAbsolute": float(absolute.mean()) if absolute.size else 0.0,
        "maximumRelative": float(relative.max(initial=0.0)),
        "meanRelative": float(relative.mean()) if relative.size else 0.0,
    }


def trace_differences(actual: Trace, reference: Trace) -> dict[str, Any]:
    if actual.sample_rate != reference.sample_rate:
        raise ValueError("Trace sample-rate mismatch.")
    if actual.inference_samples != reference.inference_samples:
        raise ValueError("Trace inference-hop mismatch.")
    warm = CLASSIFIER_HISTORY - 1
    return {
        "melAllHops": difference(actual.mel_histories, reference.mel_histories),
        "embeddingAllHops": difference(actual.embeddings, reference.embeddings),
        "rawClassifierStartupHops": difference(
            actual.raw_scores[:warm], reference.raw_scores[:warm]
        ),
        "rawClassifierPostWarmup": difference(
            actual.raw_scores[warm:], reference.raw_scores[warm:]
        ),
        "effectiveScorePostWarmup": difference(
            actual.effective_scores[warm:], reference.effective_scores[warm:]
        ),
    }


def score_summary(trace: Trace) -> dict[str, Any]:
    post_warmup = trace.effective_scores[CLASSIFIER_HISTORY - 1 :]
    return {
        "inferenceCount": int(trace.effective_scores.size),
        "maximumEffectiveScore": float(trace.effective_scores.max(initial=0.0)),
        "maximumPostWarmupEffectiveScore": float(post_warmup.max(initial=0.0)),
        "maximumRawScore": float(trace.raw_scores.max(initial=0.0)),
        "averageEffectiveScore": float(trace.effective_scores.mean()),
        "effectiveScores": [float(value) for value in trace.effective_scores],
        "rawScores": [float(value) for value in trace.raw_scores],
    }


def validate_capture(
    pcm_path: Path,
    trace_dir: Path,
    model_dir: Path,
) -> dict[str, Any]:
    pcm = np.fromfile(pcm_path, dtype="<i2")
    python_run_1 = run_official_reference(pcm, model_dir)
    python_run_2 = run_official_reference(pcm, model_dir)
    android_run_1 = read_android_trace(
        trace_dir / f"{pcm_path.stem}_android_run1.owwtrace"
    )
    android_run_2 = read_android_trace(
        trace_dir / f"{pcm_path.stem}_android_run2.owwtrace"
    )
    return {
        "waveformId": pcm_path.stem,
        "pcmFileName": pcm_path.name,
        "pcmSha256": sha256(pcm_path),
        "pcmSamples": int(pcm.size),
        "pcmInferenceHops": int(pcm.size // INFERENCE_SAMPLES),
        "python": score_summary(python_run_1),
        "android": score_summary(android_run_1),
        "determinism": {
            "python": trace_differences(python_run_1, python_run_2),
            "android": trace_differences(android_run_1, android_run_2),
        },
        "crossRuntime": trace_differences(android_run_1, python_run_1),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcm-dir", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    environment = verify_environment(args.model_dir)
    pcm_files = sorted(args.pcm_dir.glob("*.pcm"))
    if not pcm_files:
        raise RuntimeError(f"No diagnostic PCM files found in {args.pcm_dir}")
    captures = [validate_capture(path, args.trace_dir, args.model_dir) for path in pcm_files]
    output = {
        "diagnosticOnly": True,
        "officialReference": {
            "tag": OFFICIAL_TAG,
            "commit": OFFICIAL_COMMIT,
            "startupSeed": REFERENCE_SEED,
            "startupHistoryNote": (
                "Official v0.5.1 random history is deterministic under the recorded NumPy seed. "
                "Cross-runtime classifier conclusions use hop 16 onward after live history replaces it."
            ),
        },
        "environment": environment,
        "pcmContract": {
            "sampleRate": SAMPLE_RATE,
            "channels": 1,
            "format": "PCM16_LE_SIGNED",
            "inferenceSamples": INFERENCE_SAMPLES,
        },
        "captures": captures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "captureCount": len(captures),
                "inferenceHopsPerCapture": [
                    capture["pcmInferenceHops"] for capture in captures
                ],
                "postWarmupScores": [
                    {
                        "waveformId": capture["waveformId"],
                        "python": capture["python"][
                            "maximumPostWarmupEffectiveScore"
                        ],
                        "android": capture["android"][
                            "maximumPostWarmupEffectiveScore"
                        ],
                    }
                    for capture in captures
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
