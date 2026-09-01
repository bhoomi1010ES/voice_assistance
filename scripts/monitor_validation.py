import os
import sys
import time
import datetime
import json
import subprocess
import re
import threading
from pathlib import Path

# Paths
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_DIR = WORKSPACE_ROOT / "scratch"
SCRATCH_DIR.mkdir(exist_ok=True)

STATUS_FILE = SCRATCH_DIR / "validation_status.txt"
STATE_FILE = SCRATCH_DIR / "validation_state.json"
UVICORN_LOG = WORKSPACE_ROOT / "backend" / "uvicorn.log"

class ValidationMonitor:
    def __init__(self):
        self.timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.validation_dir = WORKSPACE_ROOT / "docs" / "phase4_physical_validation" / self.timestamp
        self.validation_dir.mkdir(parents=True, exist_ok=True)
        
        self.android_log_file = open(self.validation_dir / "android.log", "w", encoding="utf-8")
        self.backend_log_file = open(self.validation_dir / "backend.log", "w", encoding="utf-8")
        self.monitor_log_file = open(self.validation_dir / "validation.log", "w", encoding="utf-8")
        
        self.current_turn = 1
        self.turns_data = []  # list of dicts for each turn
        
        # State tracking
        self.active_turn_id = None
        self.active_response_id = None
        self.active_session_id = None
        
        self.current_turn_data = self._create_empty_turn()
        self.running = True
        self.lock = threading.Lock()
        
        # Calculate device-to-host offset
        try:
            device_time = int(subprocess.check_output(["adb", "shell", "date", "+%s%3N"]).decode("utf-8").strip())
            host_time = int(time.time() * 1000)
            self.device_offset_ms = host_time - device_time
        except Exception:
            self.device_offset_ms = 0
            
        self.log(f"Monitor initialized. Clock offset: device is {self.device_offset_ms} ms behind host")

    def log(self, message):
        t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        msg = f"[{t}] {message}\n"
        sys.stdout.write(msg)
        sys.stdout.flush()
        self.monitor_log_file.write(msg)
        self.monitor_log_file.flush()


    def _create_empty_turn(self):
        return {
            "turn_id": "NOT_AVAILABLE",
            "response_id": "NOT_AVAILABLE",
            "audio_start_time": "NOT_AVAILABLE",
            "speech_start_time": "NOT_AVAILABLE",
            "vad_end_time": "NOT_AVAILABLE",
            "client_commit_time": "NOT_AVAILABLE",
            "backend_commit_received_time": "NOT_AVAILABLE",
            "stt_start_time": "NOT_AVAILABLE",
            "first_partial_time": "NOT_AVAILABLE",
            "last_partial_time": "NOT_AVAILABLE",
            "final_inference_start_time": "NOT_AVAILABLE",
            "final_transcript_time": "NOT_AVAILABLE",
            "android_final_received_time": "NOT_AVAILABLE",
            "partials": [],
            "final_transcript": "NOT_AVAILABLE",
            "status": "PENDING",  # PENDING, SUCCESS, FAILED
            "failure_reason": None,
            "stt_duration_ms": "NOT_AVAILABLE",
            "audio_duration_ms": "NOT_AVAILABLE"
        }

    def save_state(self):
        with self.lock:
            state = {
                "timestamp": self.timestamp,
                "current_turn": self.current_turn,
                "turns_data": self.turns_data,
                "active_turn_id": self.active_turn_id,
                "active_response_id": self.active_response_id
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            
            status_msg = f"READY — SPEAK TURN {self.current_turn}"
            if self.current_turn > 10:
                status_msg = "10 TURNS COMPLETED"
            
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                f.write(status_msg)

    def write_status(self, msg):
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            f.write(msg)

    def parse_logcat_line(self, line):
        self.android_log_file.write(line + "\n")
        self.android_log_file.flush()

        # Parse tags: VoiceAI-Bridge, VoiceAI-VoiceGateway, VoiceAI-Audio
        if "VoiceAI-Bridge" in line:
            # VAD speech started
            m = re.search(r"VAD speech started.*wallMs=(\d+)", line)
            if m:
                wall_ms = int(m.group(1)) + self.device_offset_ms
                with self.lock:
                    if self.current_turn_data["speech_start_time"] == "NOT_AVAILABLE":
                        self.current_turn_data["speech_start_time"] = wall_ms
                        self.log(f"Turn {self.current_turn} VAD speech started (Energy): {wall_ms}")
            # SILERO VAD speech started
            m = re.search(r"SILERO VAD speech started.*wallMs=(\d+)", line)
            if m:
                wall_ms = int(m.group(1)) + self.device_offset_ms
                with self.lock:
                    self.current_turn_data["speech_start_time"] = wall_ms
                    self.log(f"Turn {self.current_turn} VAD speech started (Silero): {wall_ms}")
            # VAD speech stopped
            m = re.search(r"VAD speech stopped.*wallMs=(\d+)", line)
            if m:
                wall_ms = int(m.group(1)) + self.device_offset_ms
                with self.lock:
                    if self.current_turn_data["vad_end_time"] == "NOT_AVAILABLE":
                        self.current_turn_data["vad_end_time"] = wall_ms
                        self.log(f"Turn {self.current_turn} VAD speech stopped (Energy): {wall_ms}")
            # SILERO VAD speech stopped
            m = re.search(r"SILERO VAD speech stopped.*wallMs=(\d+)", line)
            if m:
                wall_ms = int(m.group(1)) + self.device_offset_ms
                with self.lock:
                    self.current_turn_data["vad_end_time"] = wall_ms
                    self.log(f"Turn {self.current_turn} VAD speech stopped (Silero): {wall_ms}")

        elif "VoiceAI-VoiceGateway" in line:
            # Client control sent
            m = re.search(r"VOICE control sent type=(\S+).*wallMs=(\d+)", line)
            if m:
                ctl_type = m.group(1)
                wall_ms = int(m.group(2)) + self.device_offset_ms
                if ctl_type == "client.audio.commit":
                    with self.lock:
                        self.current_turn_data["client_commit_time"] = wall_ms
                        self.log(f"Turn {self.current_turn} Client audio commit sent: {wall_ms}")
                elif ctl_type == "client.turn.start":
                    self.log(f"Turn {self.current_turn} Client start turn sent: {wall_ms}")

            # Server event received on Android
            # Format: VOICE server event type=([a-zA-Z\.\_]+) sessionId=(\S+) turnId=(\S+) responseId=(\S+) payload=(.*?) wallMs=(\d+)
            m = re.search(r"VOICE server event type=(\S+)\s+sessionId=(\S+)\s+turnId=(\S+)\s+responseId=(\S+)\s+payload=(.*?)\s+wallMs=(\d+)", line)
            if not m:
                # Fallback matching
                m = re.search(r"VOICE server event type=(\S+).*payload=(.*?) wallMs=(\d+)", line)
                if m:
                    evt_type = m.group(1)
                    payload = m.group(2)
                    wall_ms = int(m.group(3)) + self.device_offset_ms
                    # extract IDs if present in payload
                    turn_id_match = re.search(r'"turn_id":\s*"([^"]+)"', payload)
                    response_id_match = re.search(r'"response_id":\s*"([^"]+)"', payload)
                else:
                    evt_type = None
            else:
                evt_type = m.group(1)
                turn_id = m.group(3)
                response_id = m.group(4)
                wall_ms = int(m.group(6)) + self.device_offset_ms
                
            if evt_type:
                if evt_type == "transcript.final" or evt_type == "server.turn.completed":
                    with self.lock:
                        self.current_turn_data["android_final_received_time"] = wall_ms
                        self.log(f"Turn {self.current_turn} Android final received: {wall_ms}")
                        self.check_turn_completion()


        elif "VoiceAI-Audio" in line:
            if "Microphone capture started" in line:
                self.log(f"Microphone started: {line.strip()}")

    def parse_backend_line(self, line):
        self.backend_log_file.write(line + "\n")
        self.backend_log_file.flush()
        
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return
            
        event = data.get("event")
        if not event:
            return
            
        if event == "voice.turn.started":
            turn_id = data.get("turn_id")
            response_id = data.get("response_id")
            session_id = data.get("session_id")
            ts = data.get("timestamp_ms") or int(time.time() * 1000)
            with self.lock:
                self.active_turn_id = turn_id
                self.active_response_id = response_id
                self.active_session_id = session_id
                self.current_turn_data["turn_id"] = turn_id
                self.current_turn_data["response_id"] = response_id
                self.log(f"Turn {self.current_turn} started on backend. Turn ID: {turn_id}, Response ID: {response_id}")
                
        elif event == "stt.audio.started":
            ts = data.get("audio_start_timestamp_ms")
            with self.lock:
                self.current_turn_data["audio_start_time"] = ts
                self.log(f"Turn {self.current_turn} STT audio started: {ts}")
                
        elif event == "voice.audio.commit.received":
            ts = data.get("backend_commit_received_timestamp_ms")
            with self.lock:
                self.current_turn_data["backend_commit_received_time"] = ts
                self.log(f"Turn {self.current_turn} backend commit received: {ts}")
                
        elif event == "stt.inference.started":
            kind = data.get("inference_kind")
            ts = data.get("inference_start_timestamp_ms")
            if kind == "final":
                with self.lock:
                    self.current_turn_data["stt_start_time"] = ts
                    self.current_turn_data["final_inference_start_time"] = ts
                    self.log(f"Turn {self.current_turn} STT final inference started: {ts}")
                    
        elif event == "stt.partial.emitted":
            ts = data.get("timestamp_ms")
            text = data.get("text")
            with self.lock:
                if self.current_turn_data["first_partial_time"] == "NOT_AVAILABLE":
                    self.current_turn_data["first_partial_time"] = ts
                    self.log(f"Turn {self.current_turn} First partial emitted: '{text}' at {ts}")
                self.current_turn_data["last_partial_time"] = ts
                self.current_turn_data["partials"].append({"timestamp": ts, "text": text})
                
        elif event == "stt.final.completed":
            ts = data.get("final_transcript_timestamp_ms") or data.get("completed_timestamp_ms")
            text = data.get("text")
            metrics = data.get("metrics") or {}
            with self.lock:
                self.current_turn_data["final_transcript_time"] = ts
                self.current_turn_data["final_transcript"] = text
                self.current_turn_data["audio_duration_ms"] = metrics.get("audio_duration_ms")
                self.current_turn_data["stt_duration_ms"] = metrics.get("inference_duration_ms")
                self.log(f"Turn {self.current_turn} STT final transcript: '{text}' at {ts}")
                self.check_turn_completion()
                
        elif event == "voice.response.cancel.received":
            self.log(f"Cancellation received on backend: {data}")
            with self.lock:
                self.current_turn_data["status"] = "FAILED"
                self.current_turn_data["failure_reason"] = "cancelled"
                self.check_turn_completion()

        elif event in ["stt.inference.failed", "stt.inference.cancelled"]:
            self.log(f"STT inference failed/cancelled event: {event}")
            with self.lock:
                self.current_turn_data["status"] = "FAILED"
                self.current_turn_data["failure_reason"] = event
                self.check_turn_completion()

    def check_turn_completion(self):
        # We need final transcript and android final received to complete the turn
        d = self.current_turn_data
        if d["final_transcript"] != "NOT_AVAILABLE" and d["android_final_received_time"] != "NOT_AVAILABLE":
            if d["status"] == "PENDING":
                d["status"] = "SUCCESS"
            self.finalize_turn()
        elif d["status"] == "FAILED":
            self.finalize_turn()

    def finalize_turn(self):
        d = self.current_turn_data
        self.log(f"Finalizing Turn {self.current_turn} (Status: {d['status']})")
        
        # Calculate latencies
        try:
            if d["final_transcript_time"] != "NOT_AVAILABLE" and d["vad_end_time"] != "NOT_AVAILABLE":
                d["latency_speech_end_to_final"] = d["final_transcript_time"] - d["vad_end_time"]
            else:
                d["latency_speech_end_to_final"] = "NOT_AVAILABLE"
                
            if d["final_transcript_time"] != "NOT_AVAILABLE" and d["client_commit_time"] != "NOT_AVAILABLE":
                d["latency_commit_to_final"] = d["final_transcript_time"] - d["client_commit_time"]
            else:
                d["latency_commit_to_final"] = "NOT_AVAILABLE"
                
            if d["first_partial_time"] != "NOT_AVAILABLE" and d["speech_start_time"] != "NOT_AVAILABLE":
                d["latency_first_partial"] = d["first_partial_time"] - d["speech_start_time"]
            else:
                d["latency_first_partial"] = "NOT_AVAILABLE"
                
            if d["android_final_received_time"] != "NOT_AVAILABLE" and d["final_transcript_time"] != "NOT_AVAILABLE":
                d["latency_final_delivery"] = d["android_final_received_time"] - d["final_transcript_time"]
            else:
                d["latency_final_delivery"] = "NOT_AVAILABLE"
                
            if d["android_final_received_time"] != "NOT_AVAILABLE" and d["speech_start_time"] != "NOT_AVAILABLE":
                d["latency_total_turn"] = d["android_final_received_time"] - d["speech_start_time"]
            else:
                d["latency_total_turn"] = "NOT_AVAILABLE"
        except Exception as e:
            self.log(f"Error calculating latencies: {e}")
            d["latency_speech_end_to_final"] = "ERROR"
            d["latency_commit_to_final"] = "ERROR"
            d["latency_first_partial"] = "ERROR"
            d["latency_final_delivery"] = "ERROR"
            d["latency_total_turn"] = "ERROR"

        # Record partial sequence proof
        # Check if first partial occurred before final transcript
        if d["first_partial_time"] != "NOT_AVAILABLE" and d["final_transcript_time"] != "NOT_AVAILABLE":
            d["partial_before_final_proof"] = d["first_partial_time"] < d["final_transcript_time"]
        else:
            d["partial_before_final_proof"] = "NO_PARTIALS_OBSERVED"

        # Record VAD_END -> Finalization order
        if d["speech_start_time"] != "NOT_AVAILABLE" and d["vad_end_time"] != "NOT_AVAILABLE" and d["final_inference_start_time"] != "NOT_AVAILABLE":
            d["finalization_after_vad_end"] = d["final_inference_start_time"] >= d["vad_end_time"]
        else:
            d["finalization_after_vad_end"] = "NOT_AVAILABLE"

        # Save turn
        self.turns_data.append(d)
        
        # Advance count if successful
        if d["status"] == "SUCCESS":
            self.log(f"Turn {self.current_turn} SUCCESSFUL! Transcript: '{d['final_transcript']}'")
            self.log(f"Latencies: speech_end_to_final={d['latency_speech_end_to_final']}ms, commit_to_final={d['latency_commit_to_final']}ms")
            
            # Write immediate status
            recorded_msg = f"TURN {self.current_turn} RECORDED — READY FOR TURN {self.current_turn+1}"
            self.write_status(recorded_msg)
            self.current_turn += 1
        else:
            self.log(f"Turn {self.current_turn} FAILED/INVALID! Reason: {d['failure_reason']}")
            # Keep failed-turn evidence in the report but rerun this turn index!
            recorded_msg = f"TURN FAILED (REASON: {d['failure_reason']}) — PLEASE RERUN TURN {self.current_turn}"
            self.write_status(recorded_msg)

        # Clear active turn info and setup empty structure
        self.active_turn_id = None
        self.active_response_id = None
        self.current_turn_data = self._create_empty_turn()
        
        self.save_state()

    def run_logcat_monitor(self):
        self.log("Starting adb logcat monitor...")
        # Clear logcat first
        subprocess.run(["adb", "logcat", "-c"], check=True)
        
        # Run logcat
        cmd = ["adb", "logcat", "-v", "time", "VoiceAI-Bridge:I", "VoiceAI-VoiceGateway:I", "VoiceAI-Audio:I", "*:S"]
        self.logcat_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        
        for line in self.logcat_proc.stdout:
            if not self.running:
                break
            line_str = line.strip()
            if line_str:
                self.parse_logcat_line(line_str)
        self.log("Logcat monitor exited.")

    def run_backend_monitor(self):
        self.log(f"Starting backend log monitor on {UVICORN_LOG}...")
        # Tail uvicorn log
        # Ensure log exists
        if not UVICORN_LOG.exists():
            UVICORN_LOG.touch()
            
        with open(UVICORN_LOG, "r", encoding="utf-8", errors="replace") as f:
            # Seek to end
            f.seek(0, 2)
            while self.running:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                line_str = line.strip()
                if line_str:
                    self.parse_backend_line(line_str)
        self.log("Backend log monitor exited.")

    def start(self):
        # Initial status file
        self.save_state()
        
        # Start threads
        self.logcat_thread = threading.Thread(target=self.run_logcat_monitor, daemon=True)
        self.backend_thread = threading.Thread(target=self.run_backend_monitor, daemon=True)
        
        self.logcat_thread.start()
        self.backend_thread.start()

    def stop(self):
        self.running = False
        if hasattr(self, "logcat_proc"):
            self.logcat_proc.terminate()
        
        self.android_log_file.close()
        self.backend_log_file.close()
        self.monitor_log_file.close()
        self.log("Monitor stopped.")

if __name__ == "__main__":
    monitor = ValidationMonitor()
    monitor.start()
    
    print(f"Validation folder created: {monitor.validation_dir}")
    print("Monitor started. Write 'stop' to end it, or monitor status in real-time.")
    
    try:
        while monitor.current_turn <= 10:
            time.sleep(1)
        print("10 successful turns completed! Generating report...")
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
