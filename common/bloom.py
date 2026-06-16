"""A Bloom filter for distributed URL-seen tracking.

The bit-storage is pluggable (`BitBackend` protocol) so the exact same
hashing/lookup logic can be:
  - unit tested with `InMemoryBitBackend` (no network, deterministic, fast), or
  - run in production with `RedisBitBackend`, so every crawler worker process
    shares one filter instead of each having its own in-memory copy.

A Bloom filter (vs. a Redis SET of full URLs) was chosen because it does O(k)
fixed-size bit operations per check instead of storing every URL string, at
the cost of a small, tunable false-positive rate.
"""
from __future__ import annotations

import hashlib
from typing import Protocol


class BitBackend(Protocol):
    async def get_bit(self, key: str, index: int) -> int: ...

    async def set_bit(self, key: str, index: int) -> None: ...


class InMemoryBitBackend:
    """Local, in-process bit storage. Used by unit tests and by any
    single-process use of the filter that doesn't need cross-process sharing.
    """

    def __init__(self) -> None:
        self._bits: set[tuple[str, int]] = set()

    async def get_bit(self, key: str, index: int) -> int:
        return 1 if (key, index) in self._bits else 0

    async def set_bit(self, key: str, index: int) -> None:
        self._bits.add((key, index))


class RedisBitBackend:
    """Redis-backed bit storage (SETBIT/GETBIT) shared across processes."""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def get_bit(self, key: str, index: int) -> int:
        return int(await self._redis.getbit(key, index))

    async def set_bit(self, key: str, index: int) -> None:
        await self._redis.setbit(key, index, 1)


class BloomFilter:
    """Bloom filter with configurable size and hash count.

    Default size (2^23 bits = 1MiB) and hash count (7) give a false-positive
    rate of roughly 0.8% at 1,000,000 inserted URLs
    (p ~= (1 - e^(-k*n/m))^k) -- comfortably enough headroom for this
    project's default MAX_PAGES=200 while still being a small, fixed Redis
    key rather than growing with the number of URLs seen.
    """

    def __init__(
        self,
        backend: BitBackend,
        key: str = "bloom:urls",
        size_bits: int = 1 << 23,
        num_hashes: int = 7,
    ) -> None:
        if size_bits <= 0:
            raise ValueError("size_bits must be positive")
        if num_hashes <= 0:
            raise ValueError("num_hashes must be positive")
        self._backend = backend
        self._key = key
        self._size_bits = size_bits
        self._num_hashes = num_hashes

    def _indices(self, item: str) -> list[int]:
        indices = []
        data = item.encode("utf-8")
        for i in range(self._num_hashes):
            digest = hashlib.blake2b(data, digest_size=8, salt=str(i).encode().ljust(16, b"\0")[:16]).digest()
            indices.append(int.from_bytes(digest, "big") % self._size_bits)
        return indices

    async def might_contain(self, item: str) -> bool:
        for index in self._indices(item):
            if await self._backend.get_bit(self._key, index) == 0:
                return False
        return True

    async def add(self, item: str) -> None:
        for index in self._indices(item):
            await self._backend.set_bit(self._key, index)

    async def add_if_new(self, item: str) -> bool:
        """Check-then-set convenience used by the frontier before enqueuing a
        URL. Returns True if the item was not (probably) already present, in
        which case it has now been added. Note this has a benign race under
        concurrency (two workers could both observe "new" for the same URL
        seen at almost the same instant); the Postgres UNIQUE(url) constraint
        is the authoritative second line of defense against that.
        """
        if await self.might_contain(item):
            return False
        await self.add(item)
        return True
