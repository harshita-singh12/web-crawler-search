"""The crawl's seed list.

Deliberately small, fixed, and thematically relevant (pages about crawling,
search and information retrieval) so the demo crawl is self-describing.
Changing this list is an explicit code change, not something reachable via
any API -- see DESIGN.md section 0 for why that friction is intentional.
"""

SEED_URLS: list[str] = [
    "https://en.wikipedia.org/wiki/Web_crawler",
    "https://en.wikipedia.org/wiki/Search_engine",
    "https://en.wikipedia.org/wiki/Information_retrieval",
    "https://en.wikipedia.org/wiki/PageRank",
    "https://en.wikipedia.org/wiki/Inverted_index",
    "https://en.wikipedia.org/wiki/Tf%E2%80%93idf",
    "https://en.wikipedia.org/wiki/Robots_exclusion_standard",
    "https://en.wikipedia.org/wiki/Bloom_filter",
]
