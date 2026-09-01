import concurrent.futures
import math
import os
import sys
import time
from pathlib import Path
from faster_whisper import WhisperModel
import numpy as np

# Ensure workspace root is in sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = str(WORKSPACE_ROOT / "models" / "whisper-large-v3-turbo-ct2")

def generate_test_audio(duration_seconds: float = 8.0, sample_rate: int = 16000) -> np.ndarray:
    """Generate realistic speech-like audio signal with harmonics and modulation."""
    num_samples = int(duration_seconds * sample_rate)
    t = np.linspace(0, duration_seconds, num_samples, dtype=np.float32)
    
    # 220Hz fundamental with harmonics and syllabic envelope modulation (~4 Hz)
    envelope = (np.sin(2 * np.pi * 4 * t) + 1.0) * 0.5
    f0 = 220.0
    signal = (
        0.5 * np.sin(2 * np.pi * f0 * t) +
        0.3 * np.sin(2 * np.pi * 2 * f0 * t) +
        0.15 * np.sin(2 * np.pi * 3 * f0 * t) +
        0.05 * np.random.normal(0, 0.1, num_samples)
    ) * envelope
    return signal.astype(np.float32)

def benchmark_single_inference(model: WhisperModel, audio: np.ndarray, beam_size: int = 1) -> tuple[float, str, float]:
    wall_start = time.monotonic()
    cpu_start = time.process_time()
    segments, info = model.transcribe(
        audio,
        beam_size=beam_size,
        language="en",
        condition_on_previous_text=False,
        vad_filter=False,
    )
    text = " ".join([seg.text for seg in segments]).strip()
    wall_end = time.monotonic()
    cpu_end = time.process_time()
    
    duration_ms = (wall_end - wall_start) * 1000.0
    cpu_time_ms = (cpu_end - cpu_start) * 1000.0
    return duration_ms, text, cpu_time_ms

def run_benchmarks():
    print("=" * 70)
    print("PHASE 4: CTRANSLATE2 / WHISPER LARGE-V3-TURBO CPU BENCHMARK")
    print(f"Host CPUs: {os.cpu_count()} logical cores")
    print(f"Model: {MODEL_PATH}")
    print("=" * 70)

    audio_8s = generate_test_audio(8.0)
    audio_2s = generate_test_audio(2.0)

    # -------------------------------------------------------------
    # 1. CPU Threads Scaling (Large-v3-Turbo on CPU, int8, beam_size=1)
    # -------------------------------------------------------------
    print("\n--- 1. CPU THREADS SCALING (8.0s Audio, beam_size=1, int8) ---")
    thread_configs = [1, 2, 4, 8]
    scaling_results = {}

    for threads in thread_configs:
        print(f"Loading model with cpu_threads={threads}...")
        m = WhisperModel(MODEL_PATH, device="cpu", compute_type="int8", cpu_threads=threads)
        
        # Warmup
        m.transcribe(audio_2s, beam_size=1, language="en")
        
        # Benchmark 8.0s
        dur_ms, text, cpu = benchmark_single_inference(m, audio_8s, beam_size=1)
        rtf = (dur_ms / 1000.0) / 8.0
        scaling_results[threads] = (dur_ms, rtf, cpu)
        print(f"  cpu_threads={threads:2d} -> Duration: {dur_ms:8.2f} ms | RTF: {rtf:5.2f}x | Process CPU: {cpu:5.1f}%")
        del m

    # -------------------------------------------------------------
    # 2. CPU Contention: Case A vs Case B vs Case C
    # -------------------------------------------------------------
    print("\n--- 2. CPU CONTENTION BENCHMARK (Partial vs Final) ---")

    # Case A: Final Alone (4 threads)
    model_4t = WhisperModel(MODEL_PATH, device="cpu", compute_type="int8", cpu_threads=4)
    model_4t.transcribe(audio_2s, beam_size=1, language="en") # warmup
    dur_a, _, _ = benchmark_single_inference(model_4t, audio_8s, beam_size=1)
    rtf_a = (dur_a / 1000.0) / 8.0
    print(f"CASE A (Final Alone, 4 threads):")
    print(f"  Final Inference Duration: {dur_a:8.2f} ms | RTF: {rtf_a:5.2f}x")

    # Case B: Concurrent Partial (4 threads) + Final (4 threads)
    print(f"\nCASE B (Concurrent Partial [4t] + Final [4t]):")
    def run_partial_4t():
        p_start = time.monotonic()
        segments, _ = model_4t.transcribe(audio_8s, beam_size=1, language="en") # simulate heavy partial
        list(segments)
        p_end = time.monotonic()
        return (p_end - p_start) * 1000.0

    def run_final_4t():
        time.sleep(0.5) # Start 500ms into partial execution
        f_start = time.monotonic()
        dur, _, _ = benchmark_single_inference(model_4t, audio_8s, beam_size=1)
        f_end = time.monotonic()
        return dur, f_start, f_end

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_partial = executor.submit(run_partial_4t)
        f_final = executor.submit(run_final_4t)
        p_dur = f_partial.result()
        f_dur, f_st, f_et = f_final.result()

    rtf_b = (f_dur / 1000.0) / 8.0
    slowdown_b = (f_dur / dur_a - 1.0) * 100.0
    print(f"  Partial Duration: {p_dur:8.2f} ms")
    print(f"  Final Duration:   {f_dur:8.2f} ms | RTF: {rtf_b:5.2f}x | Slowdown vs Alone: +{slowdown_b:5.1f}%")

    # Case C: Partial with 1 thread model vs Final with 4 thread model
    print(f"\nCASE C (Partial [1t] + Final [4t]):")
    model_1t = WhisperModel(MODEL_PATH, device="cpu", compute_type="int8", cpu_threads=1)
    
    def run_partial_1t():
        p_start = time.monotonic()
        segments, _ = model_1t.transcribe(audio_8s, beam_size=1, language="en")
        list(segments)
        p_end = time.monotonic()
        return (p_end - p_start) * 1000.0

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_partial_c = executor.submit(run_partial_1t)
        f_final_c = executor.submit(run_final_4t)
        p_dur_c = f_partial_c.result()
        f_dur_c, _, _ = f_final_c.result()

    rtf_c = (f_dur_c / 1000.0) / 8.0
    slowdown_c = (f_dur_c / dur_a - 1.0) * 100.0
    print(f"  Partial (1t) Duration: {p_dur_c:8.2f} ms")
    print(f"  Final (4t) Duration:   {f_dur_c:8.2f} ms | RTF: {rtf_c:5.2f}x | Slowdown vs Alone: +{slowdown_c:5.1f}%")

if __name__ == "__main__":
    run_benchmarks()
