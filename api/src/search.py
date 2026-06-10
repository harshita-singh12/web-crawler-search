"""Query orchestration: tokenize -> fetch postings -> rank -> fetch metadata
-> generate snippets for the (small) top-N result set only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import asyncpg

from common.snippet import make_snippet
from common.tfidf import term_frequencies
from common.tokenizer import tokenize

from . import config, db
from .object_store import ObjectStore


@dataclass
class SearchResult:
    url: str
    title: str | None
    snippet: str
    score: float
    domain: str | None
    pagerank: float


@dataclass
class SearchResponse:
    query: str
    took_ms: float
    total_matches: int
    results: list[SearchResult]


async def search(
    pool: asyncpg.Pool,
    objects: ObjectStore,
    query: str,
    limit: int,
) -> SearchResponse:
    start = time.monotonic()
    query_tokens = tokenize(query)
    if not query_tokens:
        return SearchResponse(query=query, took_ms=(time.monotonic() - start) * 1000, total_matches=0, results=[])

    query_term_counts = term_frequencies(query_tokens)
    terms = list(query_term_counts.keys())

    postings, doc_freqs = await db.get_postings(pool, terms)
    candidate_ids: set[int] = set()
    for term_postings in postings.values():
        candidate_ids.update(term_postings.keys())

    if not candidate_ids:
        return SearchResponse(query=query, took_ms=(time.monotonic() - start) * 1000, total_matches=0, results=[])

    total = await db.total_docs(pool)
    doc_norms = await db.get_doc_norms(pool, list(candidate_ids))
    pagerank_scores = await db.get_pagerank_scores(pool, list(candidate_ids))

    from .ranking import rank_pages

    ranked = rank_pages(
        query_term_counts,
        postings,
        doc_freqs,
        total,
        doc_norms,
        pagerank_scores,
        alpha=config.PAGERANK_BOOST_ALPHA,
        limit=limit,
    )

    metadata = await db.get_pages_metadata(pool, [pid for pid, _ in ranked])

    results: list[SearchResult] = []
    query_term_set = set(terms)
    for page_id, score in ranked:
        meta = metadata.get(page_id)
        if not meta:
            continue
        snippet = ""
        if meta.get("minio_key"):
            try:
                html = await objects.get_html(meta["minio_key"])
                from common.text_extract import extract_text_and_title

                _, text = extract_text_and_title(html)
                snippet = make_snippet(text, query_term_set)
            except Exception:
                snippet = ""
        results.append(
            SearchResult(
                url=meta["url"],
                title=meta.get("title"),
                snippet=snippet,
                score=round(score, 4),
                domain=meta.get("domain"),
                pagerank=round(meta.get("pagerank") or 0.0, 4),
            )
        )

    return SearchResponse(
        query=query,
        took_ms=round((time.monotonic() - start) * 1000, 2),
        total_matches=len(candidate_ids),
        results=results,
    )
