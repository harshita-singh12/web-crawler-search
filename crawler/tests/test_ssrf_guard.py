"""Tests for the SSRF guard: DNS results pointing at private/internal
addresses must be filtered out (or, if every address for a host is
disallowed, resolution must fail closed) instead of letting the crawler
connect to them.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.ssrf_guard import SSRFSafeResolver, is_disallowed_address


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "169.254.169.254",  # link-local / cloud metadata endpoint
        "10.0.0.5",  # RFC1918 private
        "172.16.0.1",  # RFC1918 private
        "192.168.1.1",  # RFC1918 private
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 unique local (private)
        "not-an-ip",  # unparseable -> fail closed
    ],
)
def test_disallows_internal_and_unparseable_addresses(ip: str) -> None:
    assert is_disallowed_address(ip) is True


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"])
def test_allows_ordinary_public_addresses(ip: str) -> None:
    assert is_disallowed_address(ip) is False


def _fake_result(host: str) -> dict:
    return {"hostname": "example.com", "host": host, "port": 443, "family": 2, "proto": 0, "flags": 0}


async def test_resolver_filters_out_private_addresses_mixed_with_public() -> None:
    resolver = SSRFSafeResolver()
    with patch(
        "aiohttp.resolver.ThreadedResolver.resolve",
        new=AsyncMock(return_value=[_fake_result("10.0.0.1"), _fake_result("93.184.216.34")]),
    ):
        results = await resolver.resolve("example.com")
    assert len(results) == 1
    assert results[0]["host"] == "93.184.216.34"


async def test_resolver_fails_closed_when_every_address_is_private() -> None:
    resolver = SSRFSafeResolver()
    with patch(
        "aiohttp.resolver.ThreadedResolver.resolve",
        new=AsyncMock(return_value=[_fake_result("169.254.169.254")]),
    ):
        with pytest.raises(OSError):
            await resolver.resolve("metadata.internal")
