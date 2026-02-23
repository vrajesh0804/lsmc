import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test.client.expected_client_summaries import EXPECTED_CLIENT_SUMMARIES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAIN_PY = PROJECT_ROOT / "main.py"

RE_SUMMARY_HEADER = re.compile(r"(?:📊\s*)?FINAL SUMMARY")
RE_SUCCESS = re.compile(r"Success\s*:\s*(\d+)")
RE_FAILURE = re.compile(r"Failure\s*:\s*(\d+)")
RE_CRASH = re.compile(r"Crash\s*:\s*(\d+)")
RE_TIMEOUT = re.compile(r"Timeout\s*:\s*(\d+)")


def fmt_counts(c: dict) -> str:
    return f"Success={c['success']} Failure={c['failure']} Crash={c['crash']} Timeout={c['timeout']}"


def parse_final_summary_from_lines(lines: list[str]) -> dict | None:
    last_idx = -1
    for i, line in enumerate(lines):
        if RE_SUMMARY_HEADER.search(line):
            last_idx = i
    if last_idx == -1:
        return None

    window = lines[last_idx:last_idx + 30]
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
    if os.name != "nt":
        return
    cmd = (
        r'for /f "tokens=5" %a in ('
        rf'\'netstat -ano ^| findstr :{port} ^| findstr LISTENING\''
        r") do taskkill /PID %a /F"
    )
    try:
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


def run_main(client_args: str, timeout_s: int = 900) -> tuple[int, list[str]]:
    """
    Run main.py but STREAM stdout to avoid pipe backpressure changing scheduling.
    Returns (returncode, stdout_lines).
    """
    _kill_listeners_on_port(9998)

    if isinstance(client_args, str):
        # allow "--drop <path>" form
        parts = client_args.split()
        cmd = [sys.executable, str(MAIN_PY)] + parts
    else:
        cmd = [sys.executable, str(MAIN_PY)] + list(client_args)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"          # ✅ important: flush through pipes
    env["SIM_FREE_GATHER_MS"] = "200"      # ✅ give time for both threads to arrive
    env["SIM_FREE_MIN_THREADS"] = "2"      # ✅ works with scheduler patch above
    env.setdefault("PYTHONHASHSEED", "0")

    print("\n" + "=" * 80)
    print(f"[pytest] Running: {' '.join(cmd)}")
    print("=" * 80, flush=True)

    p = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,  # line-buffer
    )

    lines: list[str] = []
    try:
        assert p.stdout is not None
        for line in iter(p.stdout.readline, ""):
            if line == "" and p.poll() is not None:
                break
            lines.append(line.rstrip("\n"))
            # mirror to pytest console (so -s still feels like terminal)
            print(line, end="")
        rc = p.wait(timeout=timeout_s)
        return rc, lines
    finally:
        try:
            p.kill()
        except Exception:
            pass


def pytest_sessionstart(session):
    print("\n[pytest] Clients under test (with expected baselines):")
    for i, (client, exp) in enumerate(EXPECTED_CLIENT_SUMMARIES.items(), start=1):
        print(f"  {i}. {client} -> {fmt_counts(exp)}")
    print("", flush=True)


@pytest.mark.parametrize("client_script", list(EXPECTED_CLIENT_SUMMARIES.keys()))
def test_client_runs_via_main_py_and_matches_expected_summary(client_script: str):
    expected = EXPECTED_CLIENT_SUMMARIES[client_script]

    rc, out_lines = run_main(client_script, timeout_s=900)
    got = parse_final_summary_from_lines(out_lines)

    if got is not None:
        print("\n[pytest] FINAL SUMMARY (parsed from main.py output)")
        print(f"  Success : {got['success']}")
        print(f"  Failure : {got['failure']}")
        print(f"  Crash   : {got['crash']}")
        print(f"  Timeout : {got['timeout']}")
        print("", flush=True)
    else:
        tail = "\n".join(out_lines[-80:])
        raise AssertionError(
            f"[pytest] FAILED for {client_script}\n"
            f"Reason   : FINAL SUMMARY not found in output\n"
            f"Expected : {fmt_counts(expected)}\n"
            f"--- stdout tail (last 80 lines) ---\n{tail}"
        )

    if rc != 0:
        raise AssertionError(
            f"[pytest] FAILED for {client_script}\n"
            f"Reason   : main.py exited with non-zero code ({rc})"
        )

    if got != expected:
        raise AssertionError(
            f"[pytest] FAILED for {client_script}\n"
            f"Expected : {fmt_counts(expected)}\n"
            f"Got      : {fmt_counts(got)}"
        )