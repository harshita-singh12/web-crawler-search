"""A single conditional-GET page fetch, wrapping aiohttp with the headers
needed for incremental re-crawling (If-None-Match / If-Modified-Since) and a
consistent timeout/size policy.
"""
from __future__ import annotations

from dataclasses import dataclass

import aiohttp

MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5MB: skip anything larger than this to
# keep the demo crawl fast and avoid ever trying to tokenize e.g. a
# mis-served binary file.


@dataclass
class FetchResult:
    status: int
    url: str  # final URL after redirects
    html: str | None
    etag: str | None
    last_modified: str | None
    content_length: int | None
    not_modified: bool
    error: str | None = None


async def fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    user_agent: str,
    timeout_seconds: float,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    headers = {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with session.get(url, headers=headers, timeout=timeout, allow_redirects=True) as resp:
            if resp.status == 304:
                return FetchResult(
                    status=304,
                    url=str(resp.url),
                    html=None,
                    etag=resp.headers.get("ETag", etag),
                    last_modified=resp.headers.get("Last-Modified", last_modified),
                    content_length=0,
                    not_modified=True,
                )

            content_type = resp.headers.get("Content-Type", "")
            if resp.status == 200 and "text/html" not in content_type and "xhtml" not in content_type:
                return FetchResult(
                    status=resp.status,
                    url=str(resp.url),
                    html=None,
                    etag=resp.headers.get("ETag"),
                    last_modified=resp.headers.get("Last-Modified"),
                    content_length=None,
                    not_modified=False,
                    error=f"skipped non-HTML content-type: {content_type or 'unknown'}",
                )

            raw = await resp.content.read(MAX_CONTENT_BYTES + 1)
            truncated = len(raw) > MAX_CONTENT_BYTES
            if truncated:
                raw = raw[:MAX_CONTENT_BYTES]
            try:
                encoding = "utf-8" if truncated else resp.get_encoding()
            except (LookupError, RuntimeError, ValueError):
                encoding = "utf-8"
            html = raw.decode(encoding, errors="replace")

            return FetchResult(
                status=resp.status,
                url=str(resp.url),
                html=html if resp.status == 200 else None,
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
                content_length=len(raw),
                not_modified=False,
                error=None if resp.status == 200 else f"HTTP {resp.status}",
            )
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        return FetchResult(
            status=0,
            url=url,
            html=None,
            etag=None,
            last_modified=None,
            content_length=None,
            not_modified=False,
            error=str(exc),
        )
