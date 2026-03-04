# src/simcore/dpor.py
from __future__ import annotations

from typing import List, Dict, Set, Tuple

DROP_PREFIX = "DROP::"

# Delay is encoded as: DELAY::<seconds>::client:thread:method:path
DELAY_PREFIX = "DELAY::"


def is_drop(ev: str) -> bool:
    return ev.startswith(DROP_PREFIX)


def undrop(ev: str) -> str:
    return ev[len(DROP_PREFIX) :] if is_drop(ev) else ev


def drop(ev: str) -> str:
    return ev if is_drop(ev) else (DROP_PREFIX + ev)


def is_delay(ev: str) -> bool:
    # Supports both "DELAY::..." and "DROP::DELAY::..." (nested)
    if ev.startswith(DELAY_PREFIX):
        return True
    if ev.startswith(DROP_PREFIX):
        inner = undrop(ev)
        return inner.startswith(DELAY_PREFIX)
    return False


def delay_seconds(ev: str) -> int | None:
    """
    If ev is DELAY::<seconds>::..., returns seconds, else None.
    Supports nested DROP::DELAY::<seconds>::...
    """
    raw = ev
    if raw.startswith(DROP_PREFIX):
        raw = undrop(raw)

    if not raw.startswith(DELAY_PREFIX):
        return None

    # format: DELAY::<sec>::<rest>
    rest = raw[len(DELAY_PREFIX) :]
    parts = rest.split("::", 1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0])
    except Exception:
        return None


def undelay(ev: str) -> str:
    """
    DELAY::<seconds>::client:thread:method:path  ->  client:thread:method:path
    Supports nested DROP::DELAY::<seconds>::...
    """
    raw = ev
    outer_drop = False
    if raw.startswith(DROP_PREFIX):
        outer_drop = True
        raw = undrop(raw)

    if not raw.startswith(DELAY_PREFIX):
        # If it was DROP::X and X wasn't DELAY, return original ev
        return ev if outer_drop else raw

    rest = raw[len(DELAY_PREFIX) :]
    parts = rest.split("::", 1)
    if len(parts) != 2:
        # malformed; best-effort fallback
        return ev if outer_drop else raw
    return parts[1]


def delay(ev: str, seconds: int) -> str:
    """
    Wrap event as delayed. If already delayed (possibly nested), keep as-is.
    """
    return ev if is_delay(ev) else f"{DELAY_PREFIX}{int(seconds)}::{ev}"


def parse_step(step: str) -> Dict[str, object]:
    """
    step format:
      normal:            client:thread:method:path
      dropped:           DROP::client:thread:method:path
      delayed:           DELAY::<sec>::client:thread:method:path
      dropped+delayed:   DROP::DELAY::<sec>::client:thread:method:path   (supported)

    NOTE:
      - We support nesting in either order logically, but your generator below
        intentionally avoids creating DROP+DELAY on the same event.
    """
    raw = step

    # support nesting in either order
    is_d = raw.startswith(DROP_PREFIX)
    if is_d:
        raw = undrop(raw)

    is_l = raw.startswith(DELAY_PREFIX)
    sec = delay_seconds(raw) if is_l else None
    if is_l:
        raw = undelay(raw)

    client, thread, method, path = raw.split(":", 3)
    return {
        "client": client,
        "thread": thread,
        "method": method,
        "path": path,
        "is_drop": is_d,
        "is_delay": is_l,
        "delay_seconds": sec,
    }


def minimal_prefix_for_swap(trace: List[str], i: int, j: int) -> List[str]:
    """
    DPOR-style minimal prefix that makes trace[j] occur before trace[i], while
    including the minimal same-thread prerequisites needed for feasibility.

    This helper ONLY preserves per-thread program order. It does NOT model
    data/control dependencies across threads.
    """
    assert 0 <= i < j < len(trace)

    ti = parse_step(trace[i])["thread"]
    tj = parse_step(trace[j])["thread"]

    prereq_idx = set()

    # prerequisites in ti that must occur before i
    for k in range(0, i):
        if parse_step(trace[k])["thread"] == ti:
            prereq_idx.add(k)

    # prerequisites in tj that must occur before j
    for k in range(0, j):
        if parse_step(trace[k])["thread"] == tj:
            prereq_idx.add(k)

    # we will explicitly place j then i at the end
    prereq_idx.discard(i)
    prereq_idx.discard(j)

    prefix = [trace[k] for k in sorted(prereq_idx)]
    prefix.append(trace[j])
    prefix.append(trace[i])
    return prefix


def generate_forced_prefixes(
    trace: List[str],
    seen_prefixes: Set[Tuple[str, ...]],
    explored_prefixes: Set[Tuple[str, ...]],
    *,
    enable_drop: bool = False,
    enable_delay: bool = False,
    delay_s: int = 20,
) -> List[List[str]]:
    """
    Generate forced prefixes.

    Strategy:
      (A) SWAPS: adjacent cross-thread swaps only (conservative DPOR heuristic)
      (B) DROP mutations: prefix up to i+1, with event i replaced by DROP::event (if enabled)
      (C) DELAY mutations: prefix up to i+1, with event i replaced by DELAY::<s>::event (if enabled)
      (D) MIXED DROP+DELAY: choose i != j, build prefix up to max(i,j)+1,
          apply DROP to one event and DELAY to the other (if both enabled)

    IMPORTANT CONSTRAINT (your requirement):
      - The same event cannot be both DROP and DELAY in the same execution.
        Therefore, we DO NOT generate DROP::DELAY::X (or DELAY wrapped in DROP) for the same step.
    """
    new_prefixes: List[List[str]] = []
    parsed = [parse_step(s) for s in trace]
    n = len(trace)

    # (A) Adjacent cross-thread swaps only
    for i in range(n - 1):
        j = i + 1
        if parsed[i]["thread"] == parsed[j]["thread"]:
            continue

        swapped_prefix = minimal_prefix_for_swap(trace, i, j)
        tp = tuple(swapped_prefix)
        if tp in seen_prefixes or tp in explored_prefixes:
            continue
        seen_prefixes.add(tp)
        new_prefixes.append(swapped_prefix)

    # Helpers: for *single-mutation* generation, don't mutate a step that is already mutated
    def _is_mutated(step: str) -> bool:
        return is_drop(step) or is_delay(step)

    # (B) DROP mutation prefixes (single-mutation)
    if enable_drop:
        for i in range(n):
            if _is_mutated(trace[i]):
                continue
            p = list(trace[: i + 1])
            p[i] = drop(p[i])

            tp = tuple(p)
            if tp in seen_prefixes or tp in explored_prefixes:
                continue
            seen_prefixes.add(tp)
            new_prefixes.append(p)

    # (C) DELAY mutation prefixes (single-mutation)
    if enable_delay:
        for i in range(n):
            if _is_mutated(trace[i]):
                continue
            p = list(trace[: i + 1])
            p[i] = delay(p[i], delay_s)

            tp = tuple(p)
            if tp in seen_prefixes or tp in explored_prefixes:
                continue
            seen_prefixes.add(tp)
            new_prefixes.append(p)

    # (D) MIXED DROP+DELAY on two DIFFERENT events
    if enable_drop and enable_delay:
        # i = index to DROP, j = index to DELAY (i != j)
        for i in range(n):
            if _is_mutated(trace[i]):
                continue
            for j in range(n):
                if j == i:
                    continue
                if _is_mutated(trace[j]):
                    continue

                k = max(i, j)
                p = list(trace[: k + 1])

                # apply mutations to different indices
                p[i] = drop(p[i])
                p[j] = delay(p[j], delay_s)

                tp = tuple(p)
                if tp in seen_prefixes or tp in explored_prefixes:
                    continue
                seen_prefixes.add(tp)
                new_prefixes.append(p)

    return new_prefixes