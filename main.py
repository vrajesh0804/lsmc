import os
import sys
import time
import threading
import subprocess
import requests

from src.simulator import app

SIM_URL = "http://localhost:9998"
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def run_simulator():
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


def reset_localstack():
    script = os.path.join(PROJECT_ROOT, "reset_localstack.py")
    if os.path.isfile(script):
        subprocess.run([sys.executable, script], check=False)


def run_client(client_script: str) -> int:
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, client_script)]
    p = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return p.returncode


def notify_done(exit_code: int):
    payload = {"crash": (exit_code != 0), "exit_code": exit_code}
    requests.post(f"{SIM_URL}/__execution_done__", json=payload, timeout=5)


def main():
    if len(sys.argv) != 2:
        print("Usage: python main.py <client_script_path>")
        sys.exit(2)

    client_script = sys.argv[1]

    t = threading.Thread(target=run_simulator, daemon=True)
    t.start()
    wait_ready()

    run_no = 1
    while True:
        print(f"\n🔁 RUN #{run_no}")

        # Reset state so every run starts from same initial condition
        reset_localstack()

        exit_code = run_client(client_script)
        notify_done(exit_code)

        r = requests.get(f"{SIM_URL}/__ready__", timeout=5)
        if r.text.strip() == "DONE":
            print("\n✅ All executions explored.")
            break

        run_no += 1


if __name__ == "__main__":
    main()
