"""robots.txt fetching and parsing, with a per-process cache so we don't
re-fetch robots.txt on every single page from the same domain.

Uses the stdlib's RobotFileParser for the actual matching logic (battle
tested, handles Allow/Disallow precedence correctly) but does the network
fetch ourselves via aiohttp so it participates in the same async event loop
and timeout/retry policy as page fetches.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class RobotRules:
    parser: RobotFileParser
    crawl_delay: float
    disallow_all: bool
    fetched_at: float
    raw_text: str | None


class RobotsChecker:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        user_agent: str,
        default_delay: float,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._session = session
        self._user_agent = user_agent
        self._default_delay = default_delay
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._cache: dict[str, RobotRules] = {}

    async def _fetch(self, domain: str) -> RobotRules:
        url = f"https://{domain}/robots.txt"
        raw_text: str | None = None
        disallow_all = False
        try:
            async with self._session.get(
                url, timeout=self._timeout, headers={"User-Agent": self._user_agent}
            ) as resp:
                if resp.status == 200:
                    raw_text = await resp.text(errors="replace")
                elif resp.status in (401, 403):
                    # Convention: treat auth-gated robots.txt as "disallow
                    # everything", per the robots.txt spec's own guidance.
                    disallow_all = True
                # 404 and other statuses -> no robots.txt -> allow-all, but
                # we still apply the conservative default crawl delay below.
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            logger.warning("robots.txt fetch failed for %s: %s (defaulting to conservative delay)", domain, exc)

        parser = RobotFileParser()
        parser.set_url(url)
        if raw_text is not None:
            parser.parse(raw_text.splitlines())
        else:
            # No body to parse => RobotFileParser.can_fetch defaults to True
            # (allow), which is correct for a missing robots.txt.
            parser.parse([])

        crawl_delay = parser.crawl_delay(self._user_agent)
        if crawl_delay is None:
            crawl_delay = self._default_delay
        else:
            crawl_delay = max(float(crawl_delay), self._default_delay)

        return RobotRules(
            parser=parser,
            crawl_delay=crawl_delay,
            disallow_all=disallow_all,
            fetched_at=time.time(),
            raw_text=raw_text,
        )

    async def get_rules(self, domain: str) -> RobotRules:
        cached = self._cache.get(domain)
        if cached is not None:
            return cached
        rules = await self._fetch(domain)
        self._cache[domain] = rules
        return rules

    async def can_fetch(self, url: str, domain: str) -> bool:
        rules = await self.get_rules(domain)
        if rules.disallow_all:
            return False
        return rules.parser.can_fetch(self._user_agent, url)

    async def crawl_delay(self, domain: str) -> float:
        rules = await self.get_rules(domain)
        return rules.crawl_delay
