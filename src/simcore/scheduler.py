import os
import time
from typing import List, Optional, Tuple, Set

from src.simcore.dpor import DROP_PREFIX, undrop, DELAY_PREFIX, undelay, delay_seconds

RUN_SUCCESS = "SUCCESS"
RUN_FAILURE = "FAILURE"
RUN_CRASH = "CRASH"     # kept for compatibility, but scheduler will not set it anymore
RUN_TIMEOUT = "TIMEOUT" # ✅ watchdog/enforcement timeout uses this now


class PrefixScheduler:
    """
    Enforces a forced prefix over incoming steps.

    Deterministic FREE runs:
      - buffer contenders for a small gather window EACH step
      - pick lexicographically smallest presented_step among buffered contenders
      - (optional) for the very first free decision, wait until we have seen
        at least N unique threads, or until the gather window expires
      - Watchdog timeout while enforcing forced prefix => RUN_TIMEOUT (not RUN_CRASH)
      - When --drop and --delay are used together:
          * We allow forced tokens of the form DROP::<step> OR DELAY::<s>::<step>
          * We DO NOT allow nested tokens like DROP::DELAY::... (or DELAY::DROP::...)
            because you asked: "make sure the drop one can't be delay in same execution"
            (i.e., a single base step cannot be both dropped and delayed in the same run)
        If DPOR accidentally generates nested wrappers, the scheduler will treat it as invalid
        and never match it (so the run will TIMEOUT under watchdog).
    """

    def __init__(
        self,
        *,
        cond,
        deadlock_timeout_s: int,
        current_result_ref: dict,
        failure_reason_ref: dict,
    ):
        """
            cond : A condition variable for synchronization between threads.
            deadlock_timeout_s : How long to wait before declaring timeout during forced prefix enforcement.
            current_result_ref : A shared dictionary holding current run result
            failure_reason_ref : Shared dictionary storing failure explanation string.
        """
        # IMPORTANT: keep `cond` keyword arg API
        self.cond = cond
        self.deadlock_timeout_s = deadlock_timeout_s
        self.current_result_ref = current_result_ref
        self.failure_reason_ref = failure_reason_ref

        self.forced_prefix: List[str] = [] # the prefix we want to enforce
        self.enforcing: bool = False # whether we are currently enforcing
        self.forced_pos: int = 0 # which position in forced prefix we are waiting for now

        now = time.time() # current time
        self._last_progress_at: float = now # Stores last time a forced step successfully matched.

        # last match info (set by wait_for_turn, read by simulator)
        self._last_matched_step: Optional[str] = None
        self._last_was_drop: bool = False
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
        # Starts a new run with a given forced prefix.
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
        # Returns the next forced token we are currently expecting.
        if self.enforcing and self.forced_pos < len(self.forced_prefix):
            return self.forced_prefix[self.forced_pos]
        return None

    def is_prefix_complete(self) -> bool:
        # Checks whether forced prefix is already fully matched.
        return (not self.enforcing) or (self.forced_pos >= len(self.forced_prefix))

    def maybe_stop_enforcing(self) -> None:
        # If forced prefix is fully consumed, turn off enforcement.
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
            True if the expected token was DROP::...
        - was_delay:
            True if the expected token was DELAY::... (NOT nested)
        - delay_seconds:
            parsed seconds (0 if not delayed or malformed)
        """
        # Scheduler only decides who is allowed.
        # But simulator needs to know how to execute it.
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
    def _is_nested_wrapper(exp: str) -> bool:
        """
        Returns True if exp is something like DROP::DELAY::... or DELAY::DROP::...
        We treat these as invalid per your requirement (no step can be both dropped and delayed).
        """
        if exp.startswith(DROP_PREFIX):
            inner = undrop(exp)
            return inner.startswith(DELAY_PREFIX) or inner.startswith(DROP_PREFIX)
        if exp.startswith(DELAY_PREFIX):
            inner = undelay(exp)
            return inner.startswith(DROP_PREFIX) or inner.startswith(DELAY_PREFIX)
        return False

    @staticmethod
    def _extract_thread(step: str) -> str:
        # step may include DROP/DELAY wrappers
        # Extracts thread name from a step, even if wrapped by DROP/DELAY.
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
        # If no gather deadline exists yet, set one.
        if self._free_deadline is None:
            if self._free_gather_ms <= 0:
                self._free_deadline = time.time()
            else:
                self._free_deadline = time.time() + (self._free_gather_ms / 1000.0)

    def _free_reset_for_next_choice(self) -> None:
        # Resets free-mode deadline after one choice is made.
        self._free_deadline = None

    def wait_for_turn(self, presented_step: str) -> bool:
        """
            This is the heart of the scheduler.
            Each thread calls this when it wants to execute a step.
            The scheduler blocks until:
            step is allowed, then returns True
            or run already failed/timed out, then returns False
        """
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
            if exp is None:
                # should not happen, but be safe
                self.enforcing = False
                self.cond.notify_all()
                return True

            # ✅ Disallow nested wrapper tokens by design
            if self._is_nested_wrapper(exp):
                # never match; let watchdog mark TIMEOUT for incorrect prefix generation
                self.cond.wait(timeout=0.1)
                continue

            # exact match (rare; usually presented is unwrapped)
            if exp == presented_step:
                self.forced_pos += 1
                self._set_last_match_from_expected(exp)
                self._last_progress_at = time.time()
                return True

            # expected DROP::<X> but presented X
            if exp.startswith(DROP_PREFIX) and undrop(exp) == presented_step:
                self.forced_pos += 1
                self._set_last_match_from_expected(exp)
                self._last_progress_at = time.time()
                return True

            # expected DELAY::<s>::<X> but presented X
            if exp.startswith(DELAY_PREFIX) and undelay(exp) == presented_step:
                self.forced_pos += 1
                self._set_last_match_from_expected(exp)
                self._last_progress_at = time.time()
                return True

            self.cond.wait(timeout=0.1)

    def _set_last_match_from_expected(self, exp: Optional[str]) -> None:
        """
        exp is the forced expected token (may be wrapped).
        Nested wrappers are disallowed; this function assumes non-nested exp.
        """
        # Stores metadata for the last matched forced token.
        self._last_matched_step = exp
        self._last_was_drop = bool(exp and exp.startswith(DROP_PREFIX))

        self._last_was_delay = False
        self._last_delay_s = 0

        if not exp:
            return

        if exp.startswith(DELAY_PREFIX):
            self._last_was_delay = True
            self._last_delay_s = int(delay_seconds(exp) or 0)
            return

        # DROP::<...> only (no delay inside allowed)
        if exp.startswith(DROP_PREFIX):
            self._last_was_delay = False
            self._last_delay_s = 0

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
            # ✅ thesis semantics: watchdog is a TIMEOUT (not CRASH)
            self.current_result_ref["value"] = RUN_TIMEOUT
            self.failure_reason_ref["value"] = (
                f"Timeout while enforcing forced prefix: waited > {self.deadlock_timeout_s}s for step #{step_no}"
            )
            self.cond.notify_all()