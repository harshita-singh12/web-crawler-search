#  web crawler + search engine

A small, distributed web crawler and search engine: async Python crawler workers, a
Redis-Streams URL frontier, PostgreSQL metadata store, a Postgres-backed inverted index with
TF-IDF + PageRank ranking, raw-page storage in MinIO, a FastAPI search API, a React search UI, and
Prometheus/Grafana monitoring.

Full design rationale (frontier claiming, DB schema, inverted index structure) is in
[`DESIGN.md`](./DESIGN.md) -- read that first if you want the "why", not just the "what".

**Safety note**: this crawls the live web. It's hard-configured to crawl a small, fixed list of
~8 Wikipedia/documentation pages, to a max depth of 2, capped at 200 pages, with a minimum 3s
per-domain delay and full robots.txt compliance. See `DESIGN.md` section 0 for the full rationale.
Don't repoint this at arbitrary sites without re-reading that section.

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
filter, and the frontier's claim/reclaim logic -- are all tested against fast, in-process fakes
(`InMemoryBitBackend` for the Bloom filter, `fakeredis` for the Redis Streams frontier) rather
than live Postgres/Redis containers. This was a deliberate choice: it keeps the suite at
sub-second runtime and makes it runnable with zero infrastructure, while still exercising the real
hashing/claiming algorithms (fakeredis implements actual XADD/XREADGROUP/XAUTOCLAIM semantics, not
a mock). End-to-end correctness against the *real* Postgres/Redis/MinIO is instead verified by
actually running `docker compose up` and issuing a real crawl + search (see below) -- that's a
deliberate integration-test/unit-test split, not a gap.

## Frontier design (summary)

Redis Streams + a consumer group (`XREADGROUP`) gives "exactly one worker processes this URL"
for free -- that's Redis's own delivery guarantee, not something hand-rolled. Crash recovery is
`XAUTOCLAIM`: any worker's periodic poll also reclaims entries that have been claimed-but-not-ACKed
longer than `VISIBILITY_TIMEOUT_MS` (5 minutes by default), which is the same "visibility timeout"
concept SQS uses. Duplicate URLs are filtered before they're even enqueued, via a Redis-backed
Bloom filter shared by all workers, with a Postgres `UNIQUE(url)` constraint as a second,
authoritative line of defense against the Bloom filter's small false-positive rate. Full detail,
including the distributed per-domain rate limiter and how robots.txt is respected, is in
[`DESIGN.md`](./DESIGN.md#1-url-frontier-design).

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

## Sharding the inverted index

The current design keeps `terms`/`postings` as ordinary Postgres tables on the same node as the
rest of the metadata (see `DESIGN.md` section 3 for why that was the right call at this project's
scale). If the corpus outgrew a single node, here's how I'd shard it:

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
2. **Page metadata (`pages`, `links`) would shard by `page_id % num_shards`** instead (a
   completely different axis from the term shards above) since it's looked up by page id, not by
   term, once the top-N ranked ids are known -- fetching titles/snippets for a small result set is
   cheap to fan out to whichever shards own those ids.
3. **The crawler/indexer side is embarrassingly parallel already**: crawler workers are stateless
   and horizontally scaled today (`NUM_WORKER_TASKS` per process, and multiple `crawler` container
   replicas via `docker compose up --scale crawler=N`); the indexer would need to become
   shard-aware (route a page's postings writes to `hash(term) % num_shards` per term while
   building that page's postings), but each individual page's indexing work is still independent
   of every other page's, so indexer replicas also just need `page_id`-based work partitioning
   (e.g. via `pg_advisory_lock` or, better, its own consumer-group stream mirroring the frontier's
   pattern) to run in parallel safely.
4. In short: the `common/tfidf.py` and `api/src/ranking.py` scoring math doesn't change at all
   under sharding -- only *where the postings/doc_freq dicts it operates on come from* changes,
   from "one SQL query" to "fan-out + merge across shards". That separation (pure scoring function
   vs. data-fetching) is exactly why `ranking.py` was written to take plain dicts instead of a
   database connection.

## Deviations from the original spec, and why

- **Inverted index storage**: built as Postgres tables (`terms`/`postings`) rather than Whoosh.
  The spec explicitly allows either. Rationale in `DESIGN.md` section 3 -- mainly: free
  transactional incremental updates, and one fewer client library/query path in the API service.
- **Queue**: Redis Streams, as the spec's own suggested simpler-than-RabbitMQ starting point.
- **Frontier claim-time rate limiting**: rather than never enqueuing a URL until its domain is free
  (which would require a per-domain scheduler), a worker that claims a URL for a still-cooling-down
  domain puts it back on the stream and moves on. Simpler to implement correctly with Redis
  Streams' primitives, at the cost of a little wasted claim/requeue churn under heavy contention
  for one domain -- acceptable given the crawl is intentionally small and slow by default.
- **Retries for real HTTP failures** (a genuine 404/500/timeout, as opposed to a crashed worker):
  a page is marked `failed` and not automatically retried within the same run. The frontier's
  crash-recovery (visibility timeout) mechanism is about *worker* failures, not *page* failures --
  conflating the two would risk hammering a consistently-broken URL. This is a reasonable
  simplification for a portfolio project; a production system would add capped exponential
  backoff retries with a separate `retry_count` column.
- **No package-lock.json committed** for the frontend: the sandbox this was built in has no local
  Node/npm, so `npm install` (not `npm ci`) is used in the frontend Dockerfile. Fine for a
  from-scratch build; a real project would commit the lockfile the first time `npm install` runs
  and switch to `npm ci` for reproducible builds after that.
- **No secrets are fabricated anywhere.** `.env.example` uses obviously-placeholder values
  (`changeme_local_dev_only`, `minioadmin`), `.env` itself is gitignored, and there is no code path
  that requires a real external API key (no third-party LLM/API calls anywhere in this project).

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
DESIGN.md   frontier / schema / inverted-index design, written before implementation
```
