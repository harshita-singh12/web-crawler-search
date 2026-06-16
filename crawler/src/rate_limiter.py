"""Distributed per-domain rate limiting shared across all worker processes
via a single Redis key per domain.

The core trick is `SET key value NX PX ttl`: only one caller across the
entire fleet can ever win that call in a given cooldown window, so "am I
clear to hit this domain right now" is an atomic check-and-set instead of a
race-prone read-then-write.
"""
from __future__ import annotations

import time


class DistributedRateLimiter:
    def __init__(self, redis_client, default_delay_seconds: float) -> None:
        self._redis = redis_client
        self._default_delay = default_delay_seconds

    @staticmethod
    def _key(domain: str) -> str:
        return f"ratelimit:{domain}"

    async def try_acquire(self, domain: str, delay_seconds: float | None = None) -> bool:
        """Returns True if the caller may fetch `domain` right now (and has
        just reserved the next `delay_seconds` window for it), False if
        another fetch to this domain happened too recently.
        """
        delay = delay_seconds if delay_seconds is not None else self._default_delay
        delay_ms = max(1, int(delay * 1000))
        acquired = await self._redis.set(
            self._key(domain), str(time.time()), nx=True, px=delay_ms
        )
        return bool(acquired)

    async def seconds_until_free(self, domain: str) -> float:
        ttl_ms = await self._redis.pttl(self._key(domain))
        if ttl_ms is None or ttl_ms < 0:
            return 0.0
        return ttl_ms / 1000.0
