"""Pure TF-IDF math, shared by the indexer (which computes per-document
term frequencies and doc norms while building the inverted index) and the API
(which computes IDF and combines it with stored postings at query time).

Kept free of any database access so it is trivially unit-testable.
"""
from __future__ import annotations

import math
from collections import Counter


def term_frequencies(tokens: list[str]) -> dict[str, int]:
    """Raw term counts for a tokenized document."""
    return dict(Counter(tokens))


def tf_weight(raw_tf: int) -> float:
    """Sublinear TF scaling (1 + ln(tf)), the standard damping used so a term
    appearing 100 times isn't literally 100x as important as one appearing
    once. Returns 0 for non-positive input.
    """
    if raw_tf <= 0:
        return 0.0
    return 1.0 + math.log(raw_tf)


def idf(doc_freq: int, total_docs: int) -> float:
    """Smoothed IDF: ln((1 + N) / (1 + df)) + 1.

    The "+1" smoothing on both numerator and denominator keeps this defined
    (and positive) even when df == total_docs, and the trailing "+1" ensures
    a term appearing in every single document still contributes a small
    positive weight rather than dropping to zero, so it can't zero out a
    multi-term query where every doc matched by one term.
    """
    if total_docs <= 0:
        return 0.0
    return math.log((1 + total_docs) / (1 + doc_freq)) + 1.0


def doc_vector_norm(tfidf_values: list[float]) -> float:
    """L2 norm of a document's TF-IDF vector, used to cosine-normalize
    scores at query time so long documents don't win purely by being long.
    """
    return math.sqrt(sum(v * v for v in tfidf_values))


def score_document(
    query_term_counts: dict[str, int],
    doc_term_freqs: dict[str, int],
    doc_freqs: dict[str, int],
    total_docs: int,
    doc_norm: float | None = None,
) -> float:
    """Cosine-normalized TF-IDF score of one document against a query.

    query_term_counts: term -> count in the query (from term_frequencies on
        the tokenized query).
    doc_term_freqs: term -> raw tf in this document, for only the terms that
        are also in the query (a full posting lookup, not the whole doc).
    doc_freqs: term -> number of documents containing that term, for the same
        terms.
    total_docs: total number of documents in the index (N).
    doc_norm: precomputed L2 norm of the document's full TF-IDF vector, if
        available (this project stores it in pages.tfidf_norm at index time
        so this function doesn't need the whole document to score it). If
        None, normalization is skipped (falls back to raw dot product).
    """
    score = 0.0
    for term, q_count in query_term_counts.items():
        tf = doc_term_freqs.get(term)
        if not tf:
            continue
        df = doc_freqs.get(term, 0)
        weight = tf_weight(tf) * idf(df, total_docs)
        score += weight * q_count
    if doc_norm and doc_norm > 0:
        score /= doc_norm
    return score
