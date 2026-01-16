from typing import List, Dict, Set, Tuple

def parse_step(step: str) -> Dict[str, str]:
    client, thread, method, path = step.split(":", 3)
    return {"client": client, "thread": thread, "method": method, "path": path}


def minimal_prefix_for_swap(trace: List[str], i: int, j: int) -> List[str]:
    """
    Build a forced prefix that makes trace[j] happen BEFORE trace[i],
    while preserving per-thread program order prerequisites.

    - minimal prerequisites (same-thread earlier actions)
    - then force swapped pair (j before i)
    - suffix is not forced
    """
    assert 0 <= i < j < len(trace)

    ei = parse_step(trace[i])
    ej = parse_step(trace[j])
    ti = ei["thread"]
    tj = ej["thread"]

    prereq_idx = set()

    # prerequisites for event i (same thread, before i)
    for k in range(0, i):
        if parse_step(trace[k])["thread"] == ti:
            prereq_idx.add(k)

    # prerequisites for event j (same thread, before j)
    for k in range(0, j):
        if parse_step(trace[k])["thread"] == tj:
            prereq_idx.add(k)

    # we will re-order i and j explicitly at the end
    prereq_idx.discard(i)
    prereq_idx.discard(j)

    prefix = [trace[k] for k in sorted(prereq_idx)]
    prefix.append(trace[j])   # swap: j before i
    prefix.append(trace[i])
    return prefix


def generate_forced_prefixes(
    trace: List[str],
    seen_prefixes: Set[Tuple[str, ...]],
    explored_prefixes: Set[Tuple[str, ...]],
) -> List[List[str]]:
    """
    Requirement: if we observe two steps from different threads, consider both orders.
    Mechanism:
      - We already observed one order in the trace (i before j)
      - We generate a forced prefix that swaps them (j before i)
    """
    new_prefixes: List[List[str]] = []
    parsed = [parse_step(s) for s in trace]

    n = len(trace)
    for i in range(n):
        for j in range(i + 1, n):
            if parsed[i]["thread"] == parsed[j]["thread"]:
                continue  # same-thread order cannot be swapped

            prefix = minimal_prefix_for_swap(trace, i, j)
            tp = tuple(prefix)

            if tp in seen_prefixes or tp in explored_prefixes:
                continue

            seen_prefixes.add(tp)
            new_prefixes.append(prefix)

    return new_prefixes
