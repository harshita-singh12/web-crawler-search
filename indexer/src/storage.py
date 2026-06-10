"""Postgres + MinIO access for the indexer service. See DESIGN.md section 3
for the incremental-update algorithm this implements.
"""
from __future__ import annotations

import asyncio
import io
import math
from typing import Any

import asyncpg
from minio import Minio

from common.tfidf import idf, tf_weight


class ObjectStore:
    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @classmethod
    def connect(cls, endpoint: str, access_key: str, secret_key: str, secure: bool, bucket: str) -> "ObjectStore":
        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        return cls(client, bucket)

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


class IndexStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "IndexStore":
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def pages_needing_index(self, limit: int) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """
            SELECT id, url, minio_key, content_hash, last_indexed_hash
            FROM pages
            WHERE status = 'crawled'
              AND minio_key IS NOT NULL
              AND content_hash IS NOT NULL
              AND (last_indexed_hash IS NULL OR last_indexed_hash != content_hash)
            ORDER BY last_crawled_at ASC NULLS FIRST
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]

    async def total_docs(self, conn=None) -> int:
        executor = conn or self.pool
        row = await executor.fetchrow("SELECT count(*) AS c FROM pages WHERE last_indexed_hash IS NOT NULL")
        return int(row["c"])

    async def reindex_page(self, page_id: int, term_tf: dict[str, int], content_hash: str) -> None:
        """Delete this page's old postings, insert the new ones, and adjust
        `terms.doc_freq` by the *difference* between old and new term sets
        (not a blind decrement-then-increment of every term), all inside one
        transaction so a crash mid-update can't corrupt doc_freq counts.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                old_rows = await conn.fetch("SELECT term FROM postings WHERE page_id = $1", page_id)
                old_terms = {r["term"] for r in old_rows}
                new_terms = set(term_tf.keys())

                removed = old_terms - new_terms
                added = new_terms - old_terms

                await conn.execute("DELETE FROM postings WHERE page_id = $1", page_id)

                for term in removed:
                    await conn.execute(
                        "UPDATE terms SET doc_freq = GREATEST(doc_freq - 1, 0) WHERE term = $1", term
                    )
                for term in added:
                    await conn.execute(
                        """
                        INSERT INTO terms(term, doc_freq) VALUES ($1, 1)
                        ON CONFLICT (term) DO UPDATE SET doc_freq = terms.doc_freq + 1
                        """,
                        term,
                    )

                if term_tf:
                    await conn.executemany(
                        "INSERT INTO postings(term, page_id, tf) VALUES ($1, $2, $3)",
                        [(term, page_id, tf) for term, tf in term_tf.items()],
                    )

                # Recompute this doc's TF-IDF norm using post-update doc
                # frequencies, so query-time cosine normalization is based on
                # a consistent snapshot (see DESIGN.md section 3).
                total = await self.total_docs(conn)
                if total == 0:
                    total = 1  # this doc itself now counts; avoid idf(0,0)
                doc_freq_rows = await conn.fetch(
                    "SELECT term, doc_freq FROM terms WHERE term = ANY($1::text[])", list(new_terms)
                ) if new_terms else []
                doc_freqs = {r["term"]: r["doc_freq"] for r in doc_freq_rows}
                weights = [tf_weight(tf) * idf(doc_freqs.get(term, 1), total) for term, tf in term_tf.items()]
                norm = math.sqrt(sum(w * w for w in weights)) if weights else 0.0

                await conn.execute(
                    """
                    UPDATE pages SET last_indexed_hash = $2, tfidf_norm = $3
                    WHERE id = $1
                    """,
                    page_id,
                    content_hash,
                    norm,
                )

    async def refresh_index_stats(self) -> int:
        total = await self.total_docs()
        await self.pool.execute(
            """
            INSERT INTO index_stats(key, value, updated_at) VALUES ('total_docs', $1, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            float(total),
        )
        term_count_row = await self.pool.fetchrow("SELECT count(*) AS c FROM terms WHERE doc_freq > 0")
        await self.pool.execute(
            """
            INSERT INTO index_stats(key, value, updated_at) VALUES ('total_terms', $1, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            float(term_count_row["c"]),
        )
        return total

    async def load_link_graph(self) -> dict[int, list[int]]:
        rows = await self.pool.fetch("SELECT id FROM pages WHERE status IN ('crawled', 'not_modified')")
        edges: dict[int, list[int]] = {r["id"]: [] for r in rows}
        link_rows = await self.pool.fetch(
            "SELECT src_page_id, dst_page_id FROM links WHERE dst_page_id IS NOT NULL"
        )
        for r in link_rows:
            src, dst = r["src_page_id"], r["dst_page_id"]
            if src in edges and dst in edges:
                edges[src].append(dst)
        return edges

    async def update_pagerank(self, scores: dict[int, float]) -> None:
        if not scores:
            return
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    "UPDATE pages SET pagerank = $2 WHERE id = $1",
                    list(scores.items()),
                )
