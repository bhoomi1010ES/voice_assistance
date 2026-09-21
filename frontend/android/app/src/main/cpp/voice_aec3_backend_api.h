#pragma once

#include <cstdint>

/**
 * Stable C ABI between the JNI shim and the pinned WebRTC adapter.
 *
 * The adapter implementation owns webrtc::AudioProcessing and must feed
 * ProcessReverseStream before ProcessStream for every 10 ms frame. It must
 * not expose WebRTC C++ types across this boundary.
 */
struct VoiceAec3BackendConfig {
    int sample_rate_hz;
    int channel_count;
    int frame_size_samples;
    int stream_delay_ms;
    bool enable_aec;
    bool enable_noise_suppression;
};

struct VoiceAec3Backend;

extern "C" {

VoiceAec3Backend* voice_aec3_backend_create(const VoiceAec3BackendConfig* config);
int voice_aec3_backend_process_render(
    VoiceAec3Backend* backend,
    const int16_t* pcm,
    int sample_count,
    int64_t presentation_timestamp_ns,
    bool reference_ready);
int voice_aec3_backend_process_capture(
    VoiceAec3Backend* backend,
    const int16_t* input,
    int16_t* output,
    int sample_count,
    int64_t capture_timestamp_ns);
void voice_aec3_backend_reset(VoiceAec3Backend* backend);
void voice_aec3_backend_destroy(VoiceAec3Backend* backend);
const char* voice_aec3_backend_name();

}
