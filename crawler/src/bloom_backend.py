"""Wires common.bloom.BloomFilter to a live Redis client using the crawler's
configured size/hash-count. Kept as a one-line factory so worker.py doesn't
need to import RedisBitBackend directly.
"""
from __future__ import annotations

from common.bloom import BloomFilter, RedisBitBackend

from . import config


def build_bloom_filter(redis_client) -> BloomFilter:
    backend = RedisBitBackend(redis_client)
    return BloomFilter(
        backend,
        key="bloom:seen_urls",
        size_bits=config.BLOOM_SIZE_BITS,
        num_hashes=config.BLOOM_NUM_HASHES,
    )
