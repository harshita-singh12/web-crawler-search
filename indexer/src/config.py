from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "postgresql://crawler:crawler@localhost:5432/crawler")

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").strip().lower() in ("1", "true", "yes", "on")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "raw-pages")

INDEX_BATCH_SIZE = int(os.environ.get("INDEX_BATCH_SIZE", "25"))
INDEX_POLL_INTERVAL_SECONDS = float(os.environ.get("INDEX_POLL_INTERVAL_SECONDS", "5"))

PAGERANK_INTERVAL_SECONDS = float(os.environ.get("PAGERANK_INTERVAL_SECONDS", "30"))
PAGERANK_DAMPING = float(os.environ.get("PAGERANK_DAMPING", "0.85"))
PAGERANK_ITERATIONS = int(os.environ.get("PAGERANK_ITERATIONS", "20"))

METRICS_PORT = int(os.environ.get("INDEXER_METRICS_PORT", "9101"))
