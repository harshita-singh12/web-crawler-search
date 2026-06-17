-- Schema for the distributed web crawler + search engine.
-- Applied automatically by the postgres container on first boot (mounted into
-- /docker-entrypoint-initdb.d/).

CREATE TABLE IF NOT EXISTS domains (
    domain            TEXT PRIMARY KEY,
    robots_txt        TEXT,
    robots_fetched_at TIMESTAMPTZ,
    crawl_delay_sec   DOUBLE PRECISION NOT NULL DEFAULT 3.0,
    disallow_all      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS pages (
    id                BIGSERIAL PRIMARY KEY,
    url               TEXT NOT NULL UNIQUE,
    url_hash          TEXT NOT NULL,
    domain            TEXT REFERENCES domains(domain),
    status            TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'in_progress', 'crawled', 'failed', 'skipped', 'not_modified')),
    http_status       INT,
    depth             INT NOT NULL DEFAULT 0,
    title             TEXT,
    content_hash      TEXT,
    last_indexed_hash TEXT,
    etag              TEXT,
    last_modified     TEXT,
    content_length    INT,
    minio_key         TEXT,
    tfidf_norm        DOUBLE PRECISION,
    pagerank          DOUBLE PRECISION NOT NULL DEFAULT 0,
    discovered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_crawled_at  TIMESTAMPTZ,
    last_crawled_at   TIMESTAMPTZ,
    next_crawl_at     TIMESTAMPTZ,
    error             TEXT,
    retry_count       INT NOT NULL DEFAULT 0,     -- genuine HTTP failures (404/500/timeout) on this page
    next_retry_at     TIMESTAMPTZ                 -- capped-exponential-backoff "not before" for the next retry
);

CREATE INDEX IF NOT EXISTS idx_pages_domain_status ON pages(domain, status);
CREATE INDEX IF NOT EXISTS idx_pages_next_crawl_at ON pages(next_crawl_at);
CREATE INDEX IF NOT EXISTS idx_pages_next_retry_at ON pages(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_pages_url_hash ON pages(url_hash);
CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status);

CREATE TABLE IF NOT EXISTS links (
    id                BIGSERIAL PRIMARY KEY,
    src_page_id       BIGINT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    dst_url           TEXT NOT NULL,
    dst_page_id       BIGINT REFERENCES pages(id) ON DELETE SET NULL,
    anchor_text       TEXT,
    UNIQUE(src_page_id, dst_url)
);

CREATE INDEX IF NOT EXISTS idx_links_dst_url ON links(dst_url);
CREATE INDEX IF NOT EXISTS idx_links_src_page_id ON links(src_page_id);
CREATE INDEX IF NOT EXISTS idx_links_dst_page_id ON links(dst_page_id);

CREATE TABLE IF NOT EXISTS terms (
    term              TEXT PRIMARY KEY,
    doc_freq          INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS postings (
    term              TEXT NOT NULL REFERENCES terms(term) ON DELETE CASCADE,
    page_id           BIGINT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    tf                INT NOT NULL,
    PRIMARY KEY(term, page_id)
);

CREATE INDEX IF NOT EXISTS idx_postings_page_id ON postings(page_id);

-- Small operational table so the API/dashboards can report index-wide stats
-- without doing a full COUNT(*) scan on every request.
CREATE TABLE IF NOT EXISTS index_stats (
    key               TEXT PRIMARY KEY,
    value             DOUBLE PRECISION NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO index_stats(key, value) VALUES ('total_docs', 0)
    ON CONFLICT (key) DO NOTHING;
