import os
import re
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Parse counts even if emojis are missing
RE_SUCCESS = re.compile(r"Success\s*:\s*(\d+)")
RE_FAIL    = re.compile(r"Failure\s*:\s*(\d+)")
RE_CRASH   = re.compile(r"Crash\s*:\s*(\d+)")
RE_TIMEOUT = re.compile(r"Timeout\s*:\s*(\d+)")


def run_and_report(client_script: str):
    client_path = (PROJECT_ROOT / client_script).resolve()
    if not client_path.is_file():
        return 2, {"success": None, "failure": None, "crash": None, "timeout": None}, "", f"Client script not found: {client_path}"

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    p = subprocess.run(
        [sys.executable, "main.py", client_script],
        cwd=str(PROJECT_ROOT),              # ✅ correct cwd
        text=True,
        capture_output=True,
        encoding="utf-8",                   # ✅ preserve emojis
        errors="replace",
        env=env,
    )

    counts = {"success": None, "failure": None, "crash": None, "timeout": None}

    for line in p.stdout.splitlines():
        if m := RE_SUCCESS.search(line):
            counts["success"] = int(m.group(1))
        if m := RE_FAIL.search(line):
            counts["failure"] = int(m.group(1))
        if m := RE_CRASH.search(line):
            counts["crash"] = int(m.group(1))
        if m := RE_TIMEOUT.search(line):
            counts["timeout"] = int(m.group(1))

    return p.returncode, counts, p.stdout, p.stderr
