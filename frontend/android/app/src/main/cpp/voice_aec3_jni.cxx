#include <jni.h>

#include <cstdint>
#include <cstring>

#include "voice_aec3_backend_api.h"

namespace {

struct Handle {
    VoiceAec3Backend* backend = nullptr;
};

template <typename T>
T* criticalArray(JNIEnv* env, jshortArray array, jint offset) {
    if (array == nullptr) return nullptr;
    auto* base = static_cast<T*>(env->GetPrimitiveArrayCritical(array, nullptr));
    return base == nullptr ? nullptr : base + offset;
}

#if defined(VOICE_AEC3_EXTERNAL)
void releaseCritical(JNIEnv* env, jshortArray array, void* pointer, jint mode) {
    if (array != nullptr && pointer != nullptr) {
        env->ReleasePrimitiveArrayCritical(array, pointer, mode);
    }
}
#endif

}  // namespace

extern "C" JNIEXPORT jboolean JNICALL
Java_com_voiceaipoc_audio_WebRtcAec3EchoCanceller_nativeBackendAvailableStatic(
    JNIEnv*, jclass) {
#if defined(VOICE_AEC3_EXTERNAL)
    return JNI_TRUE;
#else
    return JNI_FALSE;
#endif
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_voiceaipoc_audio_WebRtcAec3EchoCanceller_nativeBackendAvailable(
    JNIEnv*, jobject) {
#if defined(VOICE_AEC3_EXTERNAL)
    return JNI_TRUE;
#else
    return JNI_FALSE;
#endif
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_voiceaipoc_audio_WebRtcAec3EchoCanceller_nativeBackendName(
    JNIEnv* env, jobject) {
#if defined(VOICE_AEC3_EXTERNAL)
    return env->NewStringUTF(voice_aec3_backend_name());
#else
    return env->NewStringUTF("WEBRTC_AEC3_UNAVAILABLE");
#endif
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_voiceaipoc_audio_WebRtcAec3EchoCanceller_nativeCreate(
    JNIEnv*, jobject, jint sample_rate_hz, jint channel_count,
    jint frame_size_samples, jint stream_delay_ms, jboolean enable_aec,
    jboolean enable_noise_suppression) {
#if defined(VOICE_AEC3_EXTERNAL)
    VoiceAec3BackendConfig config{
        sample_rate_hz,
        channel_count,
        frame_size_samples,
        stream_delay_ms,
        enable_aec == JNI_TRUE,
        enable_noise_suppression == JNI_TRUE,
    };
    auto* handle = new Handle();
    handle->backend = voice_aec3_backend_create(&config);
    if (handle->backend == nullptr) {
        delete handle;
        return 0;
    }
    return reinterpret_cast<jlong>(handle);
#else
    (void)sample_rate_hz;
    (void)channel_count;
    (void)frame_size_samples;
    (void)stream_delay_ms;
    (void)enable_aec;
    (void)enable_noise_suppression;
    return 0;
#endif
}

extern "C" JNIEXPORT jint JNICALL
Java_com_voiceaipoc_audio_WebRtcAec3EchoCanceller_nativeProcessRender(
    JNIEnv* env, jobject, jlong raw_handle, jshortArray pcm, jint offset,
    jint count, jlong presentation_timestamp_ns, jboolean reference_ready) {
#if defined(VOICE_AEC3_EXTERNAL)
    auto* handle = reinterpret_cast<Handle*>(raw_handle);
    if (handle == nullptr || handle->backend == nullptr || pcm == nullptr) return -1;
    auto* input = criticalArray<int16_t>(env, pcm, offset);
    if (input == nullptr) return -1;
    const int result = voice_aec3_backend_process_render(
        handle->backend, input, count, presentation_timestamp_ns,
        reference_ready == JNI_TRUE);
    releaseCritical(env, pcm, input - offset, JNI_ABORT);
    return result;
#else
    (void)raw_handle;
    (void)env;
    (void)pcm;
    (void)offset;
    (void)count;
    (void)presentation_timestamp_ns;
    (void)reference_ready;
    return -1;
#endif
}

extern "C" JNIEXPORT jint JNICALL
Java_com_voiceaipoc_audio_WebRtcAec3EchoCanceller_nativeProcessCapture(
    JNIEnv* env, jobject, jlong raw_handle, jshortArray input, jint input_offset,
    jshortArray output, jint output_offset, jint count, jlong capture_timestamp_ns) {
#if defined(VOICE_AEC3_EXTERNAL)
    auto* handle = reinterpret_cast<Handle*>(raw_handle);
    if (handle == nullptr || handle->backend == nullptr || input == nullptr || output == nullptr) {
        return -1;
    }
    auto* input_base = static_cast<int16_t*>(env->GetPrimitiveArrayCritical(input, nullptr));
    auto* output_base = static_cast<int16_t*>(env->GetPrimitiveArrayCritical(output, nullptr));
    if (input_base == nullptr || output_base == nullptr) {
        if (input_base != nullptr) env->ReleasePrimitiveArrayCritical(input, input_base, JNI_ABORT);
        if (output_base != nullptr) env->ReleasePrimitiveArrayCritical(output, output_base, 0);
        return -1;
    }
    const int result = voice_aec3_backend_process_capture(
        handle->backend, input_base + input_offset, output_base + output_offset,
        count, capture_timestamp_ns);
    env->ReleasePrimitiveArrayCritical(input, input_base, JNI_ABORT);
    env->ReleasePrimitiveArrayCritical(output, output_base, 0);
    return result;
#else
    (void)raw_handle;
    (void)env;
    (void)input;
    (void)input_offset;
    (void)output;
    (void)output_offset;
    (void)count;
    (void)capture_timestamp_ns;
    return -1;
#endif
}

extern "C" JNIEXPORT void JNICALL
Java_com_voiceaipoc_audio_WebRtcAec3EchoCanceller_nativeReset(
    JNIEnv*, jobject, jlong raw_handle) {
#if defined(VOICE_AEC3_EXTERNAL)
    auto* handle = reinterpret_cast<Handle*>(raw_handle);
    if (handle != nullptr && handle->backend != nullptr) voice_aec3_backend_reset(handle->backend);
#else
    (void)raw_handle;
#endif
}

extern "C" JNIEXPORT void JNICALL
Java_com_voiceaipoc_audio_WebRtcAec3EchoCanceller_nativeDestroy(
    JNIEnv*, jobject, jlong raw_handle) {
    auto* handle = reinterpret_cast<Handle*>(raw_handle);
#if defined(VOICE_AEC3_EXTERNAL)
    if (handle != nullptr) {
        if (handle->backend != nullptr) voice_aec3_backend_destroy(handle->backend);
        delete handle;
    }
#else
    delete handle;
#endif
}
