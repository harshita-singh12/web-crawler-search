from src.ranking import rank_pages


def test_ranks_pages_by_relevance():
    query = {"search": 1}
    postings = {"search": {1: 5, 2: 1}}
    doc_freqs = {"search": 2}
    result = rank_pages(query, postings, doc_freqs, total_docs=10, doc_norms={}, pagerank={}, alpha=0.0, limit=10)
    page_ids = [p for p, _ in result]
    assert page_ids[0] == 1  # higher tf should rank first with no pagerank boost


def test_pagerank_boost_can_reorder_close_scores():
    query = {"term": 1}
    postings = {"term": {1: 3, 2: 3}}
    doc_freqs = {"term": 5}
    pagerank = {1: 0.0, 2: 1.0}
    result = rank_pages(
        query, postings, doc_freqs, total_docs=10, doc_norms={}, pagerank=pagerank, alpha=5.0, limit=10
    )
    assert result[0][0] == 2  # page 2 has identical tf but much higher pagerank


def test_only_candidate_pages_with_a_matching_term_are_scored():
    query = {"alpha": 1, "missing_term": 1}
    postings = {"alpha": {1: 2}, "missing_term": {}}
    doc_freqs = {"alpha": 3, "missing_term": 0}
    result = rank_pages(query, postings, doc_freqs, total_docs=10, doc_norms={}, pagerank={}, alpha=0.0, limit=10)
    assert [p for p, _ in result] == [1]


def test_respects_limit():
    query = {"x": 1}
    postings = {"x": {i: 1 for i in range(20)}}
    doc_freqs = {"x": 20}
    result = rank_pages(query, postings, doc_freqs, total_docs=100, doc_norms={}, pagerank={}, alpha=0.0, limit=5)
    assert len(result) == 5


def test_empty_candidates_returns_empty():
    result = rank_pages({"x": 1}, {"x": {}}, {"x": 0}, total_docs=10, doc_norms={}, pagerank={}, alpha=0.0, limit=10)
    assert result == []


def test_multi_term_query_prefers_documents_matching_more_terms():
    query = {"search": 1, "engine": 1}
    postings = {
        "search": {1: 2, 2: 2},
        "engine": {1: 2},  # only page 1 matches both terms
    }
    doc_freqs = {"search": 5, "engine": 5}
    result = rank_pages(query, postings, doc_freqs, total_docs=20, doc_norms={}, pagerank={}, alpha=0.0, limit=10)
    assert result[0][0] == 1
