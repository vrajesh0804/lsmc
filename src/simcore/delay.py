import os

ENABLE_DELAY = os.environ.get("SIM_ENABLE_DELAY", "0") == "1"
DELAY_SECONDS = int(os.environ.get("SIM_DELAY_SECONDS", "60"))

def delay_banner(run_no: int, label: str) -> str:
    return f"[DELAY] run={run_no} delaying first step response for {DELAY_SECONDS}s -> {label}"