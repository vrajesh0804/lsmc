import threading
import time
import os
import sys
from src.simulator import app

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

def run_simulator_thread():
    print("[OK] Simulator started on http://localhost:9998")
    app.run(host="0.0.0.0", port=9998, debug=False, threaded=True)


def run_client_script(script_path):
    print(f"\n🚀 Running client script: {script_path}\n")
    abs_path = os.path.join(PROJECT_ROOT, script_path)

    if not os.path.exists(abs_path):
        print(f"❌ Client script not found: {abs_path}")
        return

    import subprocess
    subprocess.run([sys.executable, abs_path])


def main():
    print("🚀 Starting simulator...\n")

    sim_thread = threading.Thread(target=run_simulator_thread, daemon=True)
    sim_thread.start()

    if len(sys.argv) > 1:
        client_script = sys.argv[1]
        time.sleep(1)
        run_client_script(client_script)

    print("Press CTRL+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping simulator...")
        print("Stopped.")


if __name__ == "__main__":
    main()
