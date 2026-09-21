cmake_minimum_required(VERSION 3.22.1)

# Keep React Native's default New Architecture setup in the build. It provides
# OnLoad.cpp, which installs the TurboModule and Fabric providers used by the
# bridgeless runtime. Replacing that setup causes core modules such as
# PlatformConstants and SourceCode to disappear at startup.
project(appmodules)
include(${REACT_ANDROID_DIR}/cmake-utils/ReactNative-application.cmake)

# RN 0.87 enables -Werror for generated C++ targets. The generated
# safe-area-context bindings contain intentional unused parameters, so keep
# the warning but do not make it fail the application build.
target_compile_options(appmodules PRIVATE -Wno-error=unused-parameter)
if(TARGET common_flags)
    target_compile_options(common_flags INTERFACE -Wno-error=unused-parameter)
endif()

# The AEC JNI target remains separate from the React Native appmodules target.
add_library(voice_aec3_jni SHARED voice_aec3_jni.cxx)
target_compile_features(voice_aec3_jni PRIVATE cxx_std_17)
target_include_directories(voice_aec3_jni PRIVATE "${CMAKE_CURRENT_LIST_DIR}")

set(VOICE_AEC3_BACKEND_DIR "" CACHE PATH
    "Optional directory containing the pinned WebRTC AEC3 adapter and CMake target")

if(VOICE_AEC3_BACKEND_DIR AND EXISTS "${VOICE_AEC3_BACKEND_DIR}/CMakeLists.txt")
    add_subdirectory(
        "${VOICE_AEC3_BACKEND_DIR}"
        "${CMAKE_BINARY_DIR}/voice_aec3_backend"
    )
    if(NOT TARGET voice_aec3_backend)
        message(FATAL_ERROR
            "VOICE_AEC3_BACKEND_DIR must export a target named voice_aec3_backend")
    endif()
    target_compile_definitions(voice_aec3_jni PRIVATE VOICE_AEC3_EXTERNAL=1)
    target_link_libraries(voice_aec3_jni PRIVATE voice_aec3_backend)
else()
    message(STATUS
        "WebRTC AEC3 backend not configured; JNI library will report unavailable")
endif()

find_library(log-lib log)
target_link_libraries(voice_aec3_jni PRIVATE ${log-lib})
