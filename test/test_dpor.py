from src.simcore.dpor import (
    parse_step,
    minimal_prefix_for_swap,
    generate_forced_prefixes,
    drop,
    delay,
)


def test_parse_step_normal():
    s = "c1:Thread-1:PUT:/bucket-a"
    d = parse_step(s)
    assert d == {
        "client": "c1",
        "thread": "Thread-1",
        "method": "PUT",
        "path": "/bucket-a",
        "is_drop": False,
        "is_delay": False,
        "delay_seconds": None,
    }


def test_parse_step_drop():
    s = drop("c1:Thread-1:PUT:/bucket-a")
    d = parse_step(s)
    assert d["client"] == "c1"
    assert d["thread"] == "Thread-1"
    assert d["method"] == "PUT"
    assert d["path"] == "/bucket-a"
    assert d["is_drop"] is True
    assert d["is_delay"] is False
    assert d["delay_seconds"] is None


def test_parse_step_delay():
    s = delay("c1:Thread-1:PUT:/bucket-a", 20)
    d = parse_step(s)
    assert d["client"] == "c1"
    assert d["thread"] == "Thread-1"
    assert d["method"] == "PUT"
    assert d["path"] == "/bucket-a"
    assert d["is_drop"] is False
    assert d["is_delay"] is True
    assert d["delay_seconds"] == 20


def test_minimal_prefix_for_swap_includes_prereqs():
    trace = [
        "c:Thread-1:PUT:/bucket-a",  # prereq (Thread-1)
        "c:Thread-2:GET:/",          # i (Thread-2)
        "c:Thread-1:GET:/bucket-a",  # j (Thread-1)
    ]

    prefix = minimal_prefix_for_swap(trace, i=1, j=2)

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

    assert len(prefixes) >= 1
    assert any(p[-2:] == [trace[1], trace[0]] for p in prefixes)


def test_generate_forced_prefixes_no_drop_when_disabled():
    trace = [
        "c:Thread-1:PUT:/bucket-a",
        "c:Thread-2:GET:/",
    ]
    seen = {tuple([])}
    explored = set()

    prefixes = generate_forced_prefixes(trace, seen, explored, enable_drop=False)

    assert any(p[-2:] == [trace[1], trace[0]] for p in prefixes)
    assert not any(any(step.startswith("DROP::") for step in p) for p in prefixes)


def test_generate_forced_prefixes_includes_drop_when_enabled():
    trace = [
        "c:Thread-1:PUT:/bucket-a",
        "c:Thread-2:GET:/",
    ]
    seen = {tuple([])}
    explored = set()

    prefixes = generate_forced_prefixes(trace, seen, explored, enable_drop=True)

    assert len(prefixes) >= 1
    assert any(any(step.startswith("DROP::") for step in p) for p in prefixes)

    drop_prefixes = [p for p in prefixes if any(step.startswith("DROP::") for step in p)]
    assert all(any(step.startswith("DROP::") for step in p) for p in drop_prefixes)


def test_generate_forced_prefixes_includes_delay_when_enabled():
    trace = [
        "c:Thread-1:PUT:/bucket-a",
        "c:Thread-2:GET:/",
    ]
    seen = {tuple([])}
    explored = set()

    prefixes = generate_forced_prefixes(
        trace,
        seen,
        explored,
        enable_delay=True,
        delay_s=30,
    )

    assert len(prefixes) >= 1
    assert any(any(step.startswith("DELAY::30::") for step in p) for p in prefixes)


def test_generate_forced_prefixes_no_delay_when_disabled():
    trace = [
        "c:Thread-1:PUT:/bucket-a",
        "c:Thread-2:GET:/",
    ]
    seen = {tuple([])}
    explored = set()

    prefixes = generate_forced_prefixes(
        trace,
        seen,
        explored,
        enable_delay=False,
    )

    assert not any(any(step.startswith("DELAY::") for step in p) for p in prefixes)


def test_generate_forced_prefixes_mixed_drop_and_delay_on_different_events_only():
    trace = [
        "c:Thread-1:PUT:/bucket-a",
        "c:Thread-2:GET:/",
    ]
    seen = {tuple([])}
    explored = set()

    prefixes = generate_forced_prefixes(
        trace,
        seen,
        explored,
        enable_drop=True,
        enable_delay=True,
        delay_s=20,
    )

    mixed = [
        p for p in prefixes
        if any(step.startswith("DROP::") for step in p)
        and any(step.startswith("DELAY::20::") for step in p)
    ]
    assert mixed, "Expected at least one mixed DROP+DELAY prefix"

    for p in mixed:
        # same event must not be both DROP and DELAY
        assert not any(step.startswith("DROP::DELAY::") for step in p)