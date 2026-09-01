import subprocess
import sys
import os

# Set unbuffered environment variable
os.environ["PYTHONUNBUFFERED"] = "1"

print("Starting backend subprocess...")
with open("uvicorn.log", "w", encoding="utf-8", buffering=1) as f:
    p = subprocess.Popen(
        [
            r"..\.venv\Scripts\python.exe",
            "-u",
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            ".",
            "--port",
            "8000",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Forward all output to the log file and stdout
    for line in p.stdout:
        f.write(line)
        f.flush()
        sys.stdout.write(line)
        sys.stdout.flush()
