"""URL normalization and domain extraction helpers."""
from __future__ import annotations

from urllib.parse import urljoin, urlparse, urlunparse

_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Resolve `url` against `base` (if given), strip the fragment, drop a
    redundant default port, lowercase the scheme/host, and return None for
    anything that isn't a fetchable http(s) URL (mailto:, javascript:, empty
    hrefs, etc.).
    """
    if not url:
        return None
    url = url.strip()
    if not url or url.startswith("#"):
        return None
    resolved = urljoin(base, url) if base else url
    parsed = urlparse(resolved)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        return None
    if not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    port = parsed.port
    netloc = host if (port is None or port == _DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"
    path = parsed.path or "/"
    # Collapse a trailing "//" -> "/" but otherwise leave path as-is; query
    # string is preserved since different query params generally address
    # different content.
    cleaned = urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))
    return cleaned


def get_domain(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    return parsed.hostname.lower()
