# src/dpor.py
from typing import List, Dict, Tuple

def parse_step(step: str) -> Dict[str, str]:
    client, thread, method, path = step.split(":", 3)
    return {"client": client, "thread": thread, "method": method, "path": path}


def step_resource_id(info: Dict[str, str]) -> str:
    """
    Heuristic resource id:
      "/"                 -> list buckets
      "/bucket"           -> bucket
      "/bucket/key..."    -> "/bucket/key"
    """
    path = info["path"]
    if not path.startswith("/"):
        path = "/" + path
    parts = path.split("/")
    if len(parts) <= 2:
        return path
    return "/" + parts[1] + "/" + parts[2]


def likely_conflict(a: Dict[str, str], b: Dict[str, str]) -> bool:
    ra = step_resource_id(a)
    rb = step_resource_id(b)
    if ra == rb:
        return True

    # "/bucket" conflicts with "/bucket/key"
    if ra.count("/") == 2 and rb.startswith(ra + "/"):
        return True
    if rb.count("/") == 2 and ra.startswith(rb + "/"):
        return True

    # "/" weak conflict with everything
    if ra == "/" or rb == "/":
        return True

    return False


def prerequisites_for_thread(trace: List[str], upto_index: int, thread_name: str) -> List[int]:
    idxs = []
    for k in range(0, upto_index):
        if parse_step(trace[k])["thread"] == thread_name:
            idxs.append(k)
    return idxs


def minimal_prefix_for_swap(trace: List[str], i: int, j: int) -> List[str]:
    """
    Minimal enabling prefix using program-order prerequisites only,
    then force event j before i (swap order).
    """
    ei = parse_step(trace[i])
    ej = parse_step(trace[j])
    ti, tj = ei["thread"], ej["thread"]

    prereq = set(prerequisites_for_thread(trace, i, ti) + prerequisites_for_thread(trace, j, tj))
    prereq.discard(i)
    prereq.discard(j)

    prefix = [trace[k] for k in sorted(prereq)]
    prefix.append(trace[j])
    prefix.append(trace[i])
    return prefix


def generate_forced_prefixes(trace: List[str], seen: set, explored: set) -> List[List[str]]:
    """
    Returns new forced prefixes derived from trace, deduped by (seen/explored).
    """
    parsed = [parse_step(s) for s in trace]
    new_prefixes: List[List[str]] = []
    n = len(trace)

    for i in range(n):
        for j in range(i + 1, n):
            if parsed[i]["thread"] == parsed[j]["thread"]:
                continue

            if not likely_conflict(parsed[i], parsed[j]):
                continue

            prefix = minimal_prefix_for_swap(trace, i, j)
            tp = tuple(prefix)
            if tp in seen or tp in explored:
                continue
            seen.add(tp)
            new_prefixes.append(prefix)

    return new_prefixes
