import os
import sys
import time
import threading
import subprocess
import requests

SIM_URL = "http://localhost:9998"
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def run_simulator():
    from src.simulator import app

    print("[SIM] Simulator started on http://localhost:9998")
    app.run(host="0.0.0.0", port=9998, threaded=True)


def wait_ready(timeout=15):
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
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, client_script)]
    p = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return p.returncode


def notify_done(exit_code: int):
    payload = {"crash": (exit_code != 0), "exit_code": exit_code}
    for attempt in range(3):
        try:
            requests.post(f"{SIM_URL}/__execution_done__", json=payload, timeout=30)
            return
        except requests.exceptions.ReadTimeout:
            time.sleep(1.0)
    raise


def main():
    args = sys.argv[1:]

    treat_404_as_fail = False
    if "--404-as-fail" in args:
        treat_404_as_fail = True
        args.remove("--404-as-fail")

    if len(args) != 1:
        print("Usage: python main.py [--404-as-fail] <client_script_path>")
        sys.exit(2)

    client_script = args[0]

    # ✅ SET ENV BEFORE importing src.simulator
    os.environ["SIM_404_AS_FAIL"] = "1" if treat_404_as_fail else "0"

    t = threading.Thread(target=run_simulator, daemon=True)
    t.start()
    wait_ready()

    run_no = 1
    while True:
        print(f"\n🔁 RUN #{run_no}")

        exit_code = run_client(client_script)
        notify_done(exit_code)

        r = requests.get(f"{SIM_URL}/__ready__", timeout=5)
        if r.text.strip() == "DONE":
            print("\n✅ All executions explored.")
            break

        run_no += 1


if __name__ == "__main__":
    main()
