# Wayfind Search

A small, distributed web crawler and search engine: async Python crawler workers, a
Redis-Streams URL frontier, PostgreSQL metadata store, a Postgres-backed inverted index with
TF-IDF + PageRank ranking, raw-page storage in MinIO, a FastAPI search API, a React search UI, and
Prometheus/Grafana monitoring.

Design rationale for the frontier, schema, and inverted index lives alongside the code, not in a
separate document -- see the "Frontier design" section below, and the module docstrings in
`crawler/src/`, `indexer/src/`, and `db/init.sql`.

**Safety note**: this crawls the live web. It's hard-configured to crawl a small, fixed list of
~8 Wikipedia/documentation pages, to a max depth of 2, capped at 200 pages, with a minimum 3s
per-domain delay and full robots.txt compliance (see `crawler/src/config.py` and
`crawler/src/seeds.py`). Don't repoint this at arbitrary sites without re-checking those defaults.

## Architecture

```
                     ┌─────────────┐
   seed URLs ──────▶ │   crawler   │◀──────────────┐  (N replicas, asyncio tasks)
                     │  (workers)  │                │
                     └──────┬──────┘                │
                             │ XADD/XREADGROUP/XACK   │ Bloom filter (dedup)
                             ▼                        │ distributed rate limiter
                     ┌──────────────┐                │ robots.txt cache
                     │ Redis Streams │◀──────────────┘
                     │  (frontier)   │
                     └──────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
 ┌─────────────┐     ┌──────────────┐     ┌──────────────┐
 │  PostgreSQL  │     │    MinIO     │     │  (crawler     │
 │ pages/links/ │◀───▶│  raw HTML    │     │  metrics :9100)│
 │ terms/postings│     │  storage    │     └──────────────┘
 └──────┬───────┘     └──────┬───────┘
        │                    │
        ▼                    ▼
 ┌─────────────────────────────────┐
 │            indexer               │  separate process/container:
 │  tokenize → postings → PageRank  │  polls Postgres for unindexed pages,
 └─────────────────────────────────┘  recomputes PageRank periodically

        ┌─────────────┐        ┌──────────────┐
        │  FastAPI     │◀──────│  React UI     │
        │  /search     │       │ (search bar)  │
        └──────────────┘       └──────────────┘

            Prometheus scrapes crawler:9100, indexer:9101, api:8000/metrics
            Grafana dashboard auto-provisioned from monitoring/grafana/
```

Each of crawler / indexer / api / frontend is a separate service with its own Dockerfile,
composed together with `docker-compose.yml`. `common/` is a small shared Python library
(tokenizer, TF-IDF math, Bloom filter, URL/HTML helpers) imported by crawler, indexer and api --
it has no I/O of its own, which is what makes it unit-testable without any infrastructure.

Two infra choices worth calling out: the inverted index (`terms`/`postings`) lives in Postgres
itself rather than a separate Whoosh index, for free transactional incremental updates and one
fewer client library/query path in the API service; and the frontier queue is Redis Streams
rather than RabbitMQ, since it's the simpler starting point that still gives consumer groups
(competing consumers), acknowledgement, and automatic reclaiming of abandoned work out of the
box -- and this system already needs Redis for the bloom filter and rate limiter, so it's zero
marginal infra cost.

## Running it

### With Docker Compose (recommended)

```bash
cd 02-web-crawler-search
cp .env.example .env        # edit if you want non-default ports/credentials
docker compose up --build
```

Then:
- Frontend/search UI: http://localhost:3000
- API directly: http://localhost:8000/search?q=search+engine
- API docs (FastAPI autodocs): http://localhost:8000/docs
- Grafana: http://localhost:3001 (anonymous viewer access enabled; admin/admin by default)
- Prometheus: http://localhost:9090
- MinIO console: http://localhost:9001

The crawler starts crawling the seed list immediately on boot. With the default settings
(depth 2, 200-page cap, 4 concurrent tasks, 3s/domain delay) a full crawl of the seed set finishes
in roughly one to a few minutes depending on Wikipedia's response times. The indexer polls for
newly-crawled pages every few seconds, so search results start appearing well before the crawl
finishes. `docker compose logs -f crawler indexer` to watch progress.

To stop and wipe all data (start a truly fresh crawl): `docker compose down -v`.

### Without Docker

You need Python 3.12+, Node 20+, and running Postgres/Redis/MinIO instances (the quickest way to
get those three without Docker Compose orchestrating everything is still
`docker compose up postgres redis minio`, then run the four application services directly on the
host):

```bash
# 1. Apply the schema once (or let docker's postgres init do it automatically)
psql "$POSTGRES_DSN" -f db/init.sql

# 2. Crawler (from repo root, so `common` is importable)
cd crawler && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd .. && PYTHONPATH=. python -m crawler.src.main

# 3. Indexer (separate process, separate terminal)
cd indexer && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd .. && PYTHONPATH=. python -m indexer.src.main

# 4. API (separate process, separate terminal)
cd api && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd .. && PYTHONPATH=. uvicorn api.src.main:app --reload --port 8000

# 5. Frontend
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

All configuration is environment variables (see `.env.example`); each service also reads a local
`.env` via `python-dotenv` if present.

### Running the tests

```bash
# each service's tests are self-contained and infra-free (no live DB/Redis needed --
# see "Testing philosophy" below)
cd common  && pip install -r ../crawler/requirements.txt && python -m pytest -q
cd crawler && pip install -r requirements.txt && python -m pytest -q
cd indexer && pip install -r requirements.txt && python -m pytest -q
cd api     && pip install -r requirements.txt && python -m pytest -q
```

Or, without touching your host Python at all, via Docker (what was actually used to verify this
project -- no local Python/Node was available in the build environment):

```bash
for svc in common crawler indexer api; do
  docker run --rm -v "$(pwd)":/app -w /app/$svc python:3.12-slim \
    bash -c "pip install --quiet -r ../crawler/requirements.txt 2>&1 | tail -3 && python -m pytest -q"
done
```

**Testing philosophy**: the required units under test -- the tokenizer/TF-IDF scorer, the Bloom
filter, the frontier's claim/reclaim logic, and the retry-with-backoff policy -- are all tested
against fast, in-process fakes (`InMemoryBitBackend` for the Bloom filter, `fakeredis` for the
Redis Streams frontier, an in-memory fake asyncpg pool for the retry integration test) rather than
live Postgres/Redis containers. This was a deliberate choice: it keeps the suite at sub-second
runtime and makes it runnable with zero infrastructure, while still exercising the real
hashing/claiming/retry algorithms (fakeredis implements actual XADD/XREADGROUP/XAUTOCLAIM
semantics, not a mock, and the retry tests drive the real `CrawlerWorker`/`PageStore` code against
a scripted flaky upstream). End-to-end correctness against the *real* Postgres/Redis/MinIO is
instead verified by actually running `docker compose up` and issuing a real crawl + search (see
below) -- that's a deliberate integration-test/unit-test split, not a gap.

## Frontier design (summary)

Redis Streams + a consumer group (`XREADGROUP`) gives "exactly one worker processes this URL"
for free -- that's Redis's own delivery guarantee, not something hand-rolled. Crash recovery is
`XAUTOCLAIM`: any worker's periodic poll also reclaims entries that have been claimed-but-not-ACKed
longer than `VISIBILITY_TIMEOUT_MS` (5 minutes by default), which is the same "visibility timeout"
concept SQS uses. Duplicate URLs are filtered before they're even enqueued, via a Redis-backed
Bloom filter shared by all workers, with a Postgres `UNIQUE(url)` constraint as a second,
authoritative line of defense against the Bloom filter's small false-positive rate.

Politeness/rate limiting is enforced at claim-time, not enqueue-time: rather than never enqueuing a
URL until its domain is free (which would require a per-domain scheduler), a worker that claims a
URL for a still-cooling-down domain puts it back on the stream and moves on. This is simpler to
implement correctly with Redis Streams' primitives, at the cost of a little wasted claim/requeue
churn under heavy contention for one domain -- acceptable given the crawl is intentionally small
and slow by default. The distributed rate limiter itself (`crawler/src/rate_limiter.py`) uses a
single Redis key per domain written with `SET key <expiry> NX PX <delay_ms>`, so "is this domain
free right now" is an atomic check-and-set across every worker process.

## Re-crawl scheduling

Every successfully crawled page gets a `next_crawl_at = now() + RECRAWL_INTERVAL_HOURS` (24h by
default) written to `pages.next_crawl_at`, along with the response's `ETag`/`Last-Modified`
headers. A background loop in each crawler worker (`_recrawl_scheduler_loop` in
`crawler/src/worker.py`) polls Postgres every 60s for pages where `next_crawl_at <= now()` and
re-enqueues them into the same frontier stream as ordinary work items. When that re-crawl fetch
happens, the stored `ETag`/`Last-Modified` are sent as `If-None-Match`/`If-Modified-Since`; a `304
Not Modified` response short-circuits straight to rescheduling the next check without re-parsing,
re-storing, or re-indexing anything the site itself says hasn't changed. This means re-crawl
frequency is decoupled from indexing cost -- a page that never changes costs one cheap conditional
GET per interval, forever.

## Retry policy

A genuine HTTP failure (404/500/timeout -- as opposed to a crashed *worker*, which is a separate
concern already handled by the frontier's visibility-timeout/`XAUTOCLAIM` mechanism above) is
retried with capped exponential backoff instead of being marked `failed` immediately. Each `pages`
row tracks `retry_count` and `next_retry_at`; on a failure, `PageStore.record_failure`
(`crawler/src/storage.py`) increments `retry_count` and, while it's within `MAX_RETRIES` (3 by
default), schedules the next attempt at `RETRY_BASE_DELAY_SECONDS * 2**(retry_count - 1)` seconds
out, capped at `RETRY_MAX_DELAY_SECONDS` (5 minutes by default) -- so retry 1 waits ~5s, retry 2
~10s, retry 3 ~20s, and so on, without ever hammering a broken URL at full speed. A dedicated
`_retry_scheduler_loop` in `crawler/src/worker.py` polls for pages whose backoff window has
elapsed and re-enqueues them onto the same frontier stream, the same "not-before timestamp the
claim path respects" pattern re-crawl scheduling already uses above. Only once `retry_count`
exceeds `MAX_RETRIES` is a page permanently marked `failed`. All three knobs
(`MAX_RETRIES`, `RETRY_BASE_DELAY_SECONDS`, `RETRY_MAX_DELAY_SECONDS`) are environment variables;
see `crawler/src/config.py`.

## Sharding the inverted index

The current design keeps `terms`/`postings` as ordinary Postgres tables on the same node as the
rest of the metadata, which is the right call at this project's scale (hundreds of documents,
single-node Postgres). If the corpus outgrew a single node, here's how I'd shard it:

1. **Partition by term, not by document.** The query path is "look up postings for a handful of
   query terms", so a term-sharded index lets a query only fan out to the shards that actually
   hold the query's terms, instead of every shard for every query (which is what document-sharding
   would force, since any shard might hold any term). Shard key = `hash(term) % num_shards`, with
   each shard being its own Postgres instance (or, at real scale, a purpose-built index segment
   store) holding a disjoint slice of the `terms`/`postings` tables.
2. **A stateless query router (the current API service, extended)** fans a query out to only the
   shards owning its query terms, in parallel, and merges the per-shard partial postings before
   running the same `rank_pages` scoring logic (`api/src/ranking.py`) it uses today -- that
   function is already shard-agnostic since it just takes postings/doc_freqs as plain dicts
   regardless of where they came from.
3. **`doc_freq` and total-doc-count become the tricky part**, since IDF needs a *global* document
   frequency, not a per-shard one. Two standard fixes: (a) maintain a small separate
   "term stats" service/table that every shard's indexer updates transactionally (an extra hop,
   but `doc_freq` updates are rare relative to reads), or (b) accept approximate IDF computed from
   each shard's local stats scaled by shard count, which is what several production distributed
   search systems (e.g. Elasticsearch's default "distributed IDF" behavior) actually do in
   practice, trading a small ranking approximation for not needing global coordination.
4. **Page metadata (`pages`, `links`) would shard by `page_id % num_shards`** instead (a
   completely different axis from the term shards above) since it's looked up by page id, not by
   term, once the top-N ranked ids are known -- fetching titles/snippets for a small result set is
   cheap to fan out to whichever shards own those ids.
5. **The crawler/indexer side is embarrassingly parallel already**: crawler workers are stateless
   and horizontally scaled today (`NUM_WORKER_TASKS` per process, and multiple `crawler` container
   replicas via `docker compose up --scale crawler=N`); the indexer would need to become
   shard-aware (route a page's postings writes to `hash(term) % num_shards` per term while
   building that page's postings), but each individual page's indexing work is still independent
   of every other page's, so indexer replicas also just need `page_id`-based work partitioning
   (e.g. via `pg_advisory_lock` or, better, its own consumer-group stream mirroring the frontier's
   pattern) to run in parallel safely.
6. In short: the `common/tfidf.py` and `api/src/ranking.py` scoring math doesn't change at all
   under sharding -- only *where the postings/doc_freq dicts it operates on come from* changes,
   from "one SQL query" to "fan-out + merge across shards". That separation (pure scoring function
   vs. data-fetching) is exactly why `ranking.py` was written to take plain dicts instead of a
   database connection.

## Project layout

```
common/     shared, dependency-light Python library (tokenizer, TF-IDF, Bloom filter, URL/HTML
            helpers) -- imported by crawler, indexer and api; has its own unit tests.
crawler/    async crawler workers (Dockerfile, requirements.txt, src/, tests/)
indexer/    inverted-index builder + PageRank (separate process/container from the crawler)
api/        FastAPI search endpoint
frontend/   Vite + React + TypeScript search UI
db/         init.sql schema, applied automatically by the postgres container
monitoring/ prometheus.yml + Grafana provisioning (datasource + dashboard, auto-loaded)
```
