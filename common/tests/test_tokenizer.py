from common.tokenizer import tokenize


def test_lowercases_and_splits_words():
    assert tokenize("Hello World", stem=False) == ["hello", "world"]


def test_drops_stopwords():
    tokens = tokenize("the cat and the dog", stem=False)
    assert "the" not in tokens
    assert "and" not in tokens
    assert tokens == ["cat", "dog"]


def test_drops_short_tokens():
    tokens = tokenize("a I am ok go", stem=False, min_len=2)
    assert "a" not in tokens
    assert "i" not in tokens  # 'i' is also a stopword and length 1


def test_stemming_folds_plurals_together():
    assert tokenize("crawler crawlers", stem=True) == ["crawler", "crawler"]


def test_stemming_folds_ing_and_ed():
    tokens = tokenize("indexing indexed index", stem=True)
    assert tokens == ["index", "index", "index"]


def test_numbers_are_kept_as_tokens():
    assert tokenize("page 42 found", stem=False) == ["page", "42", "found"]


def test_unicode_text_is_normalized():
    # NFKC-normalizes full-width characters and handles non-ASCII letters.
    tokens = tokenize("café", stem=False)
    assert tokens == ["café"]


def test_empty_and_none_like_input():
    assert tokenize("") == []
    assert tokenize("   ") == []


def test_punctuation_is_not_tokenized():
    tokens = tokenize("hello, world! how are you?", stem=False)
    assert "," not in tokens
    assert "!" not in tokens
    assert "hello" in tokens
    assert "world" in tokens


def test_stemmer_does_not_over_strip_short_words():
    # Stripping "es"/"s" from a 4-letter word like "sees" would leave only 2
    # characters, which the stemmer's length guard should prevent -- the
    # word is left untouched rather than mangled into "se".
    assert tokenize("sees", stem=True) == ["sees"]
