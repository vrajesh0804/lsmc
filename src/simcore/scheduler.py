# src/simcore/scheduler.py
import os
import time
from typing import List, Optional, Tuple, Set

from src.simcore.dpor import DROP_PREFIX, undrop

RUN_SUCCESS = "SUCCESS"
RUN_FAILURE = "FAILURE"
RUN_CRASH = "CRASH"
RUN_TIMEOUT = "TIMEOUT"


class PrefixScheduler:
    """
    Enforces a forced prefix over incoming steps.

    Deterministic FREE runs:
      - buffer contenders for a small gather window EACH step
      - pick lexicographically smallest presented_step among buffered contenders
      - (optional) for the very first free decision, wait until we have seen
        at least N unique threads, or until the gather window expires
    """

    def __init__(
        self,
        cond,
        deadlock_timeout_s: int,
        current_result_ref: dict,
        failure_reason_ref: dict,
    ):
        self.cond = cond
        self.deadlock_timeout_s = deadlock_timeout_s
        self.current_result_ref = current_result_ref
        self.failure_reason_ref = failure_reason_ref

        self.forced_prefix: List[str] = []
        self.enforcing: bool = False
        self.forced_pos: int = 0

        now = time.time()
        self._last_progress_at: float = now

        # last match info (set by wait_for_turn, read by simulator)
        self._last_matched_step: Optional[str] = None
        self._last_was_drop: bool = False

        # FREE-run determinism knobs
        self._free_gather_ms = int(os.environ.get("SIM_FREE_GATHER_MS", "50"))
        # Wait for at least this many unique threads before choosing the FIRST free step
        self._free_min_unique_threads = int(os.environ.get("SIM_FREE_MIN_THREADS", "2"))

        # FREE-run state
        self._free_waiting: List[str] = []
        self._free_released_count: int = 0

        # NEW: per-step gather deadline (not only first step)
        self._free_deadline: Optional[float] = None
        self._free_seen_threads: Set[str] = set()

    def start_run(self, forced_prefix: List[str]) -> None:
        self.forced_prefix = list(forced_prefix)
        self.enforcing = len(self.forced_prefix) > 0
        self.forced_pos = 0

        now = time.time()
        self._last_progress_at = now

        self._last_matched_step = None
        self._last_was_drop = False

        # reset FREE-run state
        self._free_waiting = []
        self._free_released_count = 0
        self._free_deadline = None
        self._free_seen_threads = set()

        self.cond.notify_all()

    def expected_now(self) -> Optional[str]:
        if self.enforcing and self.forced_pos < len(self.forced_prefix):
            return self.forced_prefix[self.forced_pos]
        return None

    def is_prefix_complete(self) -> bool:
        return (not self.enforcing) or (self.forced_pos >= len(self.forced_prefix))

    def maybe_stop_enforcing(self) -> None:
        if self.enforcing and self.forced_pos >= len(self.forced_prefix):
            self.enforcing = False
            self.cond.notify_all()

    def consume_last_match(self) -> Tuple[Optional[str], bool]:
        ms = self._last_matched_step
        wd = self._last_was_drop
        self._last_matched_step = None
        self._last_was_drop = False
        return ms, wd

    @staticmethod
    def _extract_thread(presented_step: str) -> str:
        """
        presented_step format: client:thread:method:path (or DROP::client:thread:...)
        We'll parse only enough to get thread.
        """
        raw = undrop(presented_step)
        parts = raw.split(":", 3)
        if len(parts) >= 2:
            return parts[1]
        return "unknown"

    def _free_set_deadline_if_needed(self) -> None:
        if self._free_deadline is None:
            if self._free_gather_ms <= 0:
                self._free_deadline = time.time()
            else:
                self._free_deadline = time.time() + (self._free_gather_ms / 1000.0)

    def _free_reset_for_next_choice(self) -> None:
        """
        After we choose one step, we start a new gather window for the next choice.
        """
        self._free_deadline = None
        # Keep _free_seen_threads across the whole run (helps first-step rule only)

    def wait_for_turn(self, presented_step: str) -> bool:
        """
        Blocks until:
          - run ended => False
          - FREE run => deterministic selection (gather each step, choose min)
          - enforcing => matches expected step (incl. DROP expectation)
        """
        while True:
            if self.current_result_ref["value"] != RUN_SUCCESS:
                return False

            # ---------- FREE RUN ----------
            if not self.enforcing:
                # record contender
                if presented_step not in self._free_waiting:
                    self._free_waiting.append(presented_step)

                # record seen thread (for first-step stability)
                tname = self._extract_thread(presented_step)
                self._free_seen_threads.add(tname)

                # start gather window for this free decision
                self._free_set_deadline_if_needed()

                # FIRST FREE CHOICE: optionally wait for >= N unique threads (or deadline)
                if self._free_released_count == 0 and self._free_min_unique_threads > 1:
                    # If we haven't seen enough threads yet, wait until deadline
                    if len(self._free_seen_threads) < self._free_min_unique_threads:
                        now = time.time()
                        if now < (self._free_deadline or now):
                            self.cond.wait(timeout=max(0.0, (self._free_deadline or now) - now))
                            continue

                # For ALL free choices: wait until gather deadline expires
                now = time.time()
                if self._free_deadline is not None and now < self._free_deadline:
                    self.cond.wait(timeout=max(0.0, self._free_deadline - now))
                    continue

                if not self._free_waiting:
                    self.cond.wait(timeout=0.1)
                    continue

                chosen = min(self._free_waiting)
                if presented_step == chosen:
                    self._free_waiting.remove(chosen)
                    self._free_released_count += 1

                    self._last_matched_step = presented_step
                    self._last_was_drop = False
                    self._last_progress_at = time.time()

                    # prepare next free decision
                    self._free_reset_for_next_choice()
                    return True

                self.cond.wait(timeout=0.1)
                continue

            # ---------- ENFORCING ----------
            exp = self.expected_now()

            # exact match
            if exp == presented_step:
                self.forced_pos += 1
                self._last_matched_step = exp
                self._last_was_drop = bool(exp and exp.startswith(DROP_PREFIX))
                self._last_progress_at = time.time()
                return True

            # expected is DROP::<X>, but presented is <X>
            if exp and exp.startswith(DROP_PREFIX) and undrop(exp) == presented_step:
                self.forced_pos += 1
                self._last_matched_step = exp
                self._last_was_drop = True
                self._last_progress_at = time.time()
                return True

            self.cond.wait(timeout=0.1)

    def watchdog_tick(self) -> None:
        if self.current_result_ref["value"] != RUN_SUCCESS:
            return
        if not self.enforcing:
            return
        if self.is_prefix_complete():
            return

        now = time.time()
        stuck_for = now - self._last_progress_at
        if stuck_for > self.deadlock_timeout_s:
            step_no = self.forced_pos + 1
            self.current_result_ref["value"] = RUN_CRASH
            self.failure_reason_ref["value"] = (
                f"Forced-prefix deadlock: waited > {self.deadlock_timeout_s}s for step #{step_no}"
            )
            self.cond.notify_all()