from src.simcore.dpor import parse_step, minimal_prefix_for_swap, generate_forced_prefixes


def test_parse_step():
    s = "c1:Thread-1:PUT:/bucket-a"
    d = parse_step(s)
    assert d == {"client": "c1", "thread": "Thread-1", "method": "PUT", "path": "/bucket-a"}


def test_minimal_prefix_for_swap_includes_prereqs():
    trace = [
        "c:Thread-1:PUT:/bucket-a",   # prereq (Thread-1)
        "c:Thread-2:GET:/",           # i (Thread-2)
        "c:Thread-1:GET:/bucket-a",   # j (Thread-1)
    ]

    # correct: i < j
    prefix = minimal_prefix_for_swap(trace, i=1, j=2)

    # prerequisites for i: none (Thread-2 has no earlier events)
    # prerequisites for j: Thread-1 PUT at index 0
    # then swapped pair: trace[j] then trace[i]
    assert prefix == [
        trace[0],
        trace[2],
        trace[1],
    ]


def test_generate_forced_prefixes_simple_swap():
    trace = [
        "c:Thread-1:PUT:/bucket-a",
        "c:Thread-2:GET:/",
    ]
    seen = {tuple([])}
    explored = set()
    prefixes = generate_forced_prefixes(trace, seen, explored)

    assert len(prefixes) == 1
    assert prefixes[0][-2:] == [trace[1], trace[0]]
