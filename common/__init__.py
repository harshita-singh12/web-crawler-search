"""Shared library used by the crawler, indexer and API services.

Kept dependency-light (stdlib + bs4/lxml only) and free of any service-specific
I/O (no Redis/Postgres/MinIO clients here) so it can be unit tested in
isolation and imported by all three services without pulling in each other's
dependencies.
"""
