from typing import List, Dict, Set, Tuple

DROP_PREFIX = "DROP::"


def is_drop(ev: str) -> bool:
    return ev.startswith(DROP_PREFIX)


def undrop(ev: str) -> str:
    return ev[len(DROP_PREFIX):] if is_drop(ev) else ev


def drop(ev: str) -> str:
    return ev if is_drop(ev) else (DROP_PREFIX + ev)


def parse_step(step: str) -> Dict[str, str]:
    """
    step format:
      normal:    client:thread:method:path
      dropped:   DROP::client:thread:method:path
    """
    raw = undrop(step)
    client, thread, method, path = raw.split(":", 3)
    return {
        "client": client,
        "thread": thread,
        "method": method,
        "path": path,
        "is_drop": is_drop(step),
    }


def minimal_prefix_for_swap(trace: List[str], i: int, j: int) -> List[str]:
    """
    DPOR-style swap: make trace[j] occur before trace[i] (minimal prereqs).
    Works even if some events are DROP::...

    NOTE: We keep this helper (and tests) because it's useful for reasoning,
    but the simulator uses *full-schedule* swap variants to avoid repeated runs
    caused by partially-forced prefixes.
    """
    assert 0 <= i < j < len(trace)

    ti = parse_step(trace[i])["thread"]
    tj = parse_step(trace[j])["thread"]

    prereq_idx = set()

    for k in range(0, i):
        if parse_step(trace[k])["thread"] == ti:
            prereq_idx.add(k)

    for k in range(0, j):
        if parse_step(trace[k])["thread"] == tj:
            prereq_idx.add(k)

    prereq_idx.discard(i)
    prereq_idx.discard(j)

    prefix = [trace[k] for k in sorted(prereq_idx)]
    prefix.append(trace[j])
    prefix.append(trace[i])
    return prefix


def full_schedule_for_swap(trace: List[str], i: int, j: int) -> List[str]:
    """
    Return a *full schedule* variant that forces trace[j] to occur before trace[i],
    while preserving the relative order of all other events as in `trace`.

    This helps ensure each forced prefix corresponds to exactly one deterministic run,
    preventing repeated executions that can happen with partially-forced prefixes.
    """
    assert 0 <= i < j < len(trace)
    t = list(trace)
    ev_j = t.pop(j)
    # insert j right before i (i still points to the original position)
    t.insert(i, ev_j)
    return t


def generate_forced_prefixes(
    trace: List[str],
    seen_prefixes: Set[Tuple[str, ...]],
    explored_prefixes: Set[Tuple[str, ...]],
) -> List[List[str]]:
    """
    Explore all deterministic interleavings, plus systematic DROP variants, without repeats.

    We generate:
      (A) full-schedule swap variants across different threads
      (B) full-schedule DROP mutation variants (turn one non-drop event into DROP::event)

    Because the simulator completes FREE choices deterministically, using full schedules
    (instead of partial/minimal prefixes) greatly reduces duplicate runs.
    """
    new_prefixes: List[List[str]] = []
    parsed = [parse_step(s) for s in trace]
    n = len(trace)

    # (A) full-schedule swaps across threads
    for i in range(n):
        for j in range(i + 1, n):
            if parsed[i]["thread"] == parsed[j]["thread"]:
                continue
            swapped = full_schedule_for_swap(trace, i, j)
            tp = tuple(swapped)
            if tp in seen_prefixes or tp in explored_prefixes:
                continue
            seen_prefixes.add(tp)
            new_prefixes.append(swapped)

    # (B) drop-mutations (full schedule variants)
    for i in range(n):
        if is_drop(trace[i]):
            continue
        mutated = list(trace)
        mutated[i] = drop(mutated[i])
        tp = tuple(mutated)
        if tp in seen_prefixes or tp in explored_prefixes:
            continue
        seen_prefixes.add(tp)
        new_prefixes.append(mutated)

    return new_prefixes