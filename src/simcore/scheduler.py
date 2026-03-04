import os
import time
from typing import List, Optional, Tuple, Set

from src.simcore.dpor import DROP_PREFIX, undrop, DELAY_PREFIX, undelay, delay_seconds

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
        *,
        cond,
        deadlock_timeout_s: int,
        current_result_ref: dict,
        failure_reason_ref: dict,
    ):
        # IMPORTANT: keep `cond` keyword arg API (your simulator calls PrefixScheduler(cond=cond,...))
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

        # NEW: delay info
        self._last_was_delay: bool = False
        self._last_delay_s: int = 0

        # FREE-run determinism knobs
        self._free_gather_ms = int(os.environ.get("SIM_FREE_GATHER_MS", "50"))
        self._free_min_unique_threads = int(os.environ.get("SIM_FREE_MIN_THREADS", "2"))

        # FREE-run state
        self._free_waiting: List[str] = []
        self._free_released_count: int = 0
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
        self._last_was_delay = False
        self._last_delay_s = 0

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

    def consume_last_match(self) -> Tuple[Optional[str], bool, bool, int]:
        """
        Returns: (matched_step_or_None, was_drop, was_delay, delay_seconds)

        - matched_step_or_None:
            * enforcing: the expected token (so includes DROP/DELAY wrapper)
            * free mode: the presented step
        - was_drop:
            True if the expected token was DROP::... (or DROP::DELAY::...)
        - was_delay:
            True if the expected token was DELAY::... (or DROP::DELAY::...)
        - delay_seconds:
            parsed seconds (0 if not delayed or malformed)
        """
        ms = self._last_matched_step
        wd = self._last_was_drop
        wdel = self._last_was_delay
        ds = self._last_delay_s

        self._last_matched_step = None
        self._last_was_drop = False
        self._last_was_delay = False
        self._last_delay_s = 0
        return ms, wd, wdel, ds

    @staticmethod
    def _extract_thread(step: str) -> str:
        # step may include DROP/DELAY wrappers
        raw = step
        if raw.startswith(DROP_PREFIX):
            raw = undrop(raw)
        if raw.startswith(DELAY_PREFIX):
            raw = undelay(raw)
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
        self._free_deadline = None

    def wait_for_turn(self, presented_step: str) -> bool:
        while True:
            if self.current_result_ref["value"] != RUN_SUCCESS:
                return False

            # ---------- FREE RUN ----------
            if not self.enforcing:
                if presented_step not in self._free_waiting:
                    self._free_waiting.append(presented_step)

                tname = self._extract_thread(presented_step)
                self._free_seen_threads.add(tname)

                self._free_set_deadline_if_needed()

                # first free decision: optionally wait for >= N unique threads
                if self._free_released_count == 0 and self._free_min_unique_threads > 1:
                    if len(self._free_seen_threads) < self._free_min_unique_threads:
                        now = time.time()
                        if now < (self._free_deadline or now):
                            self.cond.wait(timeout=max(0.0, (self._free_deadline or now) - now))
                            continue

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
                    self._last_was_delay = False
                    self._last_delay_s = 0
                    self._last_progress_at = time.time()

                    self._free_reset_for_next_choice()
                    return True

                self.cond.wait(timeout=0.1)
                continue

            # ---------- ENFORCING ----------
            exp = self.expected_now()

            # exact match (rare; usually presented is unwrapped)
            if exp == presented_step:
                self.forced_pos += 1
                self._set_last_match_from_expected(exp)
                self._last_progress_at = time.time()
                return True

            # expected DROP::X but presented X
            if exp and exp.startswith(DROP_PREFIX) and undrop(exp) == presented_step:
                self.forced_pos += 1
                self._set_last_match_from_expected(exp)
                self._last_progress_at = time.time()
                return True

            # expected DELAY::<s>::X but presented X
            if exp and exp.startswith(DELAY_PREFIX) and undelay(exp) == presented_step:
                self.forced_pos += 1
                self._set_last_match_from_expected(exp)
                self._last_progress_at = time.time()
                return True

            # expected DROP::DELAY::<s>::X but presented X
            if exp and exp.startswith(DROP_PREFIX):
                inner = undrop(exp)
                if inner.startswith(DELAY_PREFIX) and undelay(inner) == presented_step:
                    self.forced_pos += 1
                    self._set_last_match_from_expected(exp)
                    self._last_progress_at = time.time()
                    return True

            self.cond.wait(timeout=0.1)

    def _set_last_match_from_expected(self, exp: Optional[str]) -> None:
        """
        exp is the forced expected token (may be wrapped).
        """
        self._last_matched_step = exp

        self._last_was_drop = bool(exp and exp.startswith(DROP_PREFIX))

        # delay may be outer (DELAY::...) or inner (DROP::DELAY::...)
        self._last_was_delay = False
        self._last_delay_s = 0

        if not exp:
            return

        if exp.startswith(DELAY_PREFIX):
            self._last_was_delay = True
            self._last_delay_s = int(delay_seconds(exp) or 0)
            return

        if exp.startswith(DROP_PREFIX):
            inner = undrop(exp)
            if inner.startswith(DELAY_PREFIX):
                self._last_was_delay = True
                self._last_delay_s = int(delay_seconds(inner) or 0)

    def watchdog_tick(self) -> None:
        # Preserve your semantics: watchdog only matters while enforcing
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
                f"Timeout while enforcing forced prefix: waited > {self.deadlock_timeout_s}s for step #{step_no}"
            )
            self.cond.notify_all()