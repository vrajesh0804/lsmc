import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test.client.expected_client_summaries import EXPECTED_CLIENT_SUMMARIES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAIN_PY = PROJECT_ROOT / "main.py"

RE_SUCCESS = re.compile(r"Success\s*:\s*(\d+)")
RE_FAILURE = re.compile(r"Failure\s*:\s*(\d+)")
RE_CRASH = re.compile(r"Crash\s*:\s*(\d+)")
RE_TIMEOUT = re.compile(r"Timeout\s*:\s*(\d+)")


def parse_final_summary(stdout: str) -> dict | None:
    counts = {"success": None, "failure": None, "crash": None, "timeout": None}
    for line in stdout.splitlines():
        if m := RE_SUCCESS.search(line):
            counts["success"] = int(m.group(1))
        if m := RE_FAILURE.search(line):
            counts["failure"] = int(m.group(1))
        if m := RE_CRASH.search(line):
            counts["crash"] = int(m.group(1))
        if m := RE_TIMEOUT.search(line):
            counts["timeout"] = int(m.group(1))

    if any(v is None for v in counts.values()):
        return None
    return counts


def run_main(client_script: str, timeout_s: int = 900) -> tuple[int, str, str]:
    """
    Runs EXACTLY like normal:
        python main.py <client_script>

    We capture stdout to parse FINAL SUMMARY, but the simulator + client behavior
    is the same as running from terminal.
    """
    cmd = [sys.executable, str(MAIN_PY), client_script]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

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


def fmt_counts(c: dict) -> str:
    return (
        f"Success={c['success']} Failure={c['failure']} "
        f"Crash={c['crash']} Timeout={c['timeout']}"
    )


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

    # If main.py crashed, short message
    if rc != 0:
        raise AssertionError(
            f"[pytest] FAILED for {client_script}\n"
            f"Reason   : main.py exited with non-zero code ({rc})"
        )

    # If summary missing, fail briefly
    if got is None:
        raise AssertionError(
            f"[pytest] FAILED for {client_script}\n"
            f"Reason   : FINAL SUMMARY not found in output\n"
            f"Expected : {fmt_counts(expected)}"
        )

    # Compare expected vs got – short diff only
    if got != expected:
        raise AssertionError(
            f"[pytest] FAILED for {client_script}\n"
            f"Expected : {fmt_counts(expected)}\n"
            f"Got      : {fmt_counts(got)}"
        )
