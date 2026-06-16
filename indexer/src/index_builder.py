"""The indexer service: a separate process from the crawler that turns
crawled+stored HTML into the inverted index (terms/postings tables) and
periodically recomputes PageRank.
"""
from __future__ import annotations

import asyncio
import logging
import time

from common.text_extract import extract_text_and_title
from common.tokenizer import tokenize
from common.tfidf import term_frequencies

from . import config, metrics
from .pagerank import compute_pagerank
from .storage import IndexStore, ObjectStore

logger = logging.getLogger(__name__)


class IndexBuilder:
    def __init__(self, store: IndexStore, objects: ObjectStore) -> None:
        self.store = store
        self.objects = objects
        self._last_pagerank_run = 0.0

    async def index_batch(self) -> int:
        pages = await self.store.pages_needing_index(config.INDEX_BATCH_SIZE)
        if not pages:
            return 0

        start = time.monotonic()
        for page in pages:
            try:
                html = await self.objects.get_html(page["minio_key"])
                _, text = extract_text_and_title(html)
                tokens = tokenize(text)
                term_tf = term_frequencies(tokens)
                await self.store.reindex_page(page["id"], term_tf, page["content_hash"])
                metrics.PAGES_INDEXED.inc()
            except Exception:
                logger.exception("failed to index page id=%s url=%s", page.get("id"), page.get("url"))

        metrics.INDEX_BATCH_DURATION.set(time.monotonic() - start)
        total = await self.store.refresh_index_stats()
        metrics.INDEX_TOTAL_DOCS.set(total)
        return len(pages)

    async def run_pagerank(self) -> None:
        start = time.monotonic()
        edges = await self.store.load_link_graph()
        if not edges:
            return
        scores = compute_pagerank(edges, damping=config.PAGERANK_DAMPING, iterations=config.PAGERANK_ITERATIONS)
        await self.store.update_pagerank(scores)
        metrics.PAGERANK_RUNS.inc()
        metrics.PAGERANK_DURATION.set(time.monotonic() - start)
        logger.info("pagerank recomputed for %d pages in %.2fs", len(scores), time.monotonic() - start)

    async def run_forever(self) -> None:
        while True:
            try:
                indexed = await self.index_batch()
            except Exception:
                logger.exception("indexing batch failed")
                indexed = 0

            now = time.monotonic()
            if now - self._last_pagerank_run >= config.PAGERANK_INTERVAL_SECONDS:
                try:
                    await self.run_pagerank()
                except Exception:
                    logger.exception("pagerank run failed")
                self._last_pagerank_run = now

            if indexed == 0:
                await asyncio.sleep(config.INDEX_POLL_INTERVAL_SECONDS)
