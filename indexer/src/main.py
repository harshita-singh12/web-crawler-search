from __future__ import annotations

import asyncio
import logging
import time

from . import config, metrics
from .index_builder import IndexBuilder
from .storage import IndexStore, ObjectStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _wait_for_postgres(dsn: str, attempts: int = 30, delay: float = 2.0) -> IndexStore:
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return await IndexStore.connect(dsn)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.info("waiting for postgres... (%s)", exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"could not connect to postgres: {last_exc}")


def _wait_for_minio(attempts: int = 30, delay: float = 2.0) -> ObjectStore:
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


async def main() -> None:
    metrics.start_metrics_server(config.METRICS_PORT)
    logger.info("indexer metrics listening on :%d", config.METRICS_PORT)

    store = await _wait_for_postgres(config.POSTGRES_DSN)
    objects = _wait_for_minio()

    builder = IndexBuilder(store, objects)
    logger.info("indexer starting")
    await builder.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
