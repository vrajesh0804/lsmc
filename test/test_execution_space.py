from src.execution_space import split_by_thread, generate_interleavings


def test_split_by_thread():
    flow = [
        "c:Thread-1:PUT:/a",
        "c:Thread-2:GET:/b",
        "c:Thread-1:GET:/c",
    ]
    grouped = [tuple(g) for g in split_by_thread(flow)]
    assert ("c:Thread-1:PUT:/a", "c:Thread-1:GET:/c") in grouped
    assert ("c:Thread-2:GET:/b",) in grouped


def test_generate_interleavings_count():
    seqs = [["t1a", "t1b"], ["t2a", "t2b"]]
    outs = list(generate_interleavings(seqs))
    assert len(outs) == 6  # C(4,2)

    for o in outs:
        assert o.index("t1a") < o.index("t1b")
        assert o.index("t2a") < o.index("t2b")
