"""Central config for the crawler service, loaded from environment
variables. Defaults are deliberately conservative since this crawls the
live web -- see the README's safety note.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
POSTGRES_DSN = os.environ.get(
    "POSTGRES_DSN", "postgresql://crawler:crawler@localhost:5432/crawler"
)

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = _bool("MINIO_SECURE", False)
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "raw-pages")

MAX_DEPTH = int(os.environ.get("MAX_DEPTH", "2"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "200"))
DEFAULT_CRAWL_DELAY_SECONDS = float(os.environ.get("DEFAULT_CRAWL_DELAY_SECONDS", "3"))
ALLOW_OFFSITE_LINKS = _bool("ALLOW_OFFSITE_LINKS", False)

NUM_WORKER_TASKS = int(os.environ.get("NUM_WORKER_TASKS", "4"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15"))
VISIBILITY_TIMEOUT_MS = int(os.environ.get("VISIBILITY_TIMEOUT_MS", str(5 * 60 * 1000)))
CLAIM_BLOCK_MS = int(os.environ.get("CLAIM_BLOCK_MS", "5000"))
RATE_LIMIT_RETRY_SLEEP_SECONDS = float(os.environ.get("RATE_LIMIT_RETRY_SLEEP_SECONDS", "0.5"))

USER_AGENT = os.environ.get(
    "CRAWLER_USER_AGENT",
    "wayfind-search-bot/0.1 (+https://github.com/harshita-singh12/wayfind-search; portfolio project, contact via repo)",
)

BLOOM_SIZE_BITS = int(os.environ.get("BLOOM_SIZE_BITS", str(1 << 23)))
BLOOM_NUM_HASHES = int(os.environ.get("BLOOM_NUM_HASHES", "7"))

METRICS_PORT = int(os.environ.get("CRAWLER_METRICS_PORT", "9100"))

RECRAWL_INTERVAL_HOURS = float(os.environ.get("RECRAWL_INTERVAL_HOURS", "24"))

# Retries for genuine HTTP failures (404/500/timeout -- as opposed to a
# crashed worker, which the frontier's visibility-timeout/XAUTOCLAIM
# mechanism already handles independently of this). A page is retried with
# capped exponential backoff -- base_delay * 2^(retry_count-1), capped at
# max_delay -- up to MAX_RETRIES times before being permanently marked
# 'failed'.
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
RETRY_BASE_DELAY_SECONDS = float(os.environ.get("RETRY_BASE_DELAY_SECONDS", "5"))
RETRY_MAX_DELAY_SECONDS = float(os.environ.get("RETRY_MAX_DELAY_SECONDS", "300"))
RETRY_SCHEDULER_POLL_SECONDS = float(os.environ.get("RETRY_SCHEDULER_POLL_SECONDS", "2"))

STREAM_KEY = "frontier:urls"
CONSUMER_GROUP = "crawlers"
