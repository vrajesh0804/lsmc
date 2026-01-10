import threading
import time
from dataclasses import dataclass
from typing import List, Optional

RUN_SUCCESS = "SUCCESS"
RUN_TIMEOUT = "TIMEOUT"

@dataclass
class PrefixScheduler:
    """
    Enforces a forced prefix, one step at a time.
    When prefix is complete -> stops enforcing.
    """
    cond: threading.Condition
    deadlock_timeout_s: int = 8

    forced_prefix: List[str] = None
    forced_pos: int = 0
    enforcing: bool = False
    last_progress_time: float = 0.0

    # shared run-status refs (kept outside, but scheduler modifies them)
    current_result_ref: Optional[dict] = None     # {"value": "..."}
    failure_reason_ref: Optional[dict] = None     # {"value": "..."}

    def start_run(self, prefix: List[str]):
        self.forced_prefix = prefix
        self.forced_pos = 0
        self.enforcing = True if prefix else False
        self.last_progress_time = time.time()

    def is_prefix_complete(self) -> bool:
        return self.forced_pos >= len(self.forced_prefix or [])

    def maybe_stop_enforcing(self):
        if self.enforcing and self.is_prefix_complete():
            self.enforcing = False

    def wait_for_turn(self, step: str) -> bool:
        """
        Block until it's step's turn in forced prefix.
        Returns False if run already failed/timed-out.
        """
        while True:
            if not self.enforcing or self.is_prefix_complete():
                return True

            expected = self.forced_prefix[self.forced_pos]
            if step == expected:
                self.forced_pos += 1
                self.last_progress_time = time.time()
                self.cond.notify_all()
                return True

            if self.current_result_ref and self.current_result_ref["value"] != RUN_SUCCESS:
                return False

            self.cond.wait(timeout=0.5)

    def watchdog_tick(self):
        """
        Call periodically. If enforcing but no progress too long -> mark TIMEOUT.
        """
        if not self.enforcing or self.is_prefix_complete():
            return

        if not self.current_result_ref or not self.failure_reason_ref:
            return

        if self.current_result_ref["value"] != RUN_SUCCESS:
            return

        idle = time.time() - self.last_progress_time
        if idle > self.deadlock_timeout_s:
            self.current_result_ref["value"] = RUN_TIMEOUT
            self.failure_reason_ref["value"] = (
                f"Infeasible forced prefix: blocked waiting for step #{self.forced_pos + 1} "
                f"for > {self.deadlock_timeout_s}s"
            )
            self.cond.notify_all()
