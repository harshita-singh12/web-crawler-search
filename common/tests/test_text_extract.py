from common.text_extract import extract, extract_text_and_title


def test_extracts_title_from_title_tag():
    html = "<html><head><title>My Page</title></head><body><p>hello</p></body></html>"
    title, text = extract_text_and_title(html)
    assert title == "My Page"
    assert "hello" in text


def test_falls_back_to_h1_when_no_title_tag():
    html = "<html><body><h1>Fallback Heading</h1><p>content</p></body></html>"
    title, _ = extract_text_and_title(html)
    assert title == "Fallback Heading"


def test_strips_script_and_style_content():
    html = "<html><body><script>evil()</script><style>.a{}</style><p>real text</p></body></html>"
    _, text = extract_text_and_title(html)
    assert "evil" not in text
    assert "real text" in text


def test_strips_nav_header_footer_chrome():
    html = """
    <html><body>
      <header>Site Header Links</header>
      <nav>Home About Contact</nav>
      <main><p>The actual article content.</p></main>
      <footer>Copyright 2026</footer>
    </body></html>
    """
    _, text = extract_text_and_title(html)
    assert "actual article content" in text
    assert "Site Header Links" not in text
    assert "Home About Contact" not in text
    assert "Copyright 2026" not in text


def test_nested_chrome_does_not_crash():
    # A <nav> inside a chrome id, containing a class-matched element -- three
    # overlapping strip rules touching the same subtree, which previously
    # crashed on the second/third attempt to decompose an already-removed tag.
    html = """
    <html><body>
      <div id="mw-navigation">
        <nav class="vector-menu">
          <span class="mw-editsection">edit</span>
          Nested nav text
        </nav>
      </div>
      <main><p>Article body text.</p></main>
    </body></html>
    """
    title, text = extract_text_and_title(html)
    assert "Article body text" in text
    assert "Nested nav text" not in text


def test_strips_known_mediawiki_chrome_ids():
    html = """
    <html><body>
      <div id="mw-panel">Sidebar links</div>
      <div id="catlinks">Categories: A B C</div>
      <p>Encyclopedia article paragraph.</p>
    </body></html>
    """
    _, text = extract_text_and_title(html)
    assert "Encyclopedia article paragraph" in text
    assert "Sidebar links" not in text
    assert "Categories" not in text


def test_extract_links_resolves_relative_urls():
    html = '<html><body><a href="/wiki/Other_Page">Other Page</a></body></html>'
    page = extract(html, base_url="https://en.wikipedia.org/wiki/Some_Page")
    assert len(page.links) == 1
    assert page.links[0].url == "https://en.wikipedia.org/wiki/Other_Page"
    assert page.links[0].anchor_text == "Other Page"


def test_extract_links_deduplicates():
    html = """
    <html><body>
      <a href="/a">first</a>
      <a href="/a">second mention</a>
    </body></html>
    """
    page = extract(html, base_url="https://example.com")
    assert len(page.links) == 1


def test_extract_links_skips_non_http_hrefs():
    html = """
    <html><body>
      <a href="mailto:test@example.com">mail</a>
      <a href="javascript:void(0)">js</a>
      <a href="#section">anchor</a>
      <a href="/real-page">real</a>
    </body></html>
    """
    page = extract(html, base_url="https://example.com")
    urls = [link.url for link in page.links]
    assert urls == ["https://example.com/real-page"]


def test_malformed_html_does_not_crash():
    html = "<html><body><p>unclosed paragraph <div>nested badly</p></div>"
    title, text = extract_text_and_title(html)
    assert "unclosed paragraph" in text
