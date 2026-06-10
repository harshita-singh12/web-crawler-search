from __future__ import annotations

from prometheus_client import Counter, Gauge, start_http_server

PAGES_INDEXED = Counter("indexer_pages_indexed_total", "Pages (re)indexed")
INDEX_TOTAL_DOCS = Gauge("indexer_total_docs", "Total documents currently in the index")
INDEX_TOTAL_TERMS = Gauge("indexer_total_terms", "Total distinct terms currently in the index")
PAGERANK_RUNS = Counter("indexer_pagerank_runs_total", "Number of PageRank recomputations performed")
PAGERANK_DURATION = Gauge("indexer_pagerank_duration_seconds", "Duration of the last PageRank recomputation")
INDEX_BATCH_DURATION = Gauge("indexer_index_batch_duration_seconds", "Duration of the last indexing batch")


def start_metrics_server(port: int) -> None:
    start_http_server(port)
