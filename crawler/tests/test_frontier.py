"""Unit tests for the URL frontier's claiming semantics: exactly-once
delivery across competing consumers, and crash recovery via the visibility
timeout / XAUTOCLAIM reclaim path. See DESIGN.md section 1.

Uses fakeredis's async Streams implementation instead of a real Redis
server, since Frontier only depends on a redis-py-compatible client -- this
keeps the test fast and infra-free while still exercising the real
XADD/XREADGROUP/XACK/XAUTOCLAIM semantics fakeredis implements.
"""
import fakeredis.aioredis as fakeredis_aio
import pytest

from src.frontier import Frontier

STREAM = "test:frontier"
GROUP = "test-group"


@pytest.fixture
async def redis_client():
    client = fakeredis_aio.FakeRedis()
    yield client
    await client.aclose()


def make_frontier(redis_client, consumer: str, visibility_timeout_ms: int = 5 * 60 * 1000) -> Frontier:
    return Frontier(
        redis_client,
        consumer_name=consumer,
        stream_key=STREAM,
        group=GROUP,
        visibility_timeout_ms=visibility_timeout_ms,
    )


async def test_enqueue_and_claim_round_trip(redis_client):
    frontier = make_frontier(redis_client, "worker-a")
    await frontier.ensure_group()

    await frontier.enqueue("https://example.com/a", "example.com", depth=0, page_id=1)
    items = await frontier.claim(count=1)

    assert len(items) == 1
    assert items[0].url == "https://example.com/a"
    assert items[0].domain == "example.com"
    assert items[0].depth == 0
    assert items[0].page_id == 1


async def test_ensure_group_is_idempotent(redis_client):
    frontier = make_frontier(redis_client, "worker-a")
    await frontier.ensure_group()
    await frontier.ensure_group()  # must not raise on the second call


async def test_claim_returns_empty_when_stream_is_empty(redis_client):
    frontier = make_frontier(redis_client, "worker-a")
    await frontier.ensure_group()
    items = await frontier.claim(count=1)
    assert items == []


async def test_two_consumers_never_claim_the_same_entry(redis_client):
    """The core double-crawl-prevention guarantee: with one entry in the
    stream, only one of two competing consumers should ever see it.
    """
    producer = make_frontier(redis_client, "producer")
    await producer.ensure_group()
    await producer.enqueue("https://example.com/only", "example.com", depth=0, page_id=1)

    consumer_a = make_frontier(redis_client, "worker-a")
    consumer_b = make_frontier(redis_client, "worker-b")

    claimed_a = await consumer_a.claim(count=1)
    claimed_b = await consumer_b.claim(count=1)

    total_claimed = len(claimed_a) + len(claimed_b)
    assert total_claimed == 1, "exactly one consumer should have claimed the single entry"


async def test_many_entries_are_partitioned_without_overlap_or_loss(redis_client):
    producer = make_frontier(redis_client, "producer")
    await producer.ensure_group()
    for i in range(20):
        await producer.enqueue(f"https://example.com/{i}", "example.com", depth=0, page_id=i)

    consumer_a = make_frontier(redis_client, "worker-a")
    consumer_b = make_frontier(redis_client, "worker-b")

    claimed_a = await consumer_a.claim(count=10)
    claimed_b = await consumer_b.claim(count=10)

    urls_a = {item.url for item in claimed_a}
    urls_b = {item.url for item in claimed_b}
    assert urls_a.isdisjoint(urls_b)
    assert len(urls_a) + len(urls_b) == 20


async def test_ack_removes_entry_from_pending(redis_client):
    frontier = make_frontier(redis_client, "worker-a")
    await frontier.ensure_group()
    await frontier.enqueue("https://example.com/a", "example.com", depth=0, page_id=1)

    items = await frontier.claim(count=1)
    assert await frontier.pending_count() == 1

    await frontier.ack(items[0].entry_id)
    assert await frontier.pending_count() == 0


async def test_crashed_worker_entry_is_not_reclaimed_before_visibility_timeout(redis_client):
    """A worker claims an item and 'crashes' (never acks). A second consumer
    with a long visibility timeout should NOT be able to claim it yet -- the
    entry must survive the crash window untouched.
    """
    frontier = make_frontier(redis_client, "worker-a", visibility_timeout_ms=60_000)
    await frontier.ensure_group()
    await frontier.enqueue("https://example.com/a", "example.com", depth=0, page_id=1)

    crashed_claim = await frontier.claim(count=1)
    assert len(crashed_claim) == 1  # worker-a claimed it, then "crashed" (no ack)

    rescuer = make_frontier(redis_client, "worker-b", visibility_timeout_ms=60_000)
    rescued = await rescuer.claim(count=1)
    assert rescued == [], "entry is still within its visibility timeout and must not be reclaimed yet"


async def test_crashed_worker_entry_is_reclaimed_after_visibility_timeout(redis_client):
    """Same setup, but the rescuer uses a visibility timeout of 0ms, i.e. any
    idle time at all qualifies for reclaim -- simulating that the timeout has
    elapsed without an actual sleep in the test.
    """
    frontier = make_frontier(redis_client, "worker-a", visibility_timeout_ms=60_000)
    await frontier.ensure_group()
    await frontier.enqueue("https://example.com/a", "example.com", depth=0, page_id=1)

    crashed_claim = await frontier.claim(count=1)
    assert len(crashed_claim) == 1

    rescuer = make_frontier(redis_client, "worker-b", visibility_timeout_ms=0)
    rescued = await rescuer.claim(count=1)
    assert len(rescued) == 1
    assert rescued[0].url == "https://example.com/a"
    assert rescued[0].page_id == 1

    # The rescued entry should now be ack-able by the rescuer, and pending
    # should drop to zero -- proving ownership transferred cleanly.
    await rescuer.ack(rescued[0].entry_id)
    assert await frontier.pending_count() == 0


async def test_requeue_acks_original_and_makes_item_claimable_again(redis_client):
    frontier = make_frontier(redis_client, "worker-a")
    await frontier.ensure_group()
    await frontier.enqueue("https://example.com/rate-limited", "example.com", depth=1, page_id=7, parent_id=3)

    items = await frontier.claim(count=1)
    original = items[0]

    await frontier.requeue(original)
    # Original delivery must be acknowledged (no longer pending)...
    assert await frontier.pending_count() == 0
    # ...and a fresh copy must be claimable.
    requeued = await frontier.claim(count=1)
    assert len(requeued) == 1
    assert requeued[0].url == original.url
    assert requeued[0].depth == 1
    assert requeued[0].page_id == 7
    assert requeued[0].parent_id == 3
    assert requeued[0].entry_id != original.entry_id


async def test_stream_length_reflects_enqueued_entries(redis_client):
    frontier = make_frontier(redis_client, "worker-a")
    await frontier.ensure_group()
    assert await frontier.stream_length() == 0
    await frontier.enqueue("https://example.com/a", "example.com", depth=0, page_id=1)
    await frontier.enqueue("https://example.com/b", "example.com", depth=0, page_id=2)
    assert await frontier.stream_length() == 2
