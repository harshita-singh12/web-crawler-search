"""Postgres read access for the API service. Read-only from this service's
perspective -- all writes to pages/terms/postings happen in the crawler and
indexer services.
"""
from __future__ import annotations

from typing import Any

import asyncpg


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)


async def get_postings(pool: asyncpg.Pool, terms: list[str]) -> tuple[dict[str, dict[int, int]], dict[str, int]]:
    """Returns (postings, doc_freqs).
    postings[term] = {page_id: tf, ...}
    doc_freqs[term] = number of documents containing that term
    """
    if not terms:
        return {}, {}
    rows = await pool.fetch(
        "SELECT term, page_id, tf FROM postings WHERE term = ANY($1::text[])", terms
    )
    postings: dict[str, dict[int, int]] = {t: {} for t in terms}
    for r in rows:
        postings[r["term"]][r["page_id"]] = r["tf"]

    df_rows = await pool.fetch("SELECT term, doc_freq FROM terms WHERE term = ANY($1::text[])", terms)
    doc_freqs = {r["term"]: r["doc_freq"] for r in df_rows}
    return postings, doc_freqs


async def total_docs(pool: asyncpg.Pool) -> int:
    row = await pool.fetchrow("SELECT value FROM index_stats WHERE key = 'total_docs'")
    return int(row["value"]) if row else 0


async def get_pages_metadata(pool: asyncpg.Pool, page_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not page_ids:
        return {}
    rows = await pool.fetch(
        """
        SELECT id, url, domain, title, pagerank, tfidf_norm, minio_key
        FROM pages
        WHERE id = ANY($1::bigint[])
        """,
        page_ids,
    )
    return {r["id"]: dict(r) for r in rows}


async def get_doc_norms(pool: asyncpg.Pool, page_ids: list[int]) -> dict[int, float]:
    if not page_ids:
        return {}
    rows = await pool.fetch(
        "SELECT id, tfidf_norm FROM pages WHERE id = ANY($1::bigint[])", page_ids
    )
    return {r["id"]: (r["tfidf_norm"] or 0.0) for r in rows}


async def get_pagerank_scores(pool: asyncpg.Pool, page_ids: list[int]) -> dict[int, float]:
    if not page_ids:
        return {}
    rows = await pool.fetch(
        "SELECT id, pagerank FROM pages WHERE id = ANY($1::bigint[])", page_ids
    )
    return {r["id"]: (r["pagerank"] or 0.0) for r in rows}


async def get_stats(pool: asyncpg.Pool) -> dict[str, Any]:
    rows = await pool.fetch("SELECT key, value, updated_at FROM index_stats")
    stats: dict[str, Any] = {"total_docs": 0, "total_terms": 0, "last_crawl_at": None}
    for r in rows:
        if r["key"] == "total_docs":
            stats["total_docs"] = int(r["value"])
        elif r["key"] == "total_terms":
            stats["total_terms"] = int(r["value"])
    last_crawl = await pool.fetchrow(
        "SELECT max(last_crawled_at) AS t FROM pages WHERE last_crawled_at IS NOT NULL"
    )
    if last_crawl and last_crawl["t"]:
        stats["last_crawl_at"] = last_crawl["t"].isoformat()
    return stats
