import os
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

RUN_SUCCESS = "SUCCESS"
RUN_TIMEOUT = "TIMEOUT"


def _default_deadlock_timeout() -> int:
    """
    After this many seconds without progress while enforcing a forced prefix,
    we classify the run as TIMEOUT.

    Override with:
      SIM_FORCED_PREFIX_TIMEOUT=15
    """
    try:
        return int(os.environ.get("SIM_FORCED_PREFIX_TIMEOUT", "15"))
    except Exception:
        return 15


@dataclass
class PrefixScheduler:
    """
    Enforces a forced prefix, one step at a time.

    - If enforcing=True, only the next expected step may proceed.
    - When the forced prefix is fully consumed -> enforcement stops.
    - If enforcing but no progress for deadlock_timeout_s -> mark RUN_TIMEOUT.
    """
    cond: threading.Condition
    deadlock_timeout_s: int = _default_deadlock_timeout()

    forced_prefix: List[str] = None
    forced_pos: int = 0
    enforcing: bool = False
    last_progress_time: float = 0.0

    # shared run-status refs (scheduler updates these)
    current_result_ref: Optional[dict] = None   # {"value": "..."}
    failure_reason_ref: Optional[dict] = None   # {"value": "..."}

    def start_run(self, prefix: List[str]) -> None:
        self.forced_prefix = prefix or []
        self.forced_pos = 0
        self.enforcing = True if self.forced_prefix else False
        self.last_progress_time = time.time()
        self.cond.notify_all()

    def is_prefix_complete(self) -> bool:
        return self.forced_pos >= len(self.forced_prefix or [])

    def maybe_stop_enforcing(self) -> None:
        if self.enforcing and self.is_prefix_complete():
            self.enforcing = False
            self.cond.notify_all()

    def _run_is_active(self) -> bool:
        if not self.current_result_ref:
            return True
        return self.current_result_ref.get("value", RUN_SUCCESS) == RUN_SUCCESS

    def wait_for_turn(self, step: str) -> bool:
        """
        Block until it's this step's turn in the forced prefix.

        Returns:
          True  -> caller may proceed (step is allowed now)
          False -> run already ended (TIMEOUT/FAILURE/CRASH etc.)
        """
        while True:
            if not self.enforcing or self.is_prefix_complete():
                return True

            expected = self.forced_prefix[self.forced_pos]
            if step == expected:
                # Consume expected step (works for DROP steps too since it's just a string)
                self.forced_pos += 1
                self.last_progress_time = time.time()
                self.cond.notify_all()
                return True

            if not self._run_is_active():
                return False

            self.cond.wait(timeout=0.5)

    def watchdog_tick(self) -> None:
        """
        Periodic tick. If enforcing and no progress for too long -> mark TIMEOUT.
        """
        if not self.enforcing or self.is_prefix_complete():
            return

        if not self.current_result_ref or not self.failure_reason_ref:
            return

        if self.current_result_ref.get("value") != RUN_SUCCESS:
            return

        idle = time.time() - self.last_progress_time
        if idle > self.deadlock_timeout_s:
            self.current_result_ref["value"] = RUN_TIMEOUT
            self.failure_reason_ref["value"] = (
                f"Timeout while enforcing forced prefix: blocked waiting for step #{self.forced_pos + 1} "
                f"for > {self.deadlock_timeout_s}s"
            )
            self.cond.notify_all()
