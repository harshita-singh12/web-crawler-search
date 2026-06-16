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

DEFAULT_RESULT_LIMIT = int(os.environ.get("DEFAULT_RESULT_LIMIT", "10"))
MAX_RESULT_LIMIT = int(os.environ.get("MAX_RESULT_LIMIT", "50"))
SNIPPET_CANDIDATE_COUNT = int(os.environ.get("SNIPPET_CANDIDATE_COUNT", "10"))

# How much PageRank nudges the final ranking on top of TF-IDF relevance.
# score = tfidf_score * (1 + alpha * pagerank). alpha=2.0 was chosen so a
# well-linked page can meaningfully outrank a marginally more keyword-dense
# but poorly-linked page, without letting link count alone dominate a
# mismatched query -- tfidf_score still gates relevance.
PAGERANK_BOOST_ALPHA = float(os.environ.get("PAGERANK_BOOST_ALPHA", "2.0"))

CORS_ALLOW_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()]

METRICS_PORT = int(os.environ.get("API_METRICS_PORT", "9102"))
