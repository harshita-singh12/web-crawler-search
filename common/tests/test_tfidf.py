import math

import pytest

from common.tfidf import (
    doc_vector_norm,
    idf,
    score_document,
    term_frequencies,
    tf_weight,
)


def test_term_frequencies_counts_occurrences():
    tokens = ["a", "b", "a", "c", "a"]
    assert term_frequencies(tokens) == {"a": 3, "b": 1, "c": 1}


def test_tf_weight_zero_for_absent_term():
    assert tf_weight(0) == 0.0
    assert tf_weight(-1) == 0.0


def test_tf_weight_is_sublinear():
    # 1 + ln(tf): monotonically increasing but sub-proportional.
    w1 = tf_weight(1)
    w10 = tf_weight(10)
    w100 = tf_weight(100)
    assert w1 == 1.0
    assert w1 < w10 < w100
    assert (w100 - w10) < (w10 - w1) * 10  # damping, not linear growth


def test_idf_is_zero_docs_returns_zero():
    assert idf(0, 0) == 0.0


def test_idf_decreases_as_doc_freq_increases():
    n = 1000
    assert idf(1, n) > idf(10, n) > idf(100, n) > idf(999, n)


def test_idf_is_always_positive_even_when_term_is_universal():
    # A term present in every single document should still contribute a
    # small positive weight, not zero out the score.
    n = 500
    assert idf(n, n) > 0.0


def test_doc_vector_norm():
    assert doc_vector_norm([3.0, 4.0]) == pytest.approx(5.0)
    assert doc_vector_norm([]) == 0.0


def test_score_document_ranks_higher_tf_above_lower_tf():
    total_docs = 100
    doc_freqs = {"search": 10}
    high_tf_doc = {"search": 20}
    low_tf_doc = {"search": 1}
    query = term_frequencies(["search"])

    high_score = score_document(query, high_tf_doc, doc_freqs, total_docs)
    low_score = score_document(query, low_tf_doc, doc_freqs, total_docs)
    assert high_score > low_score


def test_score_document_ranks_rare_term_above_common_term():
    total_docs = 1000
    query = term_frequencies(["rare"])
    doc_freqs_rare = {"rare": 2}
    doc_freqs_common = {"rare": 500}
    doc_term_freqs = {"rare": 5}

    rare_score = score_document(query, doc_term_freqs, doc_freqs_rare, total_docs)
    common_score = score_document(query, doc_term_freqs, doc_freqs_common, total_docs)
    assert rare_score > common_score


def test_score_document_zero_when_no_terms_match():
    query = term_frequencies(["missing"])
    doc_term_freqs = {"present": 5}
    doc_freqs = {"present": 3}
    assert score_document(query, doc_term_freqs, doc_freqs, 100) == 0.0


def test_score_document_normalizes_by_doc_norm():
    total_docs = 100
    query = term_frequencies(["term"])
    doc_term_freqs = {"term": 5}
    doc_freqs = {"term": 10}

    unnormalized = score_document(query, doc_term_freqs, doc_freqs, total_docs, doc_norm=None)
    normalized = score_document(query, doc_term_freqs, doc_freqs, total_docs, doc_norm=2.0)
    assert normalized == pytest.approx(unnormalized / 2.0)


def test_score_document_handles_multi_term_query():
    total_docs = 100
    query = term_frequencies(["search", "engine"])
    doc_term_freqs = {"search": 3, "engine": 2}
    doc_freqs = {"search": 20, "engine": 15}

    both_terms_score = score_document(query, doc_term_freqs, doc_freqs, total_docs)
    one_term_score = score_document(query, {"search": 3}, doc_freqs, total_docs)
    assert both_terms_score > one_term_score
