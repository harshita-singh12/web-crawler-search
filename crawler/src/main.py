"""Crawler service entrypoint. Run with `python -m src.main` from
/app inside the crawler container (see crawler/Dockerfile).
"""
from __future__ import annotations

import asyncio
import logging
import signal

import aiohttp
import redis.asyncio as aioredis

from . import config, metrics
from .ssrf_guard import SSRFSafeResolver
from .storage import ObjectStore, PageStore
from .worker import CrawlerWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _wait_for_postgres(dsn: str, attempts: int = 30, delay: float = 2.0) -> PageStore:
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return await PageStore.connect(dsn)
        except Exception as exc:  # noqa: BLE001 - retry loop, log and retry
            last_exc = exc
            logger.info("waiting for postgres... (%s)", exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"could not connect to postgres: {last_exc}")


def _wait_for_minio(attempts: int = 30, delay: float = 2.0) -> ObjectStore:
    last_exc: Exception | None = None
    import time

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


async def main() -> None:
    metrics.start_metrics_server(config.METRICS_PORT)
    logger.info("crawler metrics listening on :%d", config.METRICS_PORT)

    redis_client = aioredis.from_url(config.REDIS_URL, decode_responses=False)
    page_store = await _wait_for_postgres(config.POSTGRES_DSN)
    object_store = _wait_for_minio()

    connector = aiohttp.TCPConnector(
        limit_per_host=2, limit=config.NUM_WORKER_TASKS * 2, resolver=SSRFSafeResolver()
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        worker = CrawlerWorker(redis_client, page_store, object_store, session)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, worker.stop)
            except NotImplementedError:
                pass  # signal handlers aren't available on all platforms

        logger.info("crawler worker starting (%d concurrent tasks)", config.NUM_WORKER_TASKS)
        await worker.run()

    await page_store.close()
    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
