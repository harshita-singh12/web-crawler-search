import pytest

from src.pagerank import compute_pagerank


def test_empty_graph_returns_empty():
    assert compute_pagerank({}) == {}


def test_scores_sum_to_approximately_one():
    edges = {1: [2], 2: [3], 3: [1]}
    scores = compute_pagerank(edges, iterations=30)
    assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)


def test_symmetric_cycle_gives_equal_scores():
    edges = {1: [2], 2: [3], 3: [1]}
    scores = compute_pagerank(edges, iterations=50)
    values = list(scores.values())
    assert values[0] == pytest.approx(values[1], abs=1e-6)
    assert values[1] == pytest.approx(values[2], abs=1e-6)


def test_more_incoming_links_means_higher_rank():
    # Nodes 2, 3, 4 all link to node 1; node 1 links nowhere in particular
    # back except to 2. Node 1 should end up with the highest rank.
    edges = {1: [2], 2: [1], 3: [1], 4: [1]}
    scores = compute_pagerank(edges, iterations=50)
    assert scores[1] > scores[2]
    assert scores[1] > scores[3]
    assert scores[1] > scores[4]


def test_dangling_node_does_not_leak_rank():
    # Node 2 has no outlinks (dangling). Total rank mass must still sum to 1.
    edges = {1: [2], 2: []}
    scores = compute_pagerank(edges, iterations=50)
    assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)


def test_isolated_node_gets_baseline_rank_from_teleportation():
    edges = {1: [2], 2: [1], 3: []}
    scores = compute_pagerank(edges, damping=0.85, iterations=50)
    # Node 3 has no incoming links and is itself dangling (no outlinks), so
    # at the fixed point its score is entirely self-sustained by
    # teleportation plus its own redistributed dangling mass:
    #   s3 = base + damping * s3 / n  =>  s3 = base / (1 - damping/n)
    n = 3
    damping = 0.85
    base = (1 - damping) / n
    expected = base / (1 - damping / n)
    assert scores[3] == pytest.approx(expected, abs=1e-6)
    # Sanity check: it should still be small, on the order of 1/n, and much
    # smaller than nodes 1 and 2 which receive real incoming links.
    assert scores[3] < scores[1]
    assert scores[3] < scores[2]


def test_more_iterations_converges_rather_than_diverges():
    edges = {1: [2, 3], 2: [3], 3: [1]}
    short = compute_pagerank(edges, iterations=5)
    long = compute_pagerank(edges, iterations=100)
    for node in edges:
        assert short[node] == pytest.approx(long[node], abs=0.05)


def test_single_node_no_links():
    edges = {1: []}
    scores = compute_pagerank(edges, iterations=10)
    assert scores[1] == pytest.approx(1.0, abs=1e-6)
