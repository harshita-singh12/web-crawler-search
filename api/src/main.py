from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from . import config, db
from .object_store import ObjectStore
from .search import search as run_search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

SEARCH_REQUESTS = Counter("api_search_requests_total", "Search requests, by outcome", ["outcome"])
SEARCH_DURATION = Histogram("api_search_duration_seconds", "Search request latency")

state: dict = {}


async def _wait_for_postgres(dsn: str, attempts: int = 30, delay: float = 2.0):
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return await db.create_pool(dsn)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.info("waiting for postgres... (%s)", exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"could not connect to postgres: {last_exc}")


def _wait_for_minio(attempts: int = 30, delay: float = 2.0) -> ObjectStore:
    import time

    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return ObjectStore.connect(
                config.MINIO_ENDPOINT,
                config.MINIO_ACCESS_KEY,
                config.MINIO_SECRET_KEY,
                config.MINIO_SECURE,
                config.MINIO_BUCKET,
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.info("waiting for minio... (%s)", exc)
            time.sleep(delay)
    raise RuntimeError(f"could not connect to minio: {last_exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["pool"] = await _wait_for_postgres(config.POSTGRES_DSN)
    state["objects"] = _wait_for_minio()
    logger.info("api service ready")
    yield
    await state["pool"].close()


app = FastAPI(title="Wayfind Search API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics_endpoint():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/stats")
async def stats():
    data = await db.get_stats(state["pool"])
    return data


@app.get("/search")
async def search_endpoint(
    q: str = Query(..., max_length=500),
    limit: int = Query(config.DEFAULT_RESULT_LIMIT, ge=1, le=config.MAX_RESULT_LIMIT),
):
    if not q.strip():
        SEARCH_REQUESTS.labels(outcome="bad_request").inc()
        raise HTTPException(status_code=400, detail="query must not be empty")

    with SEARCH_DURATION.time():
        response = await run_search(state["pool"], state["objects"], q, limit)

    SEARCH_REQUESTS.labels(outcome="ok").inc()
    return {
        "query": response.query,
        "took_ms": response.took_ms,
        "total_matches": response.total_matches,
        "results": [
            {
                "url": r.url,
                "title": r.title,
                "snippet": r.snippet,
                "score": r.score,
                "domain": r.domain,
                "pagerank": r.pagerank,
            }
            for r in response.results
        ],
    }
