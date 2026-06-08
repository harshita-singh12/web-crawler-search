"""Prometheus metrics for the crawler service. Scraped by Prometheus per
monitoring/prometheus/prometheus.yml and visualized in the provisioned
Grafana dashboard.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

PAGES_FETCHED = Counter(
    "crawler_pages_fetched_total", "Pages fetched, by outcome", ["outcome"]
)
FETCH_DURATION = Histogram(
    "crawler_fetch_duration_seconds", "Time to fetch a single page", buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30)
)
QUEUE_DEPTH = Gauge("crawler_frontier_queue_depth", "Approximate number of entries in the frontier stream")
PENDING_COUNT = Gauge("crawler_frontier_pending_count", "Entries claimed but not yet acknowledged")
ROBOTS_DISALLOWED = Counter("crawler_robots_disallowed_total", "URLs skipped due to robots.txt")
RATE_LIMITED_SKIPS = Counter("crawler_rate_limited_skips_total", "Claims deferred due to per-domain rate limiting")
BLOOM_DUPLICATES = Counter("crawler_bloom_duplicate_total", "URLs skipped as probable duplicates by the Bloom filter")
LINKS_DISCOVERED = Counter("crawler_links_discovered_total", "Outgoing links discovered across all crawled pages")
PAGES_CRAWLED_SESSION = Gauge("crawler_pages_crawled_session", "Pages successfully crawled in the current run")


def start_metrics_server(port: int) -> None:
    start_http_server(port)
