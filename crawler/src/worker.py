"""The crawler worker: N concurrent asyncio tasks pulling from a shared
Frontier, each doing fetch -> parse -> store -> expand. See DESIGN.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time

import aiohttp
import redis.asyncio as aioredis

from . import config, metrics
from .bloom_backend import build_bloom_filter
from .fetcher import fetch_page
from .frontier import Frontier, FrontierItem
from .rate_limiter import DistributedRateLimiter
from .robots import RobotsChecker
from .seeds import SEED_URLS
from .storage import ObjectStore, PageStore, content_hash

from common.text_extract import extract
from common.urls import get_domain, normalize_url

logger = logging.getLogger(__name__)

SEEDED_FLAG_KEY = "frontier:seeded"
PAGE_COUNTER_KEY = "crawl:pages_fetched"


class CrawlerWorker:
    def __init__(
        self,
        redis_client: aioredis.Redis,
        page_store: PageStore,
        object_store: ObjectStore,
        session: aiohttp.ClientSession,
    ) -> None:
        self.redis = redis_client
        self.pages = page_store
        self.objects = object_store
        self.session = session
        self.robots = RobotsChecker(session, config.USER_AGENT, config.DEFAULT_CRAWL_DELAY_SECONDS)
        self.rate_limiter = DistributedRateLimiter(redis_client, config.DEFAULT_CRAWL_DELAY_SECONDS)
        self.bloom = build_bloom_filter(redis_client)
        consumer_name = f"worker-{socket.gethostname()}-{os.getpid()}"
        self.frontier = Frontier(
            redis_client,
            consumer_name,
            stream_key=config.STREAM_KEY,
            group=config.CONSUMER_GROUP,
            visibility_timeout_ms=config.VISIBILITY_TIMEOUT_MS,
        )
        self._stop = asyncio.Event()

    async def run(self) -> None:
        await self.frontier.ensure_group()
        await self._seed_if_needed()

        tasks = [asyncio.create_task(self._worker_loop(i)) for i in range(config.NUM_WORKER_TASKS)]
        tasks.append(asyncio.create_task(self._recrawl_scheduler_loop()))
        tasks.append(asyncio.create_task(self._metrics_sampler_loop()))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    def stop(self) -> None:
        self._stop.set()

    async def _seed_if_needed(self) -> None:
        acquired = await self.redis.set(SEEDED_FLAG_KEY, "1", nx=True)
        if not acquired:
            logger.info("seed already performed by another worker, skipping")
            return
        logger.info("seeding frontier with %d URLs", len(SEED_URLS))
        for url in SEED_URLS:
            normalized = normalize_url(url)
            if not normalized:
                continue
            await self._enqueue_url(normalized, depth=0, parent_id=None)

    async def _enqueue_url(self, url: str, depth: int, parent_id: int | None) -> None:
        domain = get_domain(url)
        if not domain:
            return
        is_new = await self.bloom.add_if_new(url)
        if not is_new:
            metrics.BLOOM_DUPLICATES.inc()
            return
        await self.pages.ensure_domain(domain, config.DEFAULT_CRAWL_DELAY_SECONDS)
        page_id, created = await self.pages.get_or_create_page(url, domain, depth)
        if not created:
            # Bloom filter false positive or a legitimate race with another
            # worker; the DB UNIQUE constraint is authoritative here.
            return
        await self.frontier.enqueue(url, domain, depth, page_id, parent_id)

    async def _worker_loop(self, task_id: int) -> None:
        while not self._stop.is_set():
            if await self._page_cap_reached():
                await asyncio.sleep(2.0)
                continue

            items = await self.frontier.claim(count=1)
            if not items:
                await asyncio.sleep(0.5)
                continue

            for item in items:
                try:
                    await self._process_item(item)
                except Exception:
                    logger.exception("unhandled error processing %s", item.url)
                    await self.pages.record_failure(item.page_id, None, "internal worker error")
                    await self.frontier.ack(item.entry_id)

    async def _page_cap_reached(self) -> bool:
        count = await self.redis.get(PAGE_COUNTER_KEY)
        return count is not None and int(count) >= config.MAX_PAGES

    async def _process_item(self, item: FrontierItem) -> None:
        domain = item.domain

        allowed = await self.robots.can_fetch(item.url, domain)
        if not allowed:
            metrics.ROBOTS_DISALLOWED.inc()
            await self.pages.record_skipped(item.page_id, "disallowed by robots.txt")
            await self.frontier.ack(item.entry_id)
            return

        delay = await self.robots.crawl_delay(domain)
        got_slot = await self.rate_limiter.try_acquire(domain, delay)
        if not got_slot:
            metrics.RATE_LIMITED_SKIPS.inc()
            await self.frontier.requeue(item)
            await asyncio.sleep(config.RATE_LIMIT_RETRY_SLEEP_SECONDS)
            return

        await self.pages.mark_in_progress(item.page_id)
        etag, last_modified = await self.pages.get_conditional_headers(item.url)

        start = time.monotonic()
        result = await fetch_page(
            self.session, item.url, config.USER_AGENT, config.REQUEST_TIMEOUT_SECONDS, etag, last_modified
        )
        metrics.FETCH_DURATION.observe(time.monotonic() - start)

        if result.not_modified:
            metrics.PAGES_FETCHED.labels(outcome="not_modified").inc()
            await self.pages.record_not_modified(item.page_id, config.RECRAWL_INTERVAL_HOURS)
            await self.frontier.ack(item.entry_id)
            return

        if result.status != 200 or result.html is None:
            outcome = "error" if result.status == 0 else f"http_{result.status}"
            metrics.PAGES_FETCHED.labels(outcome=outcome).inc()
            await self.pages.record_failure(item.page_id, result.status or None, result.error or "unknown error")
            await self.frontier.ack(item.entry_id)
            return

        extracted = extract(result.html, base_url=result.url)
        text_hash = content_hash(extracted.text)
        minio_key = await self.objects.put_html(item.page_id, result.html)

        await self.pages.record_success(
            item.page_id,
            http_status=result.status,
            title=extracted.title,
            text_hash=text_hash,
            etag=result.etag,
            last_modified=result.last_modified,
            content_length=result.content_length,
            minio_key=minio_key,
            recrawl_interval_hours=config.RECRAWL_INTERVAL_HOURS,
        )
        metrics.PAGES_FETCHED.labels(outcome="success").inc()
        await self.redis.incr(PAGE_COUNTER_KEY)

        if extracted.links:
            metrics.LINKS_DISCOVERED.inc(len(extracted.links))
            await self.pages.insert_links(item.page_id, [(link.url, link.anchor_text) for link in extracted.links])
            await self._expand(item, extracted.links)

        await self.frontier.ack(item.entry_id)

    async def _expand(self, item: FrontierItem, links) -> None:
        if item.depth >= config.MAX_DEPTH:
            return
        if await self._page_cap_reached():
            return
        for link in links:
            link_domain = get_domain(link.url)
            if not link_domain:
                continue
            if not config.ALLOW_OFFSITE_LINKS and link_domain != item.domain:
                continue
            await self._enqueue_url(link.url, item.depth + 1, item.page_id)

    async def _recrawl_scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                due = await self.pages.due_for_recrawl(limit=50)
                for row in due:
                    domain = row["domain"]
                    await self.frontier.enqueue(row["url"], domain, row["depth"], row["id"], None)
            except Exception:
                logger.exception("recrawl scheduler iteration failed")
            await asyncio.sleep(60)

    async def _metrics_sampler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                metrics.QUEUE_DEPTH.set(await self.frontier.stream_length())
                metrics.PENDING_COUNT.set(await self.frontier.pending_count())
                metrics.PAGES_CRAWLED_SESSION.set(await self.pages.fetched_page_count())
            except Exception:
                logger.exception("metrics sampler iteration failed")
            await asyncio.sleep(5)
