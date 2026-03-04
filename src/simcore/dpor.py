from typing import List, Dict, Set, Tuple

DROP_PREFIX = "DROP::"

# Delay is encoded as: DELAY::<seconds>::client:thread:method:path
DELAY_PREFIX = "DELAY::"


def is_drop(ev: str) -> bool:
    return ev.startswith(DROP_PREFIX)


def undrop(ev: str) -> str:
    return ev[len(DROP_PREFIX):] if is_drop(ev) else ev


def drop(ev: str) -> str:
    return ev if is_drop(ev) else (DROP_PREFIX + ev)


def is_delay(ev: str) -> bool:
    return ev.startswith(DELAY_PREFIX)


def delay_seconds(ev: str) -> int | None:
    """
    If ev is DELAY::<seconds>::..., returns seconds, else None.
    """
    if not is_delay(ev):
        return None
    # format: DELAY::<sec>::<rest>
    rest = ev[len(DELAY_PREFIX):]
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
    """
    if not is_delay(ev):
        return ev
    rest = ev[len(DELAY_PREFIX):]
    parts = rest.split("::", 1)
    if len(parts) != 2:
        # malformed; best-effort fallback
        return ev
    return parts[1]


def delay(ev: str, seconds: int) -> str:
    """
    Wrap event as delayed. If already delayed, keep as-is.
    """
    return ev if is_delay(ev) else f"{DELAY_PREFIX}{int(seconds)}::{ev}"


def parse_step(step: str) -> Dict[str, object]:
    """
    step format:
      normal:            client:thread:method:path
      dropped:           DROP::client:thread:method:path
      delayed:           DELAY::<sec>::client:thread:method:path
      dropped+delayed:   DROP::DELAY::<sec>::client:thread:method:path   (supported)
    """
    raw = step

    # support nesting in either order
    is_d = is_drop(raw)
    if is_d:
        raw = undrop(raw)

    is_l = is_delay(raw)
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
    delay_s: int = 60,
) -> List[List[str]]:
    """
    Generate forced prefixes.

    Strategy:
      (A) SWAPS: adjacent cross-thread swaps only (conservative DPOR heuristic)
      (B) DROP mutations: prefix up to i+1, with event i replaced by DROP::event (if enabled)
      (C) DELAY mutations: prefix up to i+1, with event i replaced by DELAY::<s>::event (if enabled)

    DELAY is implemented "exactly like DROP" (same prefix shape), but simulator behavior differs:
      - DROP => returns 408 immediately
      - DELAY => sleeps then forwards, likely causing client-side timeouts
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

    # (B) DROP mutation prefixes
    if enable_drop:
        for i in range(n):
            if is_drop(trace[i]):
                continue
            drop_prefix = list(trace[: i + 1])
            drop_prefix[i] = drop(drop_prefix[i])

            tp = tuple(drop_prefix)
            if tp in seen_prefixes or tp in explored_prefixes:
                continue
            seen_prefixes.add(tp)
            new_prefixes.append(drop_prefix)

    # (C) DELAY mutation prefixes (same construction as DROP)
    if enable_delay:
        for i in range(n):
            if is_delay(trace[i]):
                continue
            delay_prefix = list(trace[: i + 1])
            delay_prefix[i] = delay(delay_prefix[i], delay_s)

            tp = tuple(delay_prefix)
            if tp in seen_prefixes or tp in explored_prefixes:
                continue
            seen_prefixes.add(tp)
            new_prefixes.append(delay_prefix)

    return new_prefixes