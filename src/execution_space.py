from typing import List, Dict
from collections import defaultdict


def split_by_thread(flow: List[str]) -> List[List[str]]:
    threads: Dict[str, List[str]] = defaultdict(list)
    for step in flow:
        _, thread, _, _ = step.split(":", 3)
        threads[thread].append(step)
    return list(threads.values())


def generate_interleavings(seqs: List[List[str]]):
    if all(not s for s in seqs):
        yield []
        return

    for i, seq in enumerate(seqs):
        if seq:
            for rest in generate_interleavings(
                seqs[:i] + [seq[1:]] + seqs[i + 1 :]
            ):
                yield [seq[0]] + rest
