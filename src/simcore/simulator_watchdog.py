# src/simcore/simulator_watchdog.py
import time
import threading

def start_watchdog_thread(state) -> None:
    def loop():
        while True:
            time.sleep(0.25)
            with state.cond:
                if state.done:
                    return
                state.scheduler.watchdog_tick()

    threading.Thread(target=loop, daemon=True).start()