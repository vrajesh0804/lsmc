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


def generate_forced_prefixes(
    trace: List[str],
    seen_prefixes: Set[Tuple[str, ...]],
    explored_prefixes: Set[Tuple[str, ...]],
) -> List[List[str]]:
    """
    Prof requirement: DROP is an event, so explore interleavings that include DROP events.
    Also: while exploring, generate more drops (1-drop -> 2-drop -> ...).

    We do that by:
      (A) swap prefixes across threads
      (B) full-schedule DROP mutation: replace one *non-drop* event with DROP(event)
    """
    new_prefixes: List[List[str]] = []
    parsed = [parse_step(s) for s in trace]
    n = len(trace)

    # (A) swaps across threads
    for i in range(n):
        for j in range(i + 1, n):
            if parsed[i]["thread"] == parsed[j]["thread"]:
                continue
            prefix = minimal_prefix_for_swap(trace, i, j)
            tp = tuple(prefix)
            if tp in seen_prefixes or tp in explored_prefixes:
                continue
            seen_prefixes.add(tp)
            new_prefixes.append(prefix)

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
