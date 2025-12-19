import threading
import time
import os
import sys
import subprocess
from src.simulator import app
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SIMULATOR_HOST = "0.0.0.0"
SIMULATOR_PORT = 9998
DEFAULT_NUM_RUNS = 100_000
SIMULATOR_START_DELAY = 1  # seconds
CLIENT_RUN_DELAY = 1  # seconds


def start_simulator() -> None:
    """
    Start the Flask simulator in a daemon thread.
    """
    print(f"[OK] Simulator starting on http://{SIMULATOR_HOST}:{SIMULATOR_PORT}")
    app.run(host=SIMULATOR_HOST, port=SIMULATOR_PORT, debug=False, threaded=True)


def run_client_script(script_path: str) -> None:
    """
    Execute a client script as a subprocess.
    
    Args:
        script_path (str): Relative path to the client script.
    """
    abs_path = os.path.join(PROJECT_ROOT, script_path)

    if not os.path.exists(abs_path):
        print(f"❌ Client script not found: {abs_path}")
        return

    print(f"\n🚀 Running client script: {script_path}\n")
    subprocess.run([sys.executable, abs_path])
    print(f"\n✅ Finished running: {script_path}\n")


def run_multiple_clients(script_path: str, num_runs: int) -> None:
    """
    Execute the client script multiple times, enforcing a short delay between runs.
    
    Args:
        script_path (str): Path to the client script.
        num_runs (int): Number of times to execute the client script.
    """
    for run in range(1, num_runs + 1):
        print(f"\n🔁 Executing run #{run} for client script\n")
        run_client_script(script_path)
        time.sleep(CLIENT_RUN_DELAY)


def parse_args() -> tuple[str, int]:
    """
    Parse command-line arguments.

    Returns:
        Tuple of (client_script_path, num_runs)
    """
    if len(sys.argv) < 2:
        print("Usage: python main.py <client_script.py> [num_runs]")
        sys.exit(1)

    client_script = sys.argv[1]
    num_runs = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_NUM_RUNS
    return client_script, num_runs


def main() -> None:
    client_script, num_runs = parse_args()

    # Start simulator in a daemon thread
    sim_thread = threading.Thread(target=start_simulator, daemon=True)
    sim_thread.start()
    time.sleep(SIMULATOR_START_DELAY)  # Give simulator time to initialize

    # Run client scripts multiple times
    run_multiple_clients(client_script, num_runs)

    print("All client runs completed. Simulator is still running.")
    print("Press CTRL+C to stop the simulator.\n")

    # Keep the main thread alive to allow simulator to continue
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping simulator...")
        print("Stopped.")


if __name__ == "__main__":
    main()
