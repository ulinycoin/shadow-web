"""Tests for content_index — Content Block Index."""

import pytest

from shadow_web.content_index import build, outline_text, fetch

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

    def test_no_content_tags_returns_empty(self):
        assert build(SPAN_ONLY) == []

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
        assert "total_estimated_tokens=" in outline

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
        assert "blocks=0" in outline

    def test_reduction_wikipedia(self):
        """Wikipedia-sized content: outline must be < 10% of raw text tokens."""
        # Simulate a Wikipedia article: lots of verbose text, nav/footer excluded
        para = (
            "Machine learning is a subset of AI that enables systems to "
            "learn and improve from experience without being explicitly programmed. "
        )
        sections = [
            ("h1", "Artificial Intelligence"),
            ("p", "AI is intelligence demonstrated by machines."),
            ("p", para * 50),
            ("h2", "History"),
            ("p", ("AI research started at Dartmouth in 1956 with McCarthy, Minsky, "
                   "Newell, and Simon. ") * 60),
            ("h2", "Approaches"),
            ("p", ("Symbolic AI uses explicit rules. Connectionist AI uses neural "
                   "networks. ") * 60),
            ("h3", "Machine Learning"),
            ("p", ("Supervised, unsupervised, and reinforcement learning are the "
                   "three main paradigms. ") * 50),
            ("h3", "Deep Learning"),
            ("p", ("CNNs for images, RNNs for sequences, transformers for "
                   "language. ") * 50),
            ("h2", "Applications"),
            ("p", ("Healthcare diagnosis, fraud detection, self-driving cars, "
                   "recommender systems. ") * 60),
            ("h2", "Ethics"),
            ("p", ("Bias, privacy, job displacement, and alignment are key "
                   "concerns. ") * 50),
        ]
        lines = ["<html><body>"]
        for tag, content in sections:
            lines.append(f"<{tag}>{content}</{tag}>")
        lines.append("<nav><p>Skip this</p></nav>")
        lines.append("<footer><p>Skip this too</p></footer>")
        lines.append("<aside><p>And this</p></aside>")
        lines.append("</body></html>")
        large_html = "\n".join(lines)

        raw_tokens = len(large_html) // 4
        blocks = build(large_html)
        outline = outline_text(blocks, max_tokens=600)
        import re
        m = re.search(r"total_estimated_tokens=(\d+)", outline)
        outline_tokens = int(m.group(1)) if m else len(outline) // 4
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
