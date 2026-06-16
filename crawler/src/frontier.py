"""The URL frontier: a Redis Streams consumer group that lets many worker
processes pull work without double-crawling, and recovers work abandoned by
a crashed worker after a visibility timeout.

Deliberately takes any redis-py-compatible async client (works with both
`redis.asyncio.Redis` in production and `fakeredis.aioredis.FakeRedis` in
unit tests) so the claiming logic can be tested without a real Redis server.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class FrontierItem:
    entry_id: str
    url: str
    domain: str
    depth: int
    page_id: int
    parent_id: int | None


class Frontier:
    def __init__(
        self,
        redis_client,
        consumer_name: str,
        stream_key: str = "frontier:urls",
        group: str = "crawlers",
        visibility_timeout_ms: int = 5 * 60 * 1000,
    ) -> None:
        self._redis = redis_client
        self._consumer = consumer_name
        self._stream = stream_key
        self._group = group
        self._visibility_timeout_ms = visibility_timeout_ms

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except Exception as exc:
            # BUSYGROUP == group already exists, which is the expected
            # steady-state case after the first worker creates it.
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(
        self, url: str, domain: str, depth: int, page_id: int, parent_id: int | None = None
    ) -> str:
        payload = {
            "url": url,
            "domain": domain,
            "depth": str(depth),
            "page_id": str(page_id),
            "parent_id": "" if parent_id is None else str(parent_id),
        }
        entry_id = await self._redis.xadd(self._stream, payload)
        return entry_id.decode() if isinstance(entry_id, bytes) else entry_id

    def _parse_entry(self, entry_id: Any, fields: dict) -> FrontierItem:
        def _s(v: Any) -> str:
            return v.decode() if isinstance(v, bytes) else v

        raw = {_s(k): _s(v) for k, v in fields.items()}
        eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
        parent = raw.get("parent_id") or ""
        return FrontierItem(
            entry_id=eid,
            url=raw["url"],
            domain=raw["domain"],
            depth=int(raw.get("depth", "0")),
            page_id=int(raw["page_id"]),
            parent_id=int(parent) if parent else None,
        )

    async def claim(self, count: int = 1) -> list[FrontierItem]:
        """Return up to `count` items of work for this consumer. Prefers
        reclaiming entries abandoned by a crashed worker (idle longer than
        the visibility timeout) over pulling brand new work, so a pile of
        crashed work doesn't get starved forever behind a busy stream.
        """
        items: list[FrontierItem] = []

        reclaimed = await self._redis.xautoclaim(
            self._stream,
            self._group,
            self._consumer,
            min_idle_time=self._visibility_timeout_ms,
            start_id="0-0",
            count=count,
        )
        # redis-py returns (next_start_id, [(id, fields), ...], deleted_ids)
        _, reclaimed_entries, *_ = reclaimed
        for entry_id, fields in reclaimed_entries:
            if fields:
                items.append(self._parse_entry(entry_id, fields))
        if items:
            return items[:count]

        remaining = count - len(items)
        if remaining <= 0:
            return items

        result = await self._redis.xreadgroup(
            self._group, self._consumer, {self._stream: ">"}, count=remaining
        )
        for _stream_name, entries in result or []:
            for entry_id, fields in entries:
                items.append(self._parse_entry(entry_id, fields))
        return items

    async def ack(self, entry_id: str) -> None:
        await self._redis.xack(self._stream, self._group, entry_id)

    async def requeue(self, item: FrontierItem) -> None:
        """Put an item back at the tail of the stream (used when a domain is
        still in its rate-limit cooldown) and acknowledge the original
        delivery so it doesn't also get reclaimed later as "abandoned".
        """
        await self.enqueue(item.url, item.domain, item.depth, item.page_id, item.parent_id)
        await self.ack(item.entry_id)

    async def pending_count(self) -> int:
        info = await self._redis.xpending(self._stream, self._group)
        if not info:
            return 0
        if isinstance(info, dict):
            return int(info.get("pending", 0))
        return int(info[0]) if info[0] else 0

    async def stream_length(self) -> int:
        return int(await self._redis.xlen(self._stream))
