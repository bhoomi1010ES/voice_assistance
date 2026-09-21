# WebRTC AudioProcessing / AEC3 integration

Phase 6 pins the upstream source contract to WebRTC commit
`a3bca8f0b821abfd524f4c0d50a2ba841b7b59f3` (the Android API tree and
`api/audio/audio_processing.h` at that revision). The upstream source is not
vendored in this checkout because a WebRTC Android source checkout is roughly
16 GB and requires the upstream GN/Android toolchain. The official Android
build guidance is to fetch `webrtc_android` and build with GN/Ninja.

The selected source is WebRTC AudioProcessing/AEC3, not a subtraction filter.
The upstream source is BSD-3-Clause licensed and carries the separate WebRTC
PATENTS grant. When the source or a static library is brought into this
repository, its `LICENSE`, `PATENTS`, `AUTHORS`, and generated dependency
notices must be copied into the release notice bundle and reviewed before the
backend is enabled.

## Adapter contract

Build a small CMake subproject that exports a target named
`voice_aec3_backend` and implements the C ABI declared in
`frontend/android/app/src/main/cpp/voice_aec3_backend_api.h`. The adapter must:

1. create `webrtc::AudioProcessing` with AEC3 enabled and optional WebRTC NS;
2. call `ProcessReverseStream` for the 10 ms render frame before
   `ProcessStream` for the matching capture frame;
3. set the calibrated stream delay before capture processing;
4. return only cleaned PCM and metadata-free health codes across the C ABI; and
5. destroy/reset the APM on session teardown.

Supply the adapter directory during the Android build:

```text
gradlew.bat :app:assembleDebug \
  -PvoiceAec3BackendDir=C:/path/to/voice-aec3-adapter
```

Without that explicit input, the JNI shim still builds but reports
`WEBRTC_AEC3_UNAVAILABLE`; `AUTO` therefore keeps platform AEC and explicit
`WEBRTC_AEC3` remains a documented degraded/bypass diagnostic mode.

## Notice

Selected upstream: WebRTC source commit
`a3bca8f0b821abfd524f4c0d50a2ba841b7b59f3`.

The WebRTC project is distributed under its BSD-style license with a separate
PATENTS file. This repository does not currently redistribute the WebRTC
source, static library, or generated third-party notices. Before enabling the
external adapter, copy the exact upstream `LICENSE`, `PATENTS`, `AUTHORS`, and
dependency notices for the pinned checkout into the release artifact and
complete license review.
