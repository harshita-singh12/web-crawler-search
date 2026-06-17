"""Tests for retry-with-backoff on genuine HTTP failures (404/500/timeout --
as opposed to a crashed worker, which is a separate mechanism: the frontier's
visibility-timeout/XAUTOCLAIM reclaim path, already covered by
test_frontier.py).

Two layers, matching this project's existing testing philosophy (pure logic
gets a fast in-process unit test; anything that would otherwise need real
infra gets a fake that implements real semantics, not a rewritten copy of the
logic under test):

  1. `compute_retry_outcome` is a pure function (no I/O), so it's tested
     directly with plain unit tests.
  2. The end-to-end "does the crawler actually retry a flaky URL and
     eventually mark a permanently-broken one 'failed'" behavior is tested by
     driving the real `CrawlerWorker` + real `PageStore` against a scripted
     fake upstream (`fake_fetch_page`) and an in-memory fake asyncpg pool
     (`FakePagesPool`) -- the same "fake the transport, keep the real logic"
     approach `fakeredis` already gives `test_frontier.py` for Redis.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import fakeredis.aioredis as fakeredis_aio
import pytest

from src.fetcher import FetchResult
from src.storage import PageStore, compute_retry_outcome
from src.worker import CrawlerWorker

# Note: no `pytestmark = pytest.mark.asyncio` here -- pytest.ini already sets
# asyncio_mode = auto, which treats every `async def test_*` as an asyncio
# test automatically. Applying the marker globally would (incorrectly) also
# apply it to the plain synchronous tests below.


# ---------------------------------------------------------------------------
# 1. Pure retry/backoff policy
# ---------------------------------------------------------------------------


def test_first_failure_schedules_a_retry_with_the_base_delay():
    outcome, delay = compute_retry_outcome(
        retry_count=1, max_retries=3, base_delay_seconds=5, max_delay_seconds=300
    )
    assert outcome == "retry_scheduled"
    assert delay == 5


def test_backoff_doubles_each_retry():
    delays = [
        compute_retry_outcome(retry_count=n, max_retries=10, base_delay_seconds=5, max_delay_seconds=10_000)[1]
        for n in (1, 2, 3, 4)
    ]
    assert delays == [5, 10, 20, 40]


def test_backoff_is_capped_at_max_delay():
    outcome, delay = compute_retry_outcome(
        retry_count=6, max_retries=10, base_delay_seconds=5, max_delay_seconds=60
    )
    assert outcome == "retry_scheduled"
    assert delay == 60  # 5 * 2**5 = 160, capped down to 60


def test_exceeding_max_retries_gives_up_permanently():
    outcome, delay = compute_retry_outcome(
        retry_count=4, max_retries=3, base_delay_seconds=5, max_delay_seconds=300
    )
    assert outcome == "failed"
    assert delay is None


def test_max_retries_zero_means_no_retries_at_all():
    outcome, delay = compute_retry_outcome(
        retry_count=1, max_retries=0, base_delay_seconds=5, max_delay_seconds=300
    )
    assert outcome == "failed"
    assert delay is None


# ---------------------------------------------------------------------------
# 2. End-to-end: CrawlerWorker + PageStore against a scripted flaky upstream
# ---------------------------------------------------------------------------


class FakePagesPool:
    """In-memory stand-in for asyncpg.Pool that understands exactly the
    queries `PageStore` issues. This exercises the real `PageStore` code
    (including the retry bookkeeping in `record_failure`) end to end without
    a real Postgres instance.
    """

    def __init__(self) -> None:
        self.pages: dict[int, dict] = {}
        self._next_id = 1

    def _by_url(self, url: str) -> dict | None:
        for row in self.pages.values():
            if row["url"] == url:
                return row
        return None

    async def execute(self, sql: str, *args) -> None:
        s = " ".join(sql.split())
        if "INSERT INTO domains" in s:
            return
        if "status = 'in_progress'" in s:
            self.pages[args[0]]["status"] = "in_progress"
        elif "status = 'crawled'," in s:
            page_id, http_status, title, text_hash, etag, last_modified, content_length, minio_key, next_crawl_at = args
            self.pages[page_id].update(
                status="crawled",
                http_status=http_status,
                title=title,
                content_hash=text_hash,
                etag=etag,
                last_modified=last_modified,
                content_length=content_length,
                minio_key=minio_key,
                next_crawl_at=next_crawl_at,
                error=None,
            )
        elif "status = 'not_modified'" in s:
            page_id, next_crawl_at = args
            self.pages[page_id].update(status="not_modified", next_crawl_at=next_crawl_at, error=None)
        elif "status = 'failed', next_retry_at = NULL WHERE id = $1" in s and "http_status" not in s:
            self.pages[args[0]].update(status="failed", next_retry_at=None)
        elif "status = 'failed', http_status = $2" in s:
            page_id, http_status, error = args
            self.pages[page_id].update(status="failed", http_status=http_status, error=error, next_retry_at=None)
        elif "status = 'pending', next_retry_at = $2" in s:
            page_id, next_retry_at = args
            self.pages[page_id].update(status="pending", next_retry_at=next_retry_at)
        elif "status = 'skipped'" in s:
            page_id, reason = args
            self.pages[page_id].update(status="skipped", error=reason)
        else:
            raise NotImplementedError(sql)

    async def fetchrow(self, sql: str, *args):
        s = " ".join(sql.split())
        if "INSERT INTO pages(url, url_hash, domain, depth, status)" in s:
            url, url_hash_, domain, depth = args
            if self._by_url(url) is not None:
                return None
            page_id = self._next_id
            self._next_id += 1
            self.pages[page_id] = {
                "id": page_id,
                "url": url,
                "url_hash": url_hash_,
                "domain": domain,
                "depth": depth,
                "status": "pending",
                "http_status": None,
                "error": None,
                "etag": None,
                "last_modified": None,
                "retry_count": 0,
                "next_retry_at": None,
                "next_crawl_at": None,
            }
            return {"id": page_id}
        if s.startswith("SELECT id FROM pages WHERE url"):
            row = self._by_url(args[0])
            return {"id": row["id"]} if row else None
        if "SELECT etag, last_modified FROM pages WHERE url" in s:
            row = self._by_url(args[0])
            if row is None:
                return None
            return {"etag": row["etag"], "last_modified": row["last_modified"]}
        if "retry_count = retry_count + 1" in s:
            page_id, http_status, error = args
            row = self.pages[page_id]
            row["retry_count"] += 1
            row["http_status"] = http_status
            row["error"] = error
            return {"retry_count": row["retry_count"]}
        if s.startswith("SELECT count(*)"):
            n = sum(1 for r in self.pages.values() if r["status"] in ("crawled", "not_modified"))
            return {"c": n}
        raise NotImplementedError(sql)

    async def fetch(self, sql: str, *args):
        s = " ".join(sql.split())
        if "WHERE status = 'pending' AND next_retry_at IS NOT NULL" in s:
            now = datetime.now(timezone.utc)
            rows = [
                r
                for r in self.pages.values()
                if r["status"] == "pending" and r["next_retry_at"] is not None and r["next_retry_at"] <= now
            ]
            rows.sort(key=lambda r: r["next_retry_at"])
            return [dict(r) for r in rows]
        raise NotImplementedError(sql)


class FakeObjectStore:
    async def put_html(self, page_id: int, html: str) -> str:
        return f"pages/{page_id}.html"


def _make_worker(pool: FakePagesPool) -> tuple[CrawlerWorker, object]:
    redis_client = fakeredis_aio.FakeRedis()
    worker = CrawlerWorker(redis_client, PageStore(pool), FakeObjectStore(), session=None)
    worker.robots.can_fetch = AsyncMock(return_value=True)
    worker.robots.crawl_delay = AsyncMock(return_value=0.0)
    # Bypass the real per-domain cooldown: it's exercised by its own tests
    # elsewhere, and a real (if tiny) cooldown window would make this test's
    # tight claim/process loop flaky by occasionally requeuing instead of
    # fetching.
    worker.rate_limiter.try_acquire = AsyncMock(return_value=True)
    return worker, redis_client


async def _release_retry_backoff(pool: FakePagesPool) -> None:
    """Simulate backoff elapsing without a real sleep, by pulling every
    scheduled retry's `next_retry_at` into the past.
    """
    for row in pool.pages.values():
        if row["status"] == "pending" and row["next_retry_at"] is not None:
            row["next_retry_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)


async def test_flaky_upstream_is_retried_and_eventually_succeeds(monkeypatch):
    """A mock upstream that fails twice (HTTP 500) then succeeds should end
    up 'crawled', with retry_count reflecting the two genuine failures --
    never permanently 'failed'.
    """
    pool = FakePagesPool()
    worker, redis_client = _make_worker(pool)
    await worker.frontier.ensure_group()

    url = "https://example.com/flaky"
    await worker._enqueue_url(url, depth=0, parent_id=None)

    script = [
        FetchResult(status=500, url=url, html=None, etag=None, last_modified=None, content_length=None,
                    not_modified=False, error="HTTP 500"),
        FetchResult(status=500, url=url, html=None, etag=None, last_modified=None, content_length=None,
                    not_modified=False, error="HTTP 500"),
        FetchResult(status=200, url=url, html="<html><head><title>OK</title></head><body>ok</body></html>",
                    etag=None, last_modified=None, content_length=42, not_modified=False, error=None),
    ]
    call_count = 0

    async def fake_fetch_page(session, fetch_url, user_agent, timeout, etag, last_modified):
        nonlocal call_count
        result = script[call_count]
        call_count += 1
        return result

    monkeypatch.setattr("src.worker.fetch_page", fake_fetch_page)

    for round_index in range(len(script)):
        items = await worker.frontier.claim(count=1)
        assert len(items) == 1, f"expected a claimable item on round {round_index}"
        await worker._process_item(items[0])

        if round_index < len(script) - 1:
            # The scheduler loop would normally wait out the backoff and then
            # re-enqueue; skip the real sleep and do exactly what it does.
            await _release_retry_backoff(pool)
            due = await worker.pages.due_for_retry(limit=10)
            for row in due:
                await worker.frontier.enqueue(row["url"], row["domain"], row["depth"], row["id"], None)

    assert call_count == 3
    page = pool._by_url(url)
    assert page["status"] == "crawled"
    assert page["retry_count"] == 2  # two genuine failures before the success
    await redis_client.aclose()


async def test_permanently_broken_upstream_is_marked_failed_after_retry_budget_exhausted(monkeypatch):
    """A URL that always returns HTTP 500 should be retried up to
    config.MAX_RETRIES times and only then permanently marked 'failed'.
    """
    from src import config

    pool = FakePagesPool()
    worker, redis_client = _make_worker(pool)
    await worker.frontier.ensure_group()

    url = "https://example.com/always-down"
    await worker._enqueue_url(url, depth=0, parent_id=None)

    async def always_failing_fetch_page(session, fetch_url, user_agent, timeout, etag, last_modified):
        return FetchResult(status=500, url=fetch_url, html=None, etag=None, last_modified=None,
                            content_length=None, not_modified=False, error="HTTP 500")

    monkeypatch.setattr("src.worker.fetch_page", always_failing_fetch_page)

    attempts = 0
    max_attempts = config.MAX_RETRIES + 2  # +1 initial, +1 safety margin
    page = pool._by_url(url)
    while page["status"] != "failed" and attempts < max_attempts:
        items = await worker.frontier.claim(count=1)
        assert len(items) == 1, f"expected a claimable item on attempt {attempts}"
        await worker._process_item(items[0])
        attempts += 1
        page = pool._by_url(url)
        if page["status"] == "pending":
            await _release_retry_backoff(pool)
            due = await worker.pages.due_for_retry(limit=10)
            for row in due:
                await worker.frontier.enqueue(row["url"], row["domain"], row["depth"], row["id"], None)

    assert page["status"] == "failed"
    assert page["retry_count"] == config.MAX_RETRIES + 1  # 1 initial attempt + MAX_RETRIES retries
    assert attempts == config.MAX_RETRIES + 1
    await redis_client.aclose()
