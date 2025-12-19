from typing import List, Dict
from collections import defaultdict

# -----------------------------
# Helpers
# -----------------------------


def split_by_thread(flow: List[str]) -> List[List[str]]:
    threads: Dict[str, List[str]] = defaultdict(list)
    for step in flow:
        _, thread, _, _ = step.split(":", 3)
        threads[thread].append(step)
    return list(threads.values())


def generate_interleavings(seqs: List[List[str]]):
    """Generate all valid interleavings of multiple thread flows."""
    if all(not s for s in seqs):
        yield []
        return

    for i, seq in enumerate(seqs):
        if seq:
            for rest in generate_interleavings(seqs[:i] + [seq[1:]] + seqs[i + 1 :]):
                yield [seq[0]] + rest


def dump_all_executions(flows: List[List[str]]):
    """Write all valid executions to a file."""
    with open("all_executions.txt", "w", encoding="utf-8") as f:
        for i, flow in enumerate(flows, 1):
            f.write(f"Execution {i}:\n")
            for j, step in enumerate(flow, 1):
                f.write(f"  {j}. {step}\n")
            f.write("\n")
