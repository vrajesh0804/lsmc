# main.py
import os
import sys
import time
import threading
import subprocess
from pathlib import Path
from typing import Tuple, List, Optional

import requests

SIM_URL = "http://localhost:9998"
PROJECT_ROOT = Path(__file__).resolve().parent


def run_simulator():
    try:
        from src.simulator import app
        print("[SIM] Simulator started on http://localhost:9998", flush=True)
        app.run(host="0.0.0.0", port=9998, threaded=True)
    except Exception as e:
        import traceback
        print(f"[SIM] Simulator thread crashed: {e}", flush=True)
        traceback.print_exc()


def wait_ready(timeout: int = 15) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{SIM_URL}/__ready__", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("Simulator not reachable")


def _classify_client_error_from_lines(
    lines: List[str],
    *,
    treat_408_as_timeout: bool,
) -> Tuple[bool, Optional[str], str]:
    """
    Returns:
      (client_had_error, error_kind, detail)

    error_kind in { "TIMEOUT", "FAILURE" } or None if no error.

    Important:
      - HTTP 408 is treated as TIMEOUT only when --408-as-timeout is enabled.
      - Otherwise HTTP 408 is ignored here so simulator-side classification can decide
        whether it should count as SUCCESS.
    """
    had_error = False
    detail = ""

    for ln in lines:
        if "CLIENT_HAD_ERROR=1" in ln:
            had_error = True

    if not had_error:
        return (False, None, "")

    candidates = list(reversed(lines[-80:]))
    saw_408 = any(("HTTP 408" in ln or "ClientError 408" in ln) for ln in candidates)

    timeout_markers = [
        "ReadTimeout",
        "ReadTimeoutError",
        "Timeout",
        "TIMEOUT",
    ]
    if treat_408_as_timeout:
        timeout_markers.extend(["HTTP 408", "ClientError 408"])

    for ln in candidates:
        if "FAIL" in ln or any(m in ln for m in timeout_markers) or (treat_408_as_timeout and "HTTP 408" in ln):
            detail = ln.strip()
            break

    # When --408-as-timeout is OFF, a client-side HTTP 408 should not downgrade the run.
    if saw_408 and not treat_408_as_timeout:
        other_timeout = any(
            (m in detail) for m in ["ReadTimeout", "ReadTimeoutError", "Timeout", "TIMEOUT"]
        ) if detail else False
        if not other_timeout:
            return (False, None, "")

    is_timeout = any(m in detail for m in timeout_markers) if detail else False

    kind = "TIMEOUT" if is_timeout else "FAILURE"
    if not detail:
        detail = "Client reported error (no detail line found)"
    return (True, kind, detail)


def run_client_streaming(client_script: str) -> Tuple[int, bool, Optional[str], str]:
    """
    Run client as: python <client_script>
    Stream stdout/stderr to console (no pipe backpressure),
    but also capture lines to classify client-level failures.
    """
    client_abs = (PROJECT_ROOT / client_script).resolve()
    cmd = [sys.executable, str(client_abs)]

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    p = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    lines: List[str] = []
    assert p.stdout is not None
    for line in p.stdout:
        print(line, end="")  # keep terminal output exactly like before
        lines.append(line.rstrip("\n"))

    rc = p.wait()
    client_had_error, client_error_kind, client_error_detail = _classify_client_error_from_lines(
        lines,
        treat_408_as_timeout=(env.get("SIM_408_AS_TIMEOUT", "0") == "1"),
    )
    return rc, client_had_error, client_error_kind, client_error_detail


def notify_done(
    exit_code: int,
    *,
    client_had_error: bool,
    client_error_kind: Optional[str],
    client_error_detail: str,
) -> None:
    payload = {
        "crash": (exit_code != 0),
        "exit_code": exit_code,
        "client_had_error": bool(client_had_error),
        "client_error_kind": client_error_kind,      # "TIMEOUT" / "FAILURE" / None
        "client_error_detail": client_error_detail,  # short human message
    }
    for _ in range(3):
        try:
            requests.post(f"{SIM_URL}/__execution_done__", json=payload, timeout=30)
            return
        except requests.exceptions.ReadTimeout:
            time.sleep(1.0)
    requests.post(f"{SIM_URL}/__execution_done__", json=payload, timeout=30)


def main() -> int:
    args = sys.argv[1:]

    treat_404_as_fail = False
    enable_drop = False
    enable_delay = False
    treat_408_as_timeout = False

    # ✅ Default delay when --delay is enabled: 120s (2 minutes)
    delay_seconds = 20

    # Optional: override forced-prefix timeout (seconds)
    forced_prefix_timeout: Optional[int] = None

    if "--404-as-fail" in args:
        treat_404_as_fail = True
        args.remove("--404-as-fail")

    if "--drop" in args:
        enable_drop = True
        args.remove("--drop")

    if "--delay" in args:
        enable_delay = True
        args.remove("--delay")

    if "--408-as-timeout" in args:
        treat_408_as_timeout = True
        args.remove("--408-as-timeout")

    # NEW: --forced-prefix-timeout N
    if "--forced-prefix-timeout" in args:
        i = args.index("--forced-prefix-timeout")
        if i + 1 >= len(args):
            print("Usage: python main.py [--forced-prefix-timeout N] ...", flush=True)
            return 2
        try:
            forced_prefix_timeout = int(args[i + 1])
        except ValueError:
            print("ERROR: --forced-prefix-timeout must be an integer", flush=True)
            return 2
        del args[i : i + 2]

    # ✅ NEW: --delay-for-<N>
    delay_for_val: Optional[int] = None
    delay_for_tokens = [a for a in args if a.startswith("--delay-for-")]
    if delay_for_tokens:
        if len(delay_for_tokens) > 1:
            print("ERROR: use only one --delay-for-<N>", flush=True)
            return 2

        tok = delay_for_tokens[0]
        args.remove(tok)

        s = tok[len("--delay-for-") :]
        if not s.isdigit():
            print("ERROR: --delay-for-<N> requires integer N, e.g. --delay-for-120", flush=True)
            return 2
        delay_for_val = int(s)

    # Optional (kept): --delay-seconds N
    if "--delay-seconds" in args:
        i = args.index("--delay-seconds")
        if i + 1 >= len(args):
            print("Usage: python main.py [--delay-seconds N] ...", flush=True)
            return 2
        try:
            val = int(args[i + 1])
        except ValueError:
            print("ERROR: --delay-seconds must be an integer", flush=True)
            return 2
        del args[i : i + 2]

        if delay_for_val is not None:
            print("ERROR: use either --delay-for-<N> OR --delay-seconds N (not both)", flush=True)
            return 2
        delay_for_val = val

    # Apply delay override only if --delay was actually enabled
    if enable_delay and delay_for_val is not None:
        delay_seconds = delay_for_val

    if len(args) != 1:
        print(
            "Usage: python main.py [--404-as-fail] [--408-as-timeout] [--drop] [--delay] "
            "[--delay-for-<N>] [--delay-seconds N] [--forced-prefix-timeout N] <client_script_path>",
            flush=True,
        )
        return 2

    client_script = args[0]

    # Auto forced-prefix timeout:
    # If delay is enabled, ensure timeout > delay so we don't falsely TIMEOUT during enforced DELAY.
    if forced_prefix_timeout is None:
        forced_prefix_timeout = max(60, delay_seconds + 30) if enable_delay else 60

    # Must be set BEFORE src.simulator is imported (it reads env at import time)
    os.environ["SIM_404_AS_FAIL"] = "1" if treat_404_as_fail else "0"
    os.environ["SIM_408_AS_TIMEOUT"] = "1" if treat_408_as_timeout else "0"
    os.environ["SIM_ENABLE_DROP"] = "1" if enable_drop else "0"
    os.environ["SIM_ENABLE_DELAY"] = "1" if enable_delay else "0"
    os.environ["SIM_DELAY_SECONDS"] = str(delay_seconds)
    os.environ["SIM_FORCED_PREFIX_TIMEOUT"] = str(forced_prefix_timeout)

    # Start simulator in-process (daemon thread)
    t = threading.Thread(target=run_simulator, daemon=True)
    t.start()

    wait_ready()

    # Optional: reset LocalStack once before first run to get stable starting state
    try:
        from src.simcore.sim_helpers import reset_localstack_quiet

        print("[MAIN] Resetting LocalStack before first execution...", flush=True)
        reset_localstack_quiet()
    except Exception as e:
        print(f"[MAIN] Warning: unable to reset LocalStack before first run: {e}", flush=True)

    run_no = 1
    while True:
        try:
            r = requests.get(f"{SIM_URL}/__ready__", timeout=5)
            if r.text.strip() == "DONE":
                break
        except Exception:
            pass

        print(f"\n🔁 RUN #{run_no}", flush=True)

        exit_code, client_had_error, client_error_kind, client_error_detail = run_client_streaming(client_script)
        notify_done(
            exit_code,
            client_had_error=client_had_error,
            client_error_kind=client_error_kind,
            client_error_detail=client_error_detail,
        )
        time.sleep(0.05)
        run_no += 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())