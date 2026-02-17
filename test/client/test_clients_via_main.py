import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test.client.expected_client_summaries import EXPECTED_CLIENT_SUMMARIES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAIN_PY = PROJECT_ROOT / "main.py"

# Variable declaration
RE_SUMMARY_HEADER = re.compile(r"(?:📊\s*)?FINAL SUMMARY")
RE_SUCCESS = re.compile(r"Success\s*:\s*(\d+)")
RE_FAILURE = re.compile(r"Failure\s*:\s*(\d+)")
RE_CRASH = re.compile(r"Crash\s*:\s*(\d+)")
RE_TIMEOUT = re.compile(r"Timeout\s*:\s*(\d+)")


def fmt_counts(c: dict) -> str:
    return (
        f"Success={c['success']} Failure={c['failure']} "
        f"Crash={c['crash']} Timeout={c['timeout']}"
    )


def parse_final_summary(stdout: str) -> dict | None:
    """
    Parse the FINAL SUMMARY from main.py output.
    """
    lines = stdout.splitlines()

    last_idx = -1
    for i, line in enumerate(lines):
        if RE_SUMMARY_HEADER.search(line):
            last_idx = i

    if last_idx == -1:
        return None

    window = lines[last_idx:last_idx + 20]

    counts = {"success": None, "failure": None, "crash": None, "timeout": None}
    for line in window:
        if (m := RE_SUCCESS.search(line)) is not None:
            counts["success"] = int(m.group(1))
        if (m := RE_FAILURE.search(line)) is not None:
            counts["failure"] = int(m.group(1))
        if (m := RE_CRASH.search(line)) is not None:
            counts["crash"] = int(m.group(1))
        if (m := RE_TIMEOUT.search(line)) is not None:
            counts["timeout"] = int(m.group(1))

    if any(v is None for v in counts.values()):
        return None
    return counts


def _kill_listeners_on_port(port: int) -> None:
    """
    Windows-only: kill any process listening on the given TCP port.
    Prevents stale simulator instances from previous tests from interfering.
    """
    if os.name != "nt":
        return

    # Use netstat to get LISTENING PIDs and taskkill them.
    # We ignore errors to keep tests robust.
    cmd = (
        r'for /f "tokens=5" %a in ('
        rf'\'netstat -ano ^| findstr :{port} ^| findstr LISTENING\''
        r") do taskkill /PID %a /F"
    )
    try:
        subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        pass


def run_main(client_script: str, timeout_s: int = 900) -> tuple[int, str, str]:
    """
    Runs exactly like terminal:
        python main.py <client_script>

    Captures stdout/stderr to parse FINAL SUMMARY.
    """
    # Ensure no stale simulator is already holding the port.
    _kill_listeners_on_port(9998)

    cmd = [sys.executable, str(MAIN_PY), client_script]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["SIM_FREE_GATHER_MS"] = "50"
    env["SIM_FREE_MIN_THREADS"] = "2"
    env.setdefault("PYTHONHASHSEED", "0")

    print("\n" + "=" * 80)
    print(f"[pytest] Running: {' '.join(cmd)}")
    print("=" * 80, flush=True)

    p = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    return p.returncode, p.stdout, p.stderr


def pytest_sessionstart(session):
    print("\n[pytest] Clients under test (with expected baselines):")
    for i, (client, exp) in enumerate(EXPECTED_CLIENT_SUMMARIES.items(), start=1):
        print(f"  {i}. {client} -> {fmt_counts(exp)}")
    print("", flush=True)


@pytest.mark.parametrize("client_script", list(EXPECTED_CLIENT_SUMMARIES.keys()))
def test_client_runs_via_main_py_and_matches_expected_summary(client_script: str):
    expected = EXPECTED_CLIENT_SUMMARIES[client_script]

    rc, out, err = run_main(client_script, timeout_s=900)
    got = parse_final_summary(out)

    if got is not None:
        print("\n[pytest] FINAL SUMMARY (parsed from main.py output)")
        print(f"  Success : {got['success']}")
        print(f"  Failure : {got['failure']}")
        print(f"  Crash   : {got['crash']}")
        print(f"  Timeout : {got['timeout']}")
        print("", flush=True)
    else:
        print("\n[pytest] FINAL SUMMARY not found.", flush=True)
        # Helpful debug if parsing fails
        tail = "\n".join(out.splitlines()[-60:])
        print("[pytest] --- stdout tail (last 60 lines) ---")
        print(tail, flush=True)
        print("[pytest] --- stderr ---")
        print(err, flush=True)

    if rc != 0:
        raise AssertionError(
            f"[pytest] FAILED for {client_script}\n"
            f"Reason   : main.py exited with non-zero code ({rc})\n"
            f"stderr   : {err.strip()[:400]}"
        )

    if got is None:
        raise AssertionError(
            f"[pytest] FAILED for {client_script}\n"
            f"Reason   : FINAL SUMMARY not found in output\n"
            f"Expected : {fmt_counts(expected)}"
        )

    if got != expected:
        raise AssertionError(
            f"[pytest] FAILED for {client_script}\n"
            f"Expected : {fmt_counts(expected)}\n"
            f"Got      : {fmt_counts(got)}"
        )