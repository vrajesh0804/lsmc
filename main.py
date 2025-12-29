import threading
import time
import os
import sys
import subprocess
import requests
from src.simulator import app

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SIM_URL = "http://localhost:9998"


def run_simulator():
    print("[OK] Simulator started on http://localhost:9998")
    app.run(host="0.0.0.0", port=9998, threaded=True)


def run_client(script):
    subprocess.run([sys.executable, script], check=True)


def wait_for_simulator():
    while True:
        r = requests.get(f"{SIM_URL}/__ready__")
        if r.text == "READY":
            return True
        if r.text == "DONE":
            return False
        time.sleep(0.2)


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <client_script.py>")
        sys.exit(1)

    client_script = os.path.join(PROJECT_ROOT, sys.argv[1])

    sim_thread = threading.Thread(target=run_simulator, daemon=True)
    sim_thread.start()
    time.sleep(1)

    run_count = 1
    while True:
        print(f"\n🔁 Executing run #{run_count}")
        run_client(client_script)
        if not wait_for_simulator():
            print("✅ All valid executions completed.")
            break
        run_count += 1


if __name__ == "__main__":
    main()
