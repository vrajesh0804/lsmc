# src/simcore/scheduler.py
import time
from typing import List, Optional, Tuple

from src.simcore.dpor import DROP_PREFIX, undrop


RUN_SUCCESS = "SUCCESS"
RUN_FAILURE = "FAILURE"
RUN_CRASH = "CRASH"
RUN_TIMEOUT = "TIMEOUT"


class PrefixScheduler:
    """
    Enforces a 'forced prefix' over incoming steps.

    Key behaviors:
    - If enforcing and expected step never arrives for deadlock_timeout_s,
      watchdog_tick() marks RUN_CRASH (forced-prefix deadlock).
    - TIMEOUT is reserved for forwarding/LocalStack timeouts/exceptions.
    - DROP steps are policy decisions: the client presents the base step X,
      and if the forced prefix expects DROP::X, we must treat X as satisfying it.
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

        # Current run state
        self.forced_prefix: List[str] = []
        self.enforcing: bool = False
        self.forced_pos: int = 0

        self._run_started_at: float = time.time()
        self._last_progress_at: float = time.time()

        # Last matched step info (consumed by simulator under the same lock)
        self._last_matched_step: Optional[str] = None  # the forced-prefix token matched (may include DROP::)
        self._last_was_drop: bool = False

    def start_run(self, forced_prefix: List[str]) -> None:
        """
        Begin a new run. If forced_prefix is empty => free run (no enforcement).
        """
        self.forced_prefix = list(forced_prefix)
        self.enforcing = len(self.forced_prefix) > 0
        self.forced_pos = 0

        now = time.time()
        self._run_started_at = now
        self._last_progress_at = now

        self._last_matched_step = None
        self._last_was_drop = False

        self.cond.notify_all()

    def is_prefix_complete(self) -> bool:
        return (not self.enforcing) or (self.forced_pos >= len(self.forced_prefix))

    def expected_now(self) -> Optional[str]:
        if self.enforcing and self.forced_pos < len(self.forced_prefix):
            return self.forced_prefix[self.forced_pos]
        return None

    def consume_last_match(self) -> Tuple[Optional[str], bool]:
        """
        Returns (matched_step, was_drop) for the *most recent* successful wait_for_turn(),
        then clears it. Must be called under the same Condition lock.
        """
        ms = self._last_matched_step
        wd = self._last_was_drop
        self._last_matched_step = None
        self._last_was_drop = False
        return ms, wd

    def wait_for_turn(self, presented_step: str) -> bool:
        """
        Block until:
          - run ended (CRASH/FAILURE/TIMEOUT) -> return False
          - free run OR this satisfies the expected step -> advance and return True

        DROP rule:
          If expected == "DROP::<X>" and presented_step == "<X>",
          treat it as a match, advance forced_pos, and mark last_was_drop=True.

        The simulator calls this under the same Condition lock.
        """
        while True:
            if self.current_result_ref["value"] != RUN_SUCCESS:
                return False

            if not self.enforcing:
                self._last_matched_step = presented_step
                self._last_was_drop = False
                self._mark_progress()
                return True

            exp = self.expected_now()

            # Exact match (including "DROP::..." if simulator presented that)
            if exp == presented_step:
                self.forced_pos += 1
                self._last_matched_step = exp
                self._last_was_drop = bool(exp and exp.startswith(DROP_PREFIX))
                self._mark_progress()
                return True

            # ✅ Critical: expected is DROP::<X> but we received base step <X>
            if exp and exp.startswith(DROP_PREFIX) and undrop(exp) == presented_step:
                self.forced_pos += 1
                self._last_matched_step = exp          # record as DROP::<X>
                self._last_was_drop = True
                self._mark_progress()
                return True

            self.cond.wait(timeout=0.1)

    def maybe_stop_enforcing(self) -> None:
        """
        If we finished the forced prefix, stop enforcing for remainder of run.
        """
        if self.enforcing and self.forced_pos >= len(self.forced_prefix):
            self.enforcing = False
            self.cond.notify_all()

    def watchdog_tick(self) -> None:
        """
        If enforcing and stuck waiting too long for next forced step, mark CRASH.
        """
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

    def _mark_progress(self) -> None:
        self._last_progress_at = time.time()
