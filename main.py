# main.py
import os
import sys
import time
import threading
import subprocess
from pathlib import Path

import requests

SIM_URL = "http://localhost:9998"
PROJECT_ROOT = Path(__file__).resolve().parent


def run_simulator():
    # Import inside the thread so env vars are set before module import
    from src.simulator import app

    print("[SIM] Simulator started on http://localhost:9998", flush=True)
    app.run(host="0.0.0.0", port=9998, threaded=True)


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


def run_client(client_script: str) -> int:
    # Run client as: python <client_script>
    client_abs = (PROJECT_ROOT / client_script).resolve()
    cmd = [sys.executable, str(client_abs)]
    p = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return p.returncode


def notify_done(exit_code: int) -> None:
    payload = {"crash": (exit_code != 0), "exit_code": exit_code}
    for _ in range(3):
        try:
            requests.post(f"{SIM_URL}/__execution_done__", json=payload, timeout=30)
            return
        except requests.exceptions.ReadTimeout:
            time.sleep(1.0)
    # last attempt failed -> bubble up
    requests.post(f"{SIM_URL}/__execution_done__", json=payload, timeout=30)


def main() -> int:
    args = sys.argv[1:]

    treat_404_as_fail = False
    if "--404-as-fail" in args:
        treat_404_as_fail = True
        args.remove("--404-as-fail")

    if len(args) != 1:
        print("Usage: python main.py [--404-as-fail] <client_script_path>")
        return 2

    client_script = args[0]

    # Must be set BEFORE src.simulator is imported (it reads env at import time)
    os.environ["SIM_404_AS_FAIL"] = "1" if treat_404_as_fail else "0"

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

        exit_code = run_client(client_script)
        notify_done(exit_code)
        time.sleep(0.05)

        run_no += 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
