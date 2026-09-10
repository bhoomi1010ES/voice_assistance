# Approved openWakeWord v0.5.1 ONNX assets

This directory contains the exact, unmodified official openWakeWord `v0.5.1`
ONNX chain approved for the Phase 0.9 `hey_mycroft` experiment:

- `melspectrogram.onnx`
- `embedding_model.onnx`
- `hey_mycroft_v0.1.onnx`

The previous `hey_jarvis_v0.1.onnx` artifact is retained as an unmodified,
inactive reference asset. It is not loaded by the active Phase 0.9 contract.

The immutable sizes, SHA-256 digests, tensor contracts, official release URLs,
streaming contract, runtime version, and license are recorded in:

`docs/openwakeword_model_manifest.md`

The Android runtime refuses to initialize if any required asset is absent or
does not match its approved size/hash. Do not rename, convert, quantize, or
replace these artifacts without a new approval manifest.
