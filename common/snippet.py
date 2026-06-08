"""Query-time snippet extraction: given a document's plain text and the set
of query terms, return a short window of text around the first match so
search results look like a real search engine instead of a bare link list.
"""
from __future__ import annotations

import re

SNIPPET_WINDOW_CHARS = 160


def make_snippet(text: str, query_terms: set[str], window: int = SNIPPET_WINDOW_CHARS) -> str:
    if not text:
        return ""
    lower = text.lower()
    best_pos = -1
    for term in query_terms:
        pos = lower.find(term)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos
    if best_pos == -1:
        snippet = text[: window * 2]
    else:
        start = max(0, best_pos - window // 2)
        end = min(len(text), best_pos + window)
        snippet = text[start:end]
        if start > 0:
            snippet = "… " + snippet
        if end < len(text):
            snippet = snippet + " …"
    return re.sub(r"\s+", " ", snippet).strip()
