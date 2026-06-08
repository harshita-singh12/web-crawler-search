"""Postgres (page/link/domain metadata) and MinIO (raw HTML) persistence for
the crawler. See DESIGN.md section 2 for the schema.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PageStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "PageStore":
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def ensure_domain(self, domain: str, crawl_delay: float) -> None:
        await self.pool.execute(
            """
            INSERT INTO domains(domain, crawl_delay_sec)
            VALUES ($1, $2)
            ON CONFLICT (domain) DO NOTHING
            """,
            domain,
            crawl_delay,
        )

    async def update_robots(self, domain: str, robots_txt: str | None, crawl_delay: float, disallow_all: bool) -> None:
        await self.pool.execute(
            """
            INSERT INTO domains(domain, robots_txt, robots_fetched_at, crawl_delay_sec, disallow_all)
            VALUES ($1, $2, now(), $3, $4)
            ON CONFLICT (domain) DO UPDATE
                SET robots_txt = EXCLUDED.robots_txt,
                    robots_fetched_at = EXCLUDED.robots_fetched_at,
                    crawl_delay_sec = EXCLUDED.crawl_delay_sec,
                    disallow_all = EXCLUDED.disallow_all
            """,
            domain,
            robots_txt,
            crawl_delay,
            disallow_all,
        )

    async def get_or_create_page(self, url: str, domain: str, depth: int) -> tuple[int, bool]:
        """Returns (page_id, created). created=False means the URL already
        existed (the Postgres UNIQUE constraint's dedup path -- the Bloom
        filter is the fast-path check before we even get here).
        """
        row = await self.pool.fetchrow(
            """
            INSERT INTO pages(url, url_hash, domain, depth, status)
            VALUES ($1, $2, $3, $4, 'pending')
            ON CONFLICT (url) DO NOTHING
            RETURNING id
            """,
            url,
            url_hash(url),
            domain,
            depth,
        )
        if row:
            return row["id"], True
        existing = await self.pool.fetchrow("SELECT id FROM pages WHERE url = $1", url)
        return existing["id"], False

    async def get_conditional_headers(self, url: str) -> tuple[str | None, str | None]:
        row = await self.pool.fetchrow("SELECT etag, last_modified FROM pages WHERE url = $1", url)
        if not row:
            return None, None
        return row["etag"], row["last_modified"]

    async def mark_in_progress(self, page_id: int) -> None:
        await self.pool.execute("UPDATE pages SET status = 'in_progress' WHERE id = $1", page_id)

    async def record_success(
        self,
        page_id: int,
        *,
        http_status: int,
        title: str | None,
        text_hash: str | None,
        etag: str | None,
        last_modified: str | None,
        content_length: int | None,
        minio_key: str | None,
        recrawl_interval_hours: float,
    ) -> None:
        next_crawl_at = datetime.now(timezone.utc) + timedelta(hours=recrawl_interval_hours)
        await self.pool.execute(
            """
            UPDATE pages SET
                status = 'crawled',
                http_status = $2,
                title = $3,
                content_hash = $4,
                etag = $5,
                last_modified = $6,
                content_length = $7,
                minio_key = $8,
                first_crawled_at = COALESCE(first_crawled_at, now()),
                last_crawled_at = now(),
                next_crawl_at = $9,
                error = NULL
            WHERE id = $1
            """,
            page_id,
            http_status,
            title,
            text_hash,
            etag,
            last_modified,
            content_length,
            minio_key,
            next_crawl_at,
        )

    async def record_not_modified(self, page_id: int, recrawl_interval_hours: float) -> None:
        next_crawl_at = datetime.now(timezone.utc) + timedelta(hours=recrawl_interval_hours)
        await self.pool.execute(
            """
            UPDATE pages SET status = 'not_modified', last_crawled_at = now(),
                              next_crawl_at = $2, error = NULL
            WHERE id = $1
            """,
            page_id,
            next_crawl_at,
        )

    async def record_failure(self, page_id: int, http_status: int | None, error: str) -> None:
        await self.pool.execute(
            """
            UPDATE pages SET status = 'failed', http_status = $2, error = $3, last_crawled_at = now()
            WHERE id = $1
            """,
            page_id,
            http_status,
            error[:2000],
        )

    async def record_skipped(self, page_id: int, reason: str) -> None:
        await self.pool.execute(
            "UPDATE pages SET status = 'skipped', error = $2 WHERE id = $1",
            page_id,
            reason[:2000],
        )

    async def insert_links(self, src_page_id: int, links: list[tuple[str, str]]) -> None:
        if not links:
            return
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for dst_url, anchor_text in links:
                    dst_row = await conn.fetchrow("SELECT id FROM pages WHERE url = $1", dst_url)
                    dst_id = dst_row["id"] if dst_row else None
                    await conn.execute(
                        """
                        INSERT INTO links(src_page_id, dst_url, dst_page_id, anchor_text)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (src_page_id, dst_url) DO NOTHING
                        """,
                        src_page_id,
                        dst_url,
                        dst_id,
                        anchor_text,
                    )

    async def fetched_page_count(self) -> int:
        row = await self.pool.fetchrow(
            "SELECT count(*) AS c FROM pages WHERE status IN ('crawled', 'not_modified')"
        )
        return int(row["c"])

    async def due_for_recrawl(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT id, url, domain, depth FROM pages
            WHERE status IN ('crawled', 'not_modified') AND next_crawl_at IS NOT NULL AND next_crawl_at <= now()
            ORDER BY next_crawl_at ASC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


class ObjectStore:
    """Thin async wrapper around the (blocking) MinIO client -- calls are
    dispatched via asyncio.to_thread so they don't block the event loop that
    the rest of the worker relies on for concurrency.
    """

    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def connect(cls, endpoint: str, access_key: str, secret_key: str, secure: bool, bucket: str) -> "ObjectStore":
        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        if not client.bucket_exists(bucket):
            try:
                client.make_bucket(bucket)
            except S3Error as exc:
                if "BucketAlreadyOwnedByYou" not in str(exc):
                    raise
        return cls(client, bucket)

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            self._bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )

    async def put_html(self, page_id: int, html: str) -> str:
        key = f"pages/{page_id}.html"
        data = html.encode("utf-8", errors="replace")
        await asyncio.to_thread(self._put, key, data, "text/html; charset=utf-8")
        return key

    def _get(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    async def get_html(self, key: str) -> str:
        data = await asyncio.to_thread(self._get, key)
        return data.decode("utf-8", errors="replace")
