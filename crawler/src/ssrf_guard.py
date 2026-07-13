"""Guards the crawler's outbound HTTP connections against SSRF: a crawled
page's own links (or a malicious/compromised redirect target) could point at
an internal service or a cloud metadata endpoint (e.g. 169.254.169.254)
rather than the public web.

Enforced at DNS-resolution time via a custom aiohttp resolver, since that's
the one choke point every connection the crawler's shared `ClientSession`
makes goes through -- including redirects `fetch_page` follows
(`allow_redirects=True`) and the separate robots.txt fetch in `robots.py`,
not just the first hop built directly from a crawled link. Domain-based
filtering (`ALLOW_OFFSITE_LINKS`) happens one layer up in `worker.py` and
only looks at the URL string, so it can't catch a same-domain URL that
resolves (or redirects) to a private address.
"""
from __future__ import annotations

import ipaddress
import socket

from aiohttp.resolver import ThreadedResolver


def is_disallowed_address(ip_str: str) -> bool:
    """True if `ip_str` is not a routable public address (private/loopback/
    link-local/reserved/multicast/unspecified) -- i.e. not something the
    crawler should ever be allowed to connect to.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable -> treat as untrusted, fail closed
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


class SSRFSafeResolver(ThreadedResolver):
    """Resolves hostnames exactly like aiohttp's default resolver, then drops
    any resolved address that isn't a routable public address. If every
    address for a host is disallowed, resolution fails closed (raises
    OSError) rather than silently connecting to whatever came back.
    """

    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await super().resolve(host, port, family)
        safe = [entry for entry in results if not is_disallowed_address(entry["host"])]
        if not safe:
            raise OSError(
                f"refusing to connect to {host!r}: resolves only to disallowed "
                "(internal/private) addresses"
            )
        return safe
