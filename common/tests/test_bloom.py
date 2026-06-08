import pytest

from common.bloom import BloomFilter, InMemoryBitBackend


def make_filter(size_bits: int = 1 << 12, num_hashes: int = 5) -> BloomFilter:
    return BloomFilter(InMemoryBitBackend(), key="test", size_bits=size_bits, num_hashes=num_hashes)


async def test_might_contain_false_for_unseen_item():
    bf = make_filter()
    assert await bf.might_contain("https://example.com/a") is False


async def test_add_then_might_contain_true():
    bf = make_filter()
    await bf.add("https://example.com/a")
    assert await bf.might_contain("https://example.com/a") is True


async def test_never_false_negative_across_many_items():
    # The one hard guarantee a Bloom filter must uphold: no false negatives.
    # False positives are allowed (and expected in small proportion), but
    # anything actually added must always test as present.
    bf = make_filter(size_bits=1 << 14, num_hashes=6)
    urls = [f"https://example.com/page/{i}" for i in range(500)]
    for url in urls:
        await bf.add(url)
    for url in urls:
        assert await bf.might_contain(url) is True


async def test_add_if_new_returns_true_once_then_false():
    bf = make_filter()
    url = "https://example.com/dup"
    assert await bf.add_if_new(url) is True
    assert await bf.add_if_new(url) is False
    assert await bf.add_if_new(url) is False


async def test_distinct_urls_are_independent():
    bf = make_filter()
    await bf.add("https://example.com/one")
    # A second, unrelated URL should (with overwhelming probability at this
    # size/hash-count) not be reported as already seen.
    assert await bf.might_contain("https://example.com/two") is False


async def test_false_positive_rate_is_reasonably_bounded():
    # Not a proof, but a regression guard: with 1000 items in a 2^16-bit
    # filter and 7 hashes (~65x bits per item), the false positive rate
    # should be well under 5% -- if this starts failing it likely means the
    # hashing distribution or size math regressed.
    bf = make_filter(size_bits=1 << 16, num_hashes=7)
    inserted = [f"https://example.com/in/{i}" for i in range(1000)]
    for url in inserted:
        await bf.add(url)

    probes = [f"https://example.com/out/{i}" for i in range(2000)]
    false_positives = 0
    for url in probes:
        if await bf.might_contain(url):
            false_positives += 1
    rate = false_positives / len(probes)
    assert rate < 0.05


def test_rejects_invalid_construction_params():
    with pytest.raises(ValueError):
        BloomFilter(InMemoryBitBackend(), size_bits=0)
    with pytest.raises(ValueError):
        BloomFilter(InMemoryBitBackend(), num_hashes=0)


async def test_different_keys_do_not_collide_on_shared_backend():
    backend = InMemoryBitBackend()
    bf1 = BloomFilter(backend, key="filter1")
    bf2 = BloomFilter(backend, key="filter2")
    await bf1.add("https://example.com/shared-path")
    assert await bf1.might_contain("https://example.com/shared-path") is True
    assert await bf2.might_contain("https://example.com/shared-path") is False
