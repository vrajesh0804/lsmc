# src/simcore/simulator_prefixes.py
from typing import List, Tuple
from src.simcore.dpor import parse_step
from src.simcore.sim_helpers import print_prefix

def prefix_phase_rank(pfx: List[str], phase_order: str) -> int:
    has_drop = any(s.startswith("DROP::") for s in pfx)

    def is_delay_token(s: str) -> bool:
        if s.startswith("DELAY::"):
            return True
        if s.startswith("DROP::"):
            inner = s[len("DROP::") :]
            return inner.startswith("DELAY::")
        return False

    has_delay = any(is_delay_token(s) for s in pfx)

    if not has_drop and not has_delay:
        return 0

    if phase_order == "delay_first":
        if has_delay and not has_drop:
            return 1
        if has_drop and not has_delay:
            return 2
    else:
        if has_drop and not has_delay:
            return 1
        if has_delay and not has_drop:
            return 2

    return 3  # mixed

def prefix_sort_key(pfx: List[str], phase_order: str) -> Tuple:
    return (prefix_phase_rank(pfx, phase_order), len(pfx), tuple(pfx))

def pick_next_prefix_or_done(state) -> None:
    # imports kept minimal: state has pending_prefixes etc.
    while state.pending_prefixes:
        candidate = state.pending_prefixes.popleft()
        tp = tuple(candidate)

        if tp in state.explored_prefixes:
            continue
        if tp in state.explored_trace_prefixes:
            continue

        state.forced_prefix = candidate
        state.scheduler.start_run(state.forced_prefix)
        print_prefix(state.forced_prefix, parse_step)
        return

    state.done = True