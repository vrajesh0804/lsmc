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
    DPOR-style minimal prefix that makes trace[j] occur before trace[i], while
    including the minimal same-thread prerequisites needed for feasibility.

    This helper ONLY preserves per-thread program order. It does NOT model
    data/control dependencies across threads (e.g., "delete happens only if head==200").
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
) -> List[List[str]]:
    """
    Generate only *feasible-by-construction* forced prefixes for your simulator.

    Key design goals (given "no client changes"):
      1) Avoid infeasible forced prefixes that can deadlock enforcement (CRASH),
         especially in workloads with control/data dependencies (e.g., C depends on B==True).
      2) Keep exploration deterministic: after forced prefix is satisfied, your scheduler's
         FREE mode completes the run deterministically.

    Strategy:
      (A) SWAPS: Only consider swaps of *adjacent* cross-thread events in the observed trace.
          - This is a conservative DPOR heuristic that avoids generating long-range swaps
            like moving C (delete) ahead of A when C is control-dependent on earlier results.
          - It also preserves per-thread order via minimal_prefix_for_swap.

      (B) DROP MUTATIONS: Generate DROP prefixes only up to the mutation point (length i+1),
          not full-trace "DROP schedules". This prevents forcing steps that may disappear
          due to branching (e.g., delete not executed when head returns 404/408).

    This eliminates prefixes like [B, C, A] for your conditional example, and therefore
    eliminates the forced-prefix timeouts (CRASH) caused by demanding an impossible step.
    """
    new_prefixes: List[List[str]] = []
    parsed = [parse_step(s) for s in trace]
    n = len(trace)

    # (A) Adjacent cross-thread swaps only (conservative, feasibility-oriented)
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

    # (B) DROP mutation prefixes only up to the dropped event
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

    return new_prefixes