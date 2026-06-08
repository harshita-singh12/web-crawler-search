"""Tokenization shared by the indexer (documents) and the API (queries).

Using the exact same function on both sides is what makes TF-IDF lookups work
at all -- if the query were tokenized differently from documents, postings
lookups would silently miss.
"""
from __future__ import annotations

import re
import unicodedata

# A deliberately small, standard English stopword list. Not exhaustive by
# design: dropping too many words can make phrase-like queries ("to be or not
# to be") unsearchable. This project favors precision of common function
# words over completeness.
STOPWORDS: frozenset[str] = frozenset(
    """
    a an and are as at be by for from has have he her hers him his how i in
    is it its of on or our ours she that the their theirs there these they
    this those to was we were what when where which who whom will with you
    your yours not no do does did but if than then so such can could would
    should will shall may might must about into over under again further
    once here also
    """.split()
)

_TOKEN_RE = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)

# Suffixes stripped by the light stemmer, longest first so "ies" is tried
# before "s". This is intentionally not a full Porter stemmer -- it is a
# small, easily-explained heuristic that is good enough to fold plurals and
# simple verb inflections ("crawler"/"crawlers", "index"/"indexing") together
# without the complexity (and dependency) of a real stemming library.
_SUFFIXES = ("ies", "ing", "ed", "es", "s")
_MIN_STEM_LEN = 4


def _stem(word: str) -> str:
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= _MIN_STEM_LEN:
            if suf == "ies":
                return word[: -len(suf)] + "y"
            return word[: -len(suf)]
    return word


def tokenize(text: str, *, stem: bool = True, min_len: int = 2) -> list[str]:
    """Lowercase, Unicode-normalize, split into word/number tokens, drop
    stopwords and very short tokens, and (optionally) lightly stem.

    This is a pure function with no I/O, which is what makes it cheap to unit
    test directly.
    """
    if not text:
        return []
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(normalized):
        tok = match.group(0)
        if len(tok) < min_len:
            continue
        if tok in STOPWORDS:
            continue
        if stem and not tok.isdigit():
            tok = _stem(tok)
            if len(tok) < min_len:
                continue
        tokens.append(tok)
    return tokens
