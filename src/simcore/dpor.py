from __future__ import annotations

from typing import List, Dict, Set, Tuple

# mutation markers
DROP_PREFIX = "DROP::"
DELAY_PREFIX = "DELAY::"


def is_drop(ev: str) -> bool:
    # helper functions for DROP/DELAY
    return ev.startswith(DROP_PREFIX)

def undrop(ev: str) -> str:
    # Removes the DROP:: prefix if present.
    return ev[len(DROP_PREFIX) :] if is_drop(ev) else ev

def drop(ev: str) -> str:
    # Adds DROP:: only if it is not already dropped.
    return ev if is_drop(ev) else (DROP_PREFIX + ev)

def is_delay(ev: str) -> bool:
    # Checks whether a step is delayed.
    if ev.startswith(DELAY_PREFIX):
        return True
    if ev.startswith(DROP_PREFIX):
        inner = undrop(ev)
        return inner.startswith(DELAY_PREFIX)
    return False

def delay_seconds(ev: str) -> int | None:
    # Extracts the numeric delay from a delayed event.
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
    # Removes the delay wrapper and returns the original event.
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
    # Wraps a step with delay metadata.
    return ev if is_delay(ev) else f"{DELAY_PREFIX}{int(seconds)}::{ev}"

# parsing one step
def parse_step(step: str) -> Dict[str, object]:
    """
        Converts one step string into structured information.
        For example: "A:T1:PUT:/bucket"
        becomes something like:
        {
            "client": "A",
            "thread": "T1",
            "method": "PUT",
            "path": "/bucket",
            "is_drop": False,
            "is_delay": False,
            "delay_seconds": None,
        }
    """
    raw = step
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

# swap helper
def minimal_prefix_for_swap(trace: List[str], i: int, j: int) -> List[str]:
    # Creates the shortest prefix needed to represent a swap of two steps at positions i and j.
    # This supports schedule exploration by creating swapped versions of neighboring events.
    """
        Example
        [
          "A:T1:x",
          "B:T2:y",
          "A:T1:z"
        ]
        and swap i=0, j=1, then:
        take prefix up to j
            ["A:T1:x", "B:T2:y"]
        swap them
            ["B:T2:y", "A:T1:x"]
    """
    assert 0 <= i < j < len(trace)

    prefix = list(trace[: j + 1])
    prefix[i], prefix[j] = prefix[j], prefix[i]
    return prefix

# main function
def generate_forced_prefixes(
    trace: List[str],
    seen_prefixes: Set[Tuple[str, ...]],
    explored_prefixes: Set[Tuple[str, ...]],
    *,
    enable_drop: bool = False,
    enable_delay: bool = False,
    delay_s: int = 20,
) -> List[List[str]]:
    # 
    """
        trace: A full observed execution trace.
        seen_prefixes: Prefixes already generated in memory before. Prevents duplicates.
        explored_prefixes: Prefixes already actually explored/executed before. Prevents retrying old work.
        enable_drop: Whether to generate dropped-event mutations.
        enable_delay: Whether to generate delayed-event mutations.
        delay_s: How many seconds to use when creating delayed steps.


        Avoids duplicates by checking:
            seen_prefixes: already generated before
            explored_prefixes: already executed before

    """
    new_prefixes: List[List[str]] = [] # collects results
    parsed = [parse_step(s) for s in trace] # gives structured info for each step
    n = len(trace) # trace length

    # (A) Adjacent cross-thread swaps only
    for i in range(n - 1):
        j = i + 1
        if parsed[i]["thread"] == parsed[j]["thread"]:
            continue
        # create swapped prefix; skip if already known; otherwise store it
        # Explore alternative thread interleavings.
        swapped_prefix = minimal_prefix_for_swap(trace, i, j)
        tp = tuple(swapped_prefix)
        if tp in seen_prefixes or tp in explored_prefixes:
            continue
        seen_prefixes.add(tp)
        new_prefixes.append(swapped_prefix)

    # Helpers: for *single-mutation* generation, don't mutate a step that is already mutated
    def _is_mutated(step: str) -> bool:
        # Checks whether a step already has some mutation.
        return is_drop(step) or is_delay(step)

    # (B) DROP mutation prefixes (single-mutation)
    if enable_drop:
        """
            take prefix up to that step
            mark that step as dropped
        """
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
        """
            take prefix up to that step
            mark that step as delayed
        """
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
        """
            one event is dropped
            another different event is delayed
        """
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