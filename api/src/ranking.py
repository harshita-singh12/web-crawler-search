"""Pure ranking-combination logic: TF-IDF relevance (via common.tfidf) plus a
PageRank boost. Kept free of database/network access so it's directly unit
testable -- all the DB-shaped data (postings, doc freqs, norms, pagerank) is
passed in as plain dicts.
"""
from __future__ import annotations

from common.tfidf import score_document


def rank_pages(
    query_term_counts: dict[str, int],
    postings: dict[str, dict[int, int]],
    doc_freqs: dict[str, int],
    total_docs: int,
    doc_norms: dict[int, float],
    pagerank: dict[int, float],
    alpha: float,
    limit: int,
) -> list[tuple[int, float]]:
    """Returns [(page_id, final_score), ...] sorted descending, top `limit`.

    `postings[term]` maps page_id -> raw tf for pages containing that term
    (only candidate pages -- i.e. the union of postings for all query terms
    -- need to be considered, which keeps this cheap even over a large
    corpus since we never touch pages that share zero query terms).
    """
    candidate_page_ids: set[int] = set()
    for term in query_term_counts:
        candidate_page_ids.update(postings.get(term, {}).keys())

    scored: list[tuple[int, float]] = []
    for page_id in candidate_page_ids:
        doc_term_freqs = {
            term: postings[term][page_id] for term in query_term_counts if page_id in postings.get(term, {})
        }
        tfidf_score = score_document(
            query_term_counts,
            doc_term_freqs,
            doc_freqs,
            total_docs,
            doc_norm=doc_norms.get(page_id),
        )
        if tfidf_score <= 0:
            continue
        pr = pagerank.get(page_id, 0.0)
        final_score = tfidf_score * (1.0 + alpha * pr)
        scored.append((page_id, final_score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]
