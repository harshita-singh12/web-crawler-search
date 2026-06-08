"""HTML -> (title, visible text, outgoing links) extraction.

Shared by the crawler (needs links to expand the frontier, and text to hash
for change detection) and the indexer (needs text to tokenize, and re-derives
links when recomputing PageRank from stored HTML).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bs4 import BeautifulSoup

_STRIP_TAGS = ("script", "style", "noscript", "svg", "template")
# Note: <head> is deliberately NOT in this list -- text extraction only reads
# soup.body (see extract_text_and_title below), so head content never leaks
# into the extracted text anyway, and stripping it would destroy the
# <title> tag needed for the title lookup just below.

# Structural chrome that carries no article content: standard HTML5 landmark
# tags plus the specific ids/classes MediaWiki (Wikipedia) and most
# documentation-site templates use for their nav/sidebar/header/footer. This
# is what keeps snippets and tokenization focused on actual article text
# instead of "Search Search Appearance Donate Create account Log in..." menu
# chrome that would otherwise appear near the top of every single page.
_STRIP_LANDMARK_TAGS = ("nav", "header", "footer", "aside")
_STRIP_IDS = {
    "mw-navigation",
    "mw-panel",
    "mw-page-base",
    "mw-head-base",
    "p-navigation",
    "vector-header-container",
    "vector-page-toolbar",
    "vector-sticky-header",
    "mw-indicators",
    "siteNotice",
    "catlinks",
    "vector-toc",
    "toc",
    "footer",
    "mw-footer",
}
_STRIP_CLASS_SUBSTRINGS = (
    "navbox",
    "vector-menu",
    "mw-editsection",
    "mw-jump-link",
    "noprint",
    "navigation-not-searchable",
)

MAX_ANCHOR_TEXT_LEN = 200
MAX_TITLE_LEN = 500


@dataclass
class Link:
    url: str
    anchor_text: str


@dataclass
class ExtractedPage:
    title: str | None
    text: str
    links: list[Link] = field(default_factory=list)


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        # lxml can choke on severely malformed markup in rare cases; fall
        # back to the stdlib parser rather than failing the whole page.
        return BeautifulSoup(html, "html.parser")


def _safe_decompose(tag) -> None:
    # bs4's Tag.decompose() sets self.attrs = None as part of tearing the
    # tag down, which doubles as a reliable "already decomposed" sentinel --
    # useful because find_all() snapshots are taken up front, so later
    # entries in that snapshot may be descendants of a tag we already
    # decomposed earlier in the same pass (nested <nav>s, a class-matched
    # element inside an id-matched one, etc).
    if getattr(tag, "attrs", None) is None:
        return
    tag.decompose()


def _strip_chrome(soup: BeautifulSoup) -> None:
    for tag_name in _STRIP_TAGS + _STRIP_LANDMARK_TAGS:
        for tag in soup.find_all(tag_name):
            _safe_decompose(tag)

    for element_id in _STRIP_IDS:
        tag = soup.find(id=element_id)
        if tag is not None:
            _safe_decompose(tag)

    for tag in soup.find_all(class_=True):
        if tag.attrs is None:
            continue
        classes = tag.attrs.get("class") or []
        if any(sub in cls for cls in classes for sub in _STRIP_CLASS_SUBSTRINGS):
            _safe_decompose(tag)


def extract_text_and_title(html: str) -> tuple[str | None, str]:
    soup = _make_soup(html)
    _strip_chrome(soup)

    title = None
    if soup.title and soup.title.string:
        title = " ".join(soup.title.string.split())[:MAX_TITLE_LEN]
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = " ".join(h1.get_text().split())[:MAX_TITLE_LEN]

    body = soup.body or soup
    text = " ".join(body.get_text(separator=" ").split())
    return title, text


def extract_links(html: str, base_url: str) -> list[Link]:
    from common.urls import normalize_url

    soup = _make_soup(html)
    links: list[Link] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = normalize_url(a["href"], base=base_url)
        if not href or href in seen:
            continue
        seen.add(href)
        anchor = " ".join(a.get_text().split())[:MAX_ANCHOR_TEXT_LEN]
        links.append(Link(url=href, anchor_text=anchor))
    return links


def extract(html: str, base_url: str) -> ExtractedPage:
    title, text = extract_text_and_title(html)
    links = extract_links(html, base_url)
    return ExtractedPage(title=title, text=text, links=links)
