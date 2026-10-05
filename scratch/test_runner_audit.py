import subprocess
import sys
import json
from pathlib import Path

tests_dir = Path("backend/tests")
test_files = sorted([f.name for f in tests_dir.glob("test_*.py")])

results = {}

print(f"Total test files to audit: {len(test_files)}")

for f in test_files:
    cmd = [
        sys.executable,
        "-m", "pytest",
        f"backend/tests/{f}",
        "-m", "not integration",
        "--basetemp=scratch/pytest_tmp",
        "-o", "cache_dir=scratch/pytest_cache",
        "-q", "--tb=line"
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(Path.cwd())
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        last_line = stdout.splitlines()[-1] if stdout else ""
        results[f] = {
            "status": "PASS" if proc.returncode == 0 else "FAIL",
            "summary": last_line,
            "error": stderr if proc.returncode != 0 and not stdout else ""
        }
        print(f"[{results[f]['status']}] {f}: {last_line}")
    except subprocess.TimeoutExpired:
        results[f] = {
            "status": "TIMEOUT",
            "summary": "Timed out after 15s",
            "error": ""
        }
        print(f"[TIMEOUT] {f}")
    except Exception as e:
        results[f] = {
            "status": "ERROR",
            "summary": str(e),
            "error": ""
        }
        print(f"[ERROR] {f}: {e}")

with open("scratch/backend_test_audit_results.json", "w") as out:
    json.dump(results, out, indent=2)

print("Done audit run.")
