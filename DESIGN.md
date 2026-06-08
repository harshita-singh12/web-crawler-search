# DESIGN

This document is written before implementation, as required by the project spec. It covers the
three required design areas — URL frontier, database schema, inverted index — plus the safety
posture for the crawler, since this system crawls the live web.

## 0. Safety posture (read this first)

This is a portfolio/demo project, not a production crawler. Crawling the live web without care
can be disruptive to third-party sites and can get an IP blocked. The defaults are deliberately
conservative:

- **Seed list**: a small, fixed set of ~8 Wikipedia and public-documentation pages
  (`crawler/src/seeds.py`). No seed list is auto-discovered or user-suppliable via an
  unauthenticated API — changing it requires editing code/config, which is a deliberate friction
  point.
- **Max depth**: 2 hops from a seed by default (`MAX_DEPTH=2`).
- **Max pages**: hard cap of 200 pages per crawl run by default (`MAX_PAGES=200`), enforced
  centrally by the frontier so it holds even with many workers.
- **Politeness**: robots.txt is always fetched and obeyed (`Disallow`, `Crawl-delay`). If a site's
  robots.txt cannot be fetched, we fall back to a conservative default delay rather than treating
  the site as unrestricted.
- **Per-domain rate limit**: minimum 3 seconds between requests to the same domain by default
  (`DEFAULT_CRAWL_DELAY_SECONDS=3`), enforced via a shared Redis key so it holds across all
  worker processes, not just within one.
- **Concurrency cap**: default 4 crawler workers, each with a small per-process connection pool,
  so aggregate request rate stays low.
- **User-Agent**: identifies itself honestly (`wifey-search-bot/0.1`) with a contact URL in the
  string, and a distinct HTTP-1.1 `From` header is not sent (kept simple), but User-Agent alone is
  sufficient for site operators to identify and block the bot if they want to.
- **Scope**: same-domain-only expansion by default (`ALLOW_OFFSITE_LINKS=false`) — the crawler
  will not wander off the seed domains onto the wider web unless explicitly reconfigured.

All of the above are environment variables with safe defaults in `.env.example`; nothing here
requires code changes to run safely out of the box.

## 1. URL frontier design

**Goal:** many crawler worker processes pull URLs to fetch from a shared queue without two
workers ever fetching the same URL concurrently, and without losing a URL if a worker crashes
mid-fetch.

**Choice: Redis Streams with a consumer group.**

- A single stream `frontier:urls` holds pending work items. Each entry is a small JSON payload:
  `{"url", "domain", "depth", "parent_id"}`.
- All workers join the same consumer group `crawlers` (`XGROUP CREATE ... MKSTREAM`). Each worker
  has a unique consumer name (`worker-<hostname>-<pid>`).
- A worker claims work with `XREADGROUP GROUP crawlers <consumer> COUNT 1 BLOCK 5000 STREAMS
  frontier:urls >`. Redis guarantees that a given stream entry is delivered to **exactly one**
  consumer in the group — this is what prevents double-crawling, not application-level locking.
- After a successful fetch+store, the worker calls `XACK` to remove the entry from the group's
  Pending Entries List (PEL).
- **Fault tolerance / visibility timeout**: if a worker crashes after claiming but before
  `XACK`-ing, the entry sits in the PEL. A lightweight **reclaimer** (run inline by every worker,
  once per polling loop, so no extra process is needed) calls
  `XAUTOCLAIM frontier:urls crawlers <consumer> <min-idle-ms> 0-0 COUNT 50`, which atomically
  reassigns any entry idle longer than `VISIBILITY_TIMEOUT_MS` (default 5 minutes) to the caller
  and hands it back out for reprocessing. This is Redis's built-in equivalent of SQS's visibility
  timeout, so we don't hand-roll a lease table.
- **Dedup** (never enqueue the same normalized URL twice): a Redis-backed **Bloom filter**
  (`crawler/src/bloom.py`) is checked/set with `SETBIT` across `k` hash slots derived from
  `blake2b(url, salt=i)`. This is probabilistic (small false-positive rate → a URL is
  occasionally skipped as "already seen" when it wasn't — acceptable for this project) but O(1)
  and shared across all workers via Redis, unlike an in-memory set. It is checked before a URL is
  `XADD`-ed to the stream, and again defensively via a Postgres `UNIQUE(url)` constraint on
  `pages.url` (an `INSERT ... ON CONFLICT DO NOTHING`) as a second line of defense, since Bloom
  filters can false-positive but never false-negative in the other direction that would matter
  here (they never claim "not seen" for something actually seen, so we never *miss* a duplicate;
  we might occasionally *skip* a new URL, which is an acceptable tradeoff for a crawler).
- **Politeness / rate limiting** is enforced at claim-time, not enqueue-time: a worker that pops a
  URL for a domain still in its cooldown window puts the entry back (`XADD` a fresh copy + `XACK`
  the original) and moves on, rather than blocking the whole stream behind one slow domain. The
  distributed rate limiter (`crawler/src/rate_limiter.py`) uses a single Redis key per domain
  (`ratelimit:<domain>`) written with `SET key <expiry-timestamp> NX PX <delay_ms>`; this makes
  "is this domain free right now" an atomic check-and-set across every worker process, so no two
  workers can both think they're clear to hit the same domain in the same window.
- **Global page cap**: a Redis counter `crawl:pages_fetched` is atomically `INCR`-ed after each
  successful fetch; workers stop pulling new work once it reaches `MAX_PAGES`.

**Why not RabbitMQ/Kafka/SQS?** The spec explicitly allows Redis Streams as the simpler starting
point. It gives us consumer groups (competing consumers), acknowledgement, and automatic
reclaiming of abandoned work out of the box, which covers the fault-tolerance requirement without
a second infrastructure dependency — and this system already needs Redis for the bloom filter and
rate limiter, so it's zero marginal infra cost.

## 2. Database schema (PostgreSQL)

See `db/init.sql` for the executable version. Summary:

```
domains
  domain            TEXT PRIMARY KEY
  robots_txt        TEXT              -- raw robots.txt body, NULL if none/unfetched
  robots_fetched_at TIMESTAMPTZ
  crawl_delay_sec   DOUBLE PRECISION  -- from robots.txt Crawl-delay, else DEFAULT_CRAWL_DELAY_SECONDS
  disallow_all      BOOLEAN DEFAULT FALSE

pages
  id                BIGSERIAL PRIMARY KEY
  url               TEXT UNIQUE NOT NULL
  url_hash          TEXT NOT NULL      -- sha256(url), indexed, used for fast lookups
  domain            TEXT REFERENCES domains(domain)
  status            TEXT NOT NULL      -- 'pending' | 'in_progress' | 'crawled' | 'failed' | 'skipped'
  http_status       INT
  depth             INT NOT NULL DEFAULT 0
  title             TEXT
  content_hash      TEXT              -- sha256 of extracted text, used to skip re-indexing unchanged pages
  etag              TEXT              -- for conditional re-crawl
  last_modified     TEXT              -- raw header value, sent back as If-Modified-Since
  content_length    INT
  minio_key         TEXT              -- raw HTML location in object storage
  pagerank          DOUBLE PRECISION DEFAULT 0
  discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now()
  first_crawled_at  TIMESTAMPTZ
  last_crawled_at   TIMESTAMPTZ
  next_crawl_at     TIMESTAMPTZ       -- incremental re-crawl scheduling
  error             TEXT

links
  id                BIGSERIAL PRIMARY KEY
  src_page_id       BIGINT REFERENCES pages(id) ON DELETE CASCADE
  dst_url           TEXT NOT NULL
  dst_page_id       BIGINT REFERENCES pages(id) ON DELETE SET NULL
  anchor_text       TEXT
  UNIQUE(src_page_id, dst_url)

terms
  term              TEXT PRIMARY KEY
  doc_freq          INT NOT NULL DEFAULT 0     -- number of docs containing the term, for IDF

postings
  term              TEXT REFERENCES terms(term) ON DELETE CASCADE
  page_id           BIGINT REFERENCES pages(id) ON DELETE CASCADE
  tf                INT NOT NULL               -- raw term frequency in that doc
  PRIMARY KEY(term, page_id)
```

Notes:
- `pages.status` plus `next_crawl_at` is the source of truth for what still needs (re)crawling;
  the Redis stream only holds *in-flight* work items, so Postgres survives a full Redis flush.
- `links` rows are written for every discovered outlink regardless of whether the destination has
  been crawled yet (`dst_page_id` is nullable and back-filled once the destination is inserted).
  This table is what both the frontier-expansion step and the PageRank computation read from.
- Indexes: `pages(domain, status)` for frontier refill queries, `pages(next_crawl_at)` for
  re-crawl scheduling, `postings(page_id)` for deleting a doc's postings on re-index,
  `links(dst_url)` and `links(src_page_id)` for PageRank.

## 3. Inverted index design

**Choice: the inverted index lives in PostgreSQL itself** (`terms` + `postings` tables above),
rather than a separate Whoosh index or a hand-rolled on-disk file format.

Rationale:
- It's genuinely "disk-backed" (Postgres tables), which satisfies the spec's "disk-backed, or via
  Whoosh" either/or.
- It gets incremental updates for free via normal SQL transactions — re-indexing a page is
  `DELETE FROM postings WHERE page_id = ?` followed by re-inserting new postings and adjusting
  `terms.doc_freq`, all inside one transaction, so a crash mid-update can't corrupt the index.
  A hand-rolled file-based index would need its own compaction/merge logic to get the same
  property (this is essentially what Whoosh or Lucene segment merging exists to solve).
- It avoids a second query path/second client library in the API service — the API already talks
  to Postgres for page metadata, so ranking and metadata fetch can be done with joins in one
  round-trip.
- The tradeoff is raw query latency: a B-tree-indexed `postings(term)` lookup is slower than a
  purpose-built inverted-index file format under heavy load. That's an acceptable tradeoff at this
  project's scale (hundreds of documents, single-node Postgres); seeing "how to shard this" is
  addressed in `README.md`.

**Build/update process** (`indexer/src/index_builder.py`, run as its own service/process,
separate from the crawler workers):
1. Poll Postgres for pages with `status = 'crawled'` whose `content_hash` differs from what was
   last indexed (tracked via a `last_indexed_hash` column check) — this is what makes indexing
   *incremental*: unchanged pages are never re-tokenized.
2. Fetch the raw HTML from MinIO, extract visible text (`crawler`'s parser is reused as a
   library), tokenize (`indexer/src/tokenizer.py`: lowercase, Unicode-aware word splitting, stop
   word removal, light stemming via suffix-stripping).
3. Compute raw term frequencies for the doc, `DELETE` old postings for that page, insert new
   postings, and adjust `terms.doc_freq` (decrement for terms removed, increment for terms newly
   present, insert new terms at `doc_freq=1`).
4. Periodically (every `PAGERANK_INTERVAL_SECONDS`) recompute PageRank over the `links` table
   (`indexer/src/pagerank.py`, power iteration, damping 0.85, 20 iterations) and write
   `pages.pagerank`.

**Scoring at query time** (`api/src/search.py`):
- Classic TF-IDF: for each query term, `idf = ln(N / (1 + doc_freq))`; for each doc containing the
  term, `tf_weight = 1 + ln(tf)`. Doc score = `sum(tf_weight * idf)` over query terms present,
  cosine-normalized by an approximate doc-length factor stored per page (`sqrt(sum of tf^2)`,
  computed once during indexing and cached in `pages.tfidf_norm`).
- Final rank score = `tfidf_score * (1 + PAGERANK_BOOST_ALPHA * pagerank)`, so link authority
  nudges ranking without letting a single highly-linked-but-irrelevant page dominate results.
- Query path: normalize+tokenize the query the same way as documents, `SELECT` postings for the
  query terms (a handful of indexed lookups), aggregate scores in Python, sort, return top N with
  snippets (see below).

**Snippets**: generated at query time, not stored, by re-fetching the raw HTML from MinIO for the
top-N candidates only, stripping tags, and returning the sentence/window around the first query
term match (`api/src/search.py::make_snippet`). Doing this lazily for only the top page or two of
results keeps it cheap.
