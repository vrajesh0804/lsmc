# src/simcore/simulator_state.py
import threading
from dataclasses import dataclass, field
from collections import deque, defaultdict
from typing import List, Tuple, Set, Dict

from src.simcore.scheduler import PrefixScheduler, RUN_SUCCESS

@dataclass
class SimState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    cond: threading.Condition = field(init=False)

    done: bool = False
    run_index: int = 0
    delay_active: bool = False

    current_result: Dict[str, str] = field(default_factory=lambda: {"value": RUN_SUCCESS})
    failure_reason: Dict[str, str] = field(default_factory=lambda: {"value": ""})

    trace: List[str] = field(default_factory=list)
    forced_prefix: List[str] = field(default_factory=list)

    pending_prefixes: deque = field(default_factory=deque)
    seen_prefixes: Set[Tuple[str, ...]] = field(default_factory=set)
    explored_prefixes: Set[Tuple[str, ...]] = field(default_factory=set)

    seen_traces: Set[Tuple[str, ...]] = field(default_factory=set)
    explored_trace_prefixes: Set[Tuple[str, ...]] = field(default_factory=set)

    results: List[Tuple[str, str]] = field(default_factory=list)

    # per-run step book-keeping
    seen_base_steps_this_run: Set[str] = field(default_factory=set)
    step_attempts_this_run: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # caches:
    response_cache_this_run: Dict[str, Tuple[int, bytes, Dict[str, str]]] = field(default_factory=dict)
    stable_response_cache: Dict[str, Tuple[int, bytes, Dict[str, str]]] = field(default_factory=dict)

    inflight_steps_this_run: Set[str] = field(default_factory=set)

    scheduler: PrefixScheduler = field(init=False)

    def __post_init__(self):
        self.cond = threading.Condition(self.lock)
        # scheduler expects cond + references
        self.scheduler = PrefixScheduler(
            cond=self.cond,
            deadlock_timeout_s=20,  # overwritten by init helper
            current_result_ref=self.current_result,
            failure_reason_ref=self.failure_reason,
        )

def init_state(deadlock_timeout_s: int) -> SimState:
    st = SimState()
    st.scheduler.deadlock_timeout_s = deadlock_timeout_s  # if your PrefixScheduler stores it like this
    return st