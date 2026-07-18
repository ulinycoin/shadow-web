"""Tests for content_index — Content Block Index."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from shadow_web.content_index import build, fetch, outline_text, quality
from shadow_web.mcp import server as mcp_server

# ── Fixtures ───────────────────────────────────────────────────────────

WIKI_HTML = """\
<html><body>
<h1>Web Scraping</h1>
<p>Web scraping is the process of automatically extracting data from websites.
It is used in various fields including data analysis, market research, and
machine learning. Modern web scraping tools can handle JavaScript-rendered
content and complex authentication flows.</p>

<h2>Techniques</h2>
<p>There are several approaches to web scraping. The simplest is making HTTP
requests and parsing the returned HTML using libraries like BeautifulSoup or
lxml. More advanced techniques involve browser automation with tools such as
Playwright or Puppeteer.</p>

<h2>Common Use Cases</h2>
<p>Web scraping is widely used for price monitoring across e-commerce
platforms. Companies track competitor pricing, product availability, and
customer reviews to inform their business strategies.</p>

<h3>Market Research</h3>
<p>Analysts scrape social media, forums, and review sites to gauge public
sentiment about products and brands. This real-time data helps companies make
faster decisions than traditional survey methods.</p>

<h2>Ethical Considerations</h2>
<p>Web scraping raises important ethical questions. Website owners may prohibit
scraping in their Terms of Service, and excessive requests can degrade server
performance for other users. Responsible scrapers implement rate limiting and
respect robots.txt.</p>

<nav>
  <p>This should not appear — inside nav.</p>
  <a href="/home">Home</a>
</nav>

<footer>
  <p>Copyright 2026 — also excluded.</p>
</footer>

<aside>
  <p>Sidebar content — excluded.</p>
</aside>

<form>
  <p>Form description — excluded.</p>
</form>

<table>
  <tr><td>Table data — excluded.</td></tr>
</table>
</body></html>
"""

# Real-world page: article-style, multiple headings, >500 tokens raw
SIMPLE_PAGE = """\
<html><body>
<h1>The History of Programming Languages</h1>
<p>Programming languages have evolved significantly since the 1950s. From
assembly language to modern high-level languages, each era brought new
paradigms and capabilities that shaped how we write software today.</p>

<h2>Early Languages</h2>
<p>Fortran, developed by IBM in 1957, was the first high-level programming
language. It was designed for scientific and engineering calculations.
COBOL followed in 1959, targeting business data processing. Both languages
remain in use today in legacy systems.</p>

<p>Lisp, created in 1958 by John McCarthy, introduced functional programming
concepts that influence modern languages like Clojure and Scheme. Its
parenthetical syntax was revolutionary for its time.</p>

<h2>Object-Oriented Revolution</h2>
<p>Smalltalk, developed at Xerox PARC in the 1970s, popularized object-oriented
programming. C++ emerged in 1985 as an extension of C with classes, and Java
followed in 1995 with its "write once, run anywhere" philosophy.</p>

<h3>Modern Languages</h3>
<p>Python, created by Guido van Rossum in 1991, emphasized readability and
simplicity. Its extensive standard library and package ecosystem have made
it one of the most popular languages for data science and web development.</p>

<p>Rust, developed by Mozilla, offers memory safety without garbage collection.
It has been voted the "most loved language" on Stack Overflow surveys for
multiple years running.</p>

<h2>Future Trends</h2>
<p>Emerging languages focus on concurrency, safety, and developer experience.
Languages like Go, Kotlin, and TypeScript address specific niches while
incorporating lessons from their predecessors.</p>

<p>WebAssembly enables running code written in multiple languages in the
browser at near-native speed, opening new possibilities for web applications.</p>
</body></html>
"""

# Completely empty/trivial HTML
EMPTY_HTML = "<html><body></body></html>"
SPAN_ONLY = "<html><body><span>hello</span></body></html>"


# ── build() tests ──────────────────────────────────────────────────────


class TestBuild:
    def test_wikipedia_builds_blocks(self):
        blocks = build(WIKI_HTML)
        # 5 headings (h1, 3×h2, h3) + 5 paragraphs = 10
        assert len(blocks) == 10

    def test_blocks_have_required_keys(self):
        blocks = build(WIKI_HTML)
        for b in blocks:
            assert "id" in b
            assert "tag" in b
            assert "heading_path" in b
            assert "type" in b
            assert "text" in b
            assert "tokens" in b
            assert b["tokens"] >= 1

    def test_sequential_ids(self):
        blocks = build(WIKI_HTML)
        expected = [f"p{i}" for i in range(len(blocks))]
        assert [b["id"] for b in blocks] == expected

    def test_excludes_nav_footer_aside_form_table(self):
        blocks = build(WIKI_HTML)
        texts = [b["text"] for b in blocks]
        assert "should not appear" not in " ".join(texts)
        assert "Copyright" not in " ".join(texts)
        assert "Sidebar" not in " ".join(texts)
        assert "Form description" not in " ".join(texts)
        assert "Table data" not in " ".join(texts)

    def test_heading_path_nesting(self):
        blocks = build(SIMPLE_PAGE)
        # "Modern Languages" should have path "Object-Oriented Revolution > Modern Languages"
        modern = [b for b in blocks if "Modern Languages" in b["heading_path"]]
        assert len(modern) > 0
        for b in modern:
            assert "Object-Oriented Revolution" in b["heading_path"]

    def test_empty_html_returns_empty(self):
        assert build(EMPTY_HTML) == []

    def test_span_only_content_is_indexed(self):
        blocks = build(SPAN_ONLY)
        assert [block["text"] for block in blocks] == ["hello"]
        assert blocks[0]["type"] == "text_group"

    def test_div_card_content_is_indexed_without_tag_specific_rules(self):
        html = """
        <main>
          <div>
            <a><span>Phone Alpha 256 GB</span></a>
            <div><span>4.8 rating</span><span>59 990 ₽</span></div>
          </div>
          <div>
            <a><span>Phone Beta 128 GB</span></a>
            <div><span>4.6 rating</span><span>39 990 ₽</span></div>
          </div>
        </main>
        """
        blocks = build(html)
        indexed = " ".join(block["text"] for block in blocks)
        assert "Phone Alpha 256 GB" in indexed
        assert "Phone Beta 128 GB" in indexed
        assert "59 990 ₽" in indexed
        assert "39 990 ₽" in indexed
        assert indexed.count("Phone Alpha 256 GB") == 1
        assert indexed.count("59 990 ₽") == 1
        assert all(block["type"] == "text_group" for block in blocks)

    def test_semantic_and_structural_blocks_keep_document_order(self):
        html = (
            "<h1>Catalog</h1>"
            "<div><span>Featured device</span><span>19 990 ₽</span></div>"
            "<h2>Details</h2><p>Semantic description.</p>"
        )
        blocks = build(html)
        assert [block["text"] for block in blocks] == [
            "Catalog",
            "Featured device 19 990 ₽",
            "Details",
            "Semantic description.",
        ]
        assert blocks[1]["heading_path"] == "Catalog"
        assert blocks[3]["heading_path"] == "Catalog > Details"

    def test_each_text_node_is_owned_once(self):
        html = (
            "<div><div><span>Unique product name</span></div>"
            "<div><span>12 345 ₽</span></div></div>"
        )
        indexed = " ".join(block["text"] for block in build(html))
        assert indexed.count("Unique product name") == 1
        assert indexed.count("12 345 ₽") == 1

    def test_large_repeated_grid_stops_at_compact_cards(self):
        html = "<main>" + "".join(
            f"<div><span>Product {i} with descriptive model name</span>"
            f"<span>{i + 10} 990 ₽</span></div>"
            for i in range(30)
        ) + "</main>"
        blocks = build(html)
        assert len(blocks) > 1
        assert max(block["tokens"] for block in blocks) <= 120
        indexed = " ".join(block["text"] for block in blocks)
        assert all(indexed.count(f"Product {i} with descriptive model name") == 1
                   for i in range(30))

    def test_empty_string_returns_empty(self):
        assert build("") == []

    def test_nested_list_text_is_not_duplicated(self):
        blocks = build("<ul><li>Parent<ul><li>Child</li></ul></li></ul>")
        assert [block["text"] for block in blocks] == ["Parent", "Child"]

    def test_boilerplate_language_links_removed(self):
        languages = ["العربية","Deutsch","Español","Français","Italiano","日本語","Русский","中文"]
        html = (
            "<h1>Article</h1>"
            + "".join(
                f'<li><a href="/lang/{idx}">{lang}</a></li>'
                for idx, lang in enumerate(languages)
            )
            + "<p>First real paragraph with enough text to be meaningful content for readers.</p>"
        )
        blocks = build(html)
        block_texts = {b["text"] for b in blocks}
        assert not set(languages) & block_texts
        assert "First real paragraph" in " ".join(b["text"] for b in blocks)

    def test_boilerplate_keeps_unlinked_data_list_after_h1(self):
        """Ingredients/specifications must not be mistaken for navigation."""
        items = ["Flour", "Water", "Salt", "Yeast", "Oil"]
        html = (
            "<h1>Bread recipe</h1>"
            + "".join(f"<li>{item}</li>" for item in items)
            + "<p>This recipe explains how to combine the ingredients and bake "
              "the bread correctly for a reliable result.</p>"
        )
        blocks = build(html)
        li_texts = [b["text"] for b in blocks if b["tag"] == "li"]
        assert li_texts == items
        assert all(not any(key.startswith("_") for key in b) for b in blocks)

    def test_boilerplate_keeps_short_lists_inside_article(self):
        """A short list inside the article body should NOT be removed."""
        html = (
            "<h1>Article</h1>"
            + "<p>Introduction with enough text to be solid content for the article body.</p>"
            + "<h2>Key points</h2>"
            + "<li>Point one with some descriptive detail</li>"
            + "<li>Point two with more substance here</li>"
            + "<li>Point three</li>"
            + "<li>Point four</li>"
            + "<li>Point five</li>"
            + "<li>Point six</li>"
        )
        blocks = build(html)
        li_texts = [b["text"] for b in blocks if b["tag"] == "li"]
        assert len(li_texts) >= 4  # Short lis after h2 are kept

    def test_heading_types(self):
        blocks = build(WIKI_HTML)
        headings = [b for b in blocks if b["type"] == "heading"]
        assert len(headings) == 5
        assert all(b["tag"].startswith("h") for b in headings)

    def test_paragraph_types(self):
        blocks = build(WIKI_HTML)
        paragraphs = [b for b in blocks if b["type"] == "paragraph"]
        assert len(paragraphs) == 5

    def test_heading_levels(self):
        blocks = build(SIMPLE_PAGE)
        for b in blocks:
            if b["tag"] in ("h1", "h2", "h3", "h4", "h5", "h6"):
                assert b["level"] > 0
            else:
                assert b["level"] == 0


# ── outline_text() tests ───────────────────────────────────────────────


class TestOutline:
    def test_includes_headings(self):
        blocks = build(WIKI_HTML)
        outline = outline_text(blocks, max_tokens=100)
        assert "Web Scraping" in outline
        assert "Techniques" in outline

    def test_removes_excluded_content(self):
        blocks = build(WIKI_HTML)
        outline = outline_text(blocks)
        assert "nav" not in outline.lower()
        assert "Copyright" not in outline

    def test_contains_block_ids(self):
        blocks = build(WIKI_HTML)
        outline = outline_text(blocks)
        assert "p0" in outline
        assert "p1" in outline

    def test_token_count_format(self):
        blocks = build(WIKI_HTML)
        outline = outline_text(blocks)
        assert "t |" in outline
        assert "source=" in outline

    def test_max_tokens_limits_paragraphs(self):
        blocks = build(SIMPLE_PAGE)
        outline_small = outline_text(blocks, max_tokens=100)
        outline_large = outline_text(blocks, max_tokens=500)
        # Smaller budget should produce fewer lines
        lines_small = outline_small.strip().split("\n")
        lines_large = outline_large.strip().split("\n")
        # Both have summary line, headings always included
        assert len(lines_small) <= len(lines_large)

    def test_empty_blocks(self):
        outline = outline_text([])
        assert "range=0:0/0" in outline

    def test_budget_applies_to_actual_outline(self):
        html = "<body>" + "".join(
            f"<h2>Heading {i} with a fairly long descriptive title</h2>"
            for i in range(100)
        ) + "</body>"
        outline = outline_text(build(html), max_tokens=50)
        assert len(outline) // 4 <= 50

    def test_large_blocks_remain_discoverable(self):
        html = "<h1>Article</h1><p>" + ("Long content. " * 1000) + "</p>"
        outline = outline_text(build(html), max_tokens=100)
        assert "p1 | Article | paragraph" in outline

    def test_offset_continues_a_budgeted_outline(self):
        html = "<body>" + "".join(
            f"<p>Paragraph {i} with enough text for a compact outline line.</p>"
            for i in range(40)
        ) + "</body>"
        blocks = build(html)
        first = outline_text(blocks, max_tokens=100)
        assert "next=" in first
        next_offset = int(first.rsplit("next=", 1)[1].split()[0])
        second = outline_text(blocks, max_tokens=100, offset=next_offset)
        assert f"range={next_offset}:" in second

    def test_catalog_outline_surfaces_priced_cards_first(self):
        """Nav/filter chrome must not consume the first outline budget."""
        chrome = "".join(
            f"<div><span>Filter option {i} with long descriptive label text</span></div>"
            for i in range(40)
        )
        products = "".join(
            f"<div><a><span>Product model {i} wireless headphones</span></a>"
            f"<span>€{20 + i}.99</span></div>"
            for i in range(12)
        )
        html = f"<main><h1>Catalog</h1>{chrome}{products}</main>"
        blocks = build(html)
        outline = outline_text(blocks, max_tokens=350)
        assert "cards=" in outline
        assert outline.count("€") >= 4
        assert " | card | " in outline
        # First content line after ranking should be a product card or heading,
        # not a long run of filter chrome.
        first_lines = [line for line in outline.splitlines() if line.startswith("p")]
        assert first_lines
        assert "Product model" in first_lines[0] or "Catalog" in first_lines[0]
        assert "Filter option 0" not in first_lines[0]

    def test_article_outline_keeps_heading_first(self):
        blocks = build(WIKI_HTML)
        outline = outline_text(blocks, max_tokens=200)
        first = next(line for line in outline.splitlines() if line.startswith("p"))
        assert "Web Scraping" in first

    def test_feed_outline_surfaces_items_without_prices(self):
        """Repeating mid-size posts beat engagement chrome in the first budget."""
        chrome = "".join(
            f"<div><span>Like {i}</span><span>Share {i}</span>"
            f"<span>Comment {i}</span></div>"
            for i in range(30)
        )
        # ~40-60 tokens each — similar length, no currency signals.
        posts = "".join(
            f"<div><a><span>Breaking story number {i} about local politics "
            f"and community events with enough detail for agents</span></a>"
            f"<span>Posted by user{i} · 2 hours ago · discussion thread</span></div>"
            for i in range(10)
        )
        html = f"<main><h1>Latest</h1>{chrome}{posts}</main>"
        blocks = build(html)
        outline = outline_text(blocks, max_tokens=400)
        assert "feeds=" in outline
        assert " | feed | " in outline
        first_lines = [line for line in outline.splitlines() if line.startswith("p")]
        assert first_lines
        assert "Breaking story" in first_lines[0] or "Latest" in first_lines[0]
        assert "Like 0" not in first_lines[0]

    def test_reduction_wikipedia(self):
        """Real benchmark fixture: actual outline text is at least 10x smaller."""
        fixture = (
            Path(__file__).parents[1]
            / "benchmarks"
            / "fixtures"
            / "wikipedia_like.html"
        )
        large_html = fixture.read_text(encoding="utf-8")
        raw_tokens = len(large_html) // 4
        blocks = build(large_html)
        outline = outline_text(blocks, max_tokens=600)
        outline_tokens = max(1, len(outline) // 4)
        assert outline_tokens <= 600
        assert outline_tokens < raw_tokens // 10, (
            f"Outline {outline_tokens}t vs raw {raw_tokens}t — "
            f"ratio {raw_tokens // max(outline_tokens, 1)}x, need >= 10x"
        )


# ── fetch() tests ──────────────────────────────────────────────────────


class TestFetch:
    def test_returns_requested_blocks(self):
        blocks = build(WIKI_HTML)
        result = fetch(blocks, ["p0", "p1"])
        assert "p0" in result
        assert "p1" in result
        assert "Web Scraping" in result["p0"]
        assert "automatically extracting" in result["p1"]

    def test_unknown_id_skipped(self):
        blocks = build(WIKI_HTML)
        result = fetch(blocks, ["p999"])
        assert result == {}

    def test_max_tokens_truncates(self):
        blocks = build(WIKI_HTML)
        result = fetch(blocks, ["p0", "p1", "p2", "p3", "p4"], max_tokens=20)
        # Should contain at least one block (may be truncated)
        assert len(result) >= 1
        for text in result.values():
            assert len(text) <= 200  # truncated

    def test_empty_ids(self):
        blocks = build(WIKI_HTML)
        result = fetch(blocks, [])
        assert result == {}

    def test_non_positive_budget_returns_empty(self):
        blocks = build(WIKI_HTML)
        assert fetch(blocks, ["p0"], max_tokens=0) == {}
        assert fetch(blocks, ["p0"], max_tokens=-1) == {}

    def test_requested_order_is_preserved(self):
        blocks = build(WIKI_HTML)
        result = fetch(blocks, ["p3", "p0"])
        assert list(result) == ["p3", "p0"]


class TestSessionIndex:
    def test_requires_browser_session(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "_session", {})
        with pytest.raises(RuntimeError, match="navigate"):
            mcp_server._get_content_index_blocks()

    def test_builds_from_current_session_and_reuses_cache(self, monkeypatch):
        shadow = SimpleNamespace(clean_html="<h1>One</h1><p>First body</p>")
        session = {"shadow_page": shadow}
        monkeypatch.setattr(mcp_server, "_session", session)

        first = mcp_server._get_content_index_blocks()
        second = mcp_server._get_content_index_blocks()
        assert first is second
        assert [block["text"] for block in first] == ["One", "First body"]
        assert mcp_server._get_content_index_quality()["coverage_pct"] >= 95

        shadow.clean_html = "<h1>Two</h1><p>Second body</p>"
        refreshed = mcp_server._get_content_index_blocks()
        assert refreshed is not first
        assert [block["text"] for block in refreshed] == ["Two", "Second body"]


class TestQuality:
    def test_reports_full_structural_coverage_and_price_retention(self):
        html = (
            "<main><div><span>Phone Alpha</span><span>59 990 ₽</span></div>"
            "<div><span>Phone Beta</span><span>39 990 ₽</span></div></main>"
        )
        stats = quality(html, build(html))
        assert stats["coverage_pct"] >= 95
        assert stats["duplicate_overhead_pct"] == 0
        assert stats["source_price_signals"] == 2
        assert stats["indexed_price_signals"] == 2
        assert stats["signal_retention_pct"] == 100
        assert stats["mode"] == "hybrid"

    def test_outline_can_include_quality_diagnostics(self):
        blocks = build("<div><span>Product</span><span>12 990 ₽</span></div>")
        stats = quality("<div><span>Product</span><span>12 990 ₽</span></div>", blocks)
        outline = outline_text(blocks, quality_data=stats)
        assert "coverage=" in outline
        assert "mode=hybrid" in outline
        assert "signals=1/1" in outline

    def test_recognizes_major_retail_currencies(self):
        html = """
        <main>
          <div><span>Coat</span><span>€39.95</span></div>
          <div><span>Dress</span><span>€ 25.96</span></div>
          <div><span>Shirt</span><span>19,99 €</span></div>
          <div><span>Bag</span><span>£45.00</span></div>
          <div><span>Tee</span><span>$19.99</span></div>
          <div><span>Phone</span><span>59 990 ₽</span></div>
          <div><span>Watch</span><span>EUR 120.00</span></div>
          <div><span>Sneakers</span><span>2 499 руб.</span></div>
        </main>
        """
        stats = quality(html, build(html))
        assert stats["source_price_signals"] == 8
        assert stats["indexed_price_signals"] == 8
        assert stats["signal_retention_pct"] == 100

    def test_does_not_count_bare_numbers_as_prices(self):
        html = "<main><div><span>Size 42</span><span>Model 2024</span><div>Rating 4.8</div></div></main>"
        stats = quality(html, build(html))
        assert stats["source_price_signals"] == 0
        assert stats["indexed_price_signals"] == 0
