from src.simcore.dpor import parse_step, minimal_prefix_for_swap, generate_forced_prefixes, drop


def test_parse_step_normal():
    s = "c1:Thread-1:PUT:/bucket-a"
    d = parse_step(s)
    assert d == {
        "client": "c1",
        "thread": "Thread-1",
        "method": "PUT",
        "path": "/bucket-a",
        "is_drop": False,
    }


def test_parse_step_drop():
    s = drop("c1:Thread-1:PUT:/bucket-a")
    d = parse_step(s)
    assert d["client"] == "c1"
    assert d["thread"] == "Thread-1"
    assert d["method"] == "PUT"
    assert d["path"] == "/bucket-a"
    assert d["is_drop"] is True


def test_minimal_prefix_for_swap_includes_prereqs():
    trace = [
        "c:Thread-1:PUT:/bucket-a",   # prereq (Thread-1)
        "c:Thread-2:GET:/",           # i (Thread-2)
        "c:Thread-1:GET:/bucket-a",   # j (Thread-1)
    ]

    prefix = minimal_prefix_for_swap(trace, i=1, j=2)

    assert prefix == [
        trace[0],   # prereq for Thread-1
        trace[2],   # swapped j
        trace[1],   # swapped i
    ]


def test_generate_forced_prefixes_simple_swap():
    trace = [
        "c:Thread-1:PUT:/bucket-a",
        "c:Thread-2:GET:/",
    ]
    seen = {tuple([])}
    explored = set()
    prefixes = generate_forced_prefixes(trace, seen, explored)

    assert len(prefixes) >= 1
    assert any(p[-2:] == [trace[1], trace[0]] for p in prefixes)
