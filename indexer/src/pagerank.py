"""Pure PageRank computation via power iteration over the link graph. No I/O
-- takes an adjacency mapping and returns scores, so it's directly unit
testable without a database.
"""
from __future__ import annotations


def compute_pagerank(
    edges: dict[int, list[int]],
    damping: float = 0.85,
    iterations: int = 20,
) -> dict[int, float]:
    """Standard power-iteration PageRank with uniform teleportation and
    dangling-node mass redistributed evenly across all nodes (the common fix
    for nodes with no outlinks, otherwise rank "leaks" out of the graph).

    `edges`: node id -> list of node ids it links to. Every node that
    appears anywhere (as a source or a destination) must have a key in the
    returned dict, even if it has no outlinks -- callers should pre-populate
    `edges` with an empty list for such nodes.
    """
    nodes = list(edges.keys())
    n = len(nodes)
    if n == 0:
        return {}

    scores = {node: 1.0 / n for node in nodes}
    base = (1.0 - damping) / n

    for _ in range(iterations):
        dangling_mass = sum(scores[node] for node in nodes if not edges.get(node))
        new_scores = {node: base + damping * dangling_mass / n for node in nodes}

        for src in nodes:
            outlinks = edges.get(src) or []
            if not outlinks:
                continue
            share = damping * scores[src] / len(outlinks)
            for dst in outlinks:
                if dst in new_scores:
                    new_scores[dst] += share

        scores = new_scores

    return scores
