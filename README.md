
<!-- badges -->
[![PyPI version](https://badge.fury.io/py/shadow-web.svg)](https://pypi.org/project/shadow-web/)
[![CI](https://github.com/ulinycoin/shadow-web/actions/workflows/test.yml/badge.svg)](https://github.com/ulinycoin/shadow-web/actions/workflows/test.yml)
[![Python](https://img.shields.io/pypi/pyversions/shadow-web.svg)](https://pypi.org/project/shadow-web/)
[![License](https://img.shields.io/github/license/ulinycoin/shadow-web.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/ulinycoin/shadow-web?style=flat)](https://github.com/ulinycoin/shadow-web)

# Shadow Web

**Cut 64–97% of tokens from web pages before your LLM sees them — then extract structured data.**  
Open-source Python SDK that flattens Shadow DOM, builds a typed Action Map with semantic groups, heals broken selectors, and turns HTML tables/forms/lists into clean JSON — no cloud required.

```python
from shadow_web.compressor import process_html

clean_html, actions, groups = process_html(raw_html)
# ✅ actions = [{"id":"1","type":"button","label":"Buy Now","group":"Checkout"}, ...]
# ✅ 164 → 46 tokens on a typical page

from shadow_web.schema_snap import parse_page

data = parse_page(clean_html)
# ✅ tables: [{columns, types, rows, total_rows}]
# ✅ forms:  [{action, method, fields: [{name, type, required, label}]}]
# ✅ lists:  [{type, items, total}]
```

---

## Pain point

AI agents need to see web pages. But raw HTML is full of `<script>`, `<style>`, inline CSS, and interactive elements buried in Shadow DOM trees that Playwright can't reach. A typical Wikipedia page costs **99K tokens** raw. Your LLM bill doesn't need that.

Shadow Web is what runs **between** the browser and the LLM: a compression layer that keeps only what matters — interactive elements, their labels, and a clean DOM skeleton. **SchemaSnap** then takes that clean HTML and turns it into structured data agents can actually use: table rows, form fields with validation, list items.

---

## What you get

| Feature | Raw HTML | Playwright locators | Shadow Web |
|---------|----------|-------------------|------------|
| Token cost (Wikipedia) | 99,343 | — | **16,462** (−83%) |
| Token cost (GitHub Trending) | 167,875 | — | **37,833** (−77%) |
| Shadow DOM readable | ❌ | ❌ partial | ✅ flattened |
| Semantic groups | ❌ | ❌ | ✅ Login / Cart / Nav |
| Self-healing selectors | ❌ | ❌ | ✅ local + LLM fallback |
| **Tables → JSON columns+rows** | ❌ | ❌ | ✅ SchemaSnap |
| **Forms → fields with validation** | ❌ | ❌ | ✅ SchemaSnap |
| **Lists → typed items** | ❌ | ❌ | ✅ SchemaSnap |
| Works offline | ✅ | ✅ | ✅ |
| PyPI package | — | `playwright` | `shadow-web` |

---

## Who this is for

| You're building … | Why Shadow Web |
|-------------------|----------------|
| A browser-based AI agent | Action Map + self-healing + SchemaSnap = fewer failures, structured data |
| An MCP tool for Cursor/Claude | Built-in MCP server with 15+ tools, one-command setup |
| A Playwright scraper that breaks on every deploy | `heal_local.py` catches DOM drift without LLM cost |
| A Shadow DOM-heavy app (Web components, Lit, Angular) | Read-only flatten — no React/Vue breakage |
| **An agent that needs data from web pages** | SchemaSnap parses tables, forms, and lists into clean JSON |

---

## Quick install

```bash
pip install shadow-web
playwright install chromium
```

**Extras:**

```bash
pip install "shadow-web[mcp]"          # Cursor/Claude MCP server
pip install "shadow-web[server]"        # FastAPI heal API
pip install "shadow-web[all]"           # everything
```

---

## Demo

### Compress a page (3 lines)

```python
from shadow_web.compressor import process_html, generate_grouped_xml_map

clean_html, actions, groups = process_html(open("page.html").read())
xml_map = generate_grouped_xml_map("https://example.com", "Example", groups)
print(xml_map)
```

### Playwright + Shadow DOM flatten

```python
from playwright.sync_api import sync_playwright
from shadow_web.wrapper import ShadowPage

with sync_playwright() as p:
    page = p.chromium.launch(headless=True).new_page()
    page.goto("https://example.com")
    shadow = ShadowPage(page)
    _, xml_map = shadow.refresh()
    print(shadow.capture_stats)  # shadow_hosts, iframes, a11y supplement
```

### shadow_grep — send only what the LLM needs

```python
result = shadow.query("intent:login", fmt="terse")
# @1 button Sign in [Login Form]
# @2 input[email] Email [Login Form]
```

### Stream a delta after clicking

```python
shadow.refresh()               # full baseline
shadow.click("3")              # navigate
_, delta_xml = shadow.refresh(diff=True)  # only what changed
```

### SchemaSnap — extract tables, forms, and lists

```python
from shadow_web.schema_snap import parse_page, parse_tables

# Parse everything from a page
data = parse_page(raw_html)
# {
#   "tables": [
#     {
#       "columns": ["Product", "Price", "Stock"],
#       "types": ["string", "currency", "integer"],
#       "rows": [["Widget A", "$19.99", "150"], ...],
#       "total_rows": 12,
#       "column_count": 3
#     }
#   ],
#   "forms": [
#     {
#       "action": "/checkout",
#       "method": "POST",
#       "fields": [
#         {"tag": "input", "type": "email", "name": "email",
#          "required": true, "label": "Email Address"},
#         {"tag": "select", "name": "country", "options": [
#           {"value": "US", "label": "United States"}, ...]}
#       ]
#     }
#   ],
#   "lists": [
#     {"type": "unordered", "items": ["Apples", "Bananas"], "total": 2}
#   ]
# }

# Or extract only tables with a row limit
tables = parse_tables(html, max_rows=50)
# When truncated: adds rows_truncated=True, rows_returned=50
```

### SchemaSnap in MCP (no browser session)

```python
# Via MCP — send HTML directly
mcp.schema_table(html=raw_html)
mcp.schema_form(html=raw_html)
mcp.schema_list(html=raw_html)
mcp.schema_page(html=raw_html)

# Or from the current browser session (after navigate/snapshot)
mcp.schema_session()
mcp.get_page_html()
```

---

## How it works

```
Browser (live DOM)
    │
    ├─ [default] DOM capture — flatten Shadow DOM + same-origin iframes (read-only)
    │              ↓
    ├─ [optional] a11y CDP supplement — catch closed Shadow DOM elements
    │              ↓
    ├─ [Chrome 145+] WebMCP bridge — page exposes document.modelContext.getTools()
    │
    └─→ compressor.py → Action Map (data-sid, type, label, group)
                          ↓
                    shadow_grep.py → filter before LLM
                          ↓
                    schema_snap.py → structured data (tables, forms, lists)
                          ↓
                    heal_local.py → fuzzy selector recovery (no LLM)
                          ↓
                    FastAPI /v1/heal → LLM fallback + verification
```

**No live DOM mutation.** Shadow Web reads your page; it never writes back. React/Vue/Svelte listeners stay intact.

---

## Architecture

```
shadow_web/
├── compressor.py      # DOM strip + Action Map + semantic groups
├── dom_capture.py     # Shadow DOM / iframe flatten (in-browser, read-only)
├── grouping.py        # Semantic groups (forms, nav, modals)
├── schema_snap.py     # ★ NEW — parse tables, forms, lists → structured JSON
├── heal_local.py      # Local selector heal + ~/.shadow-web/heal_cache.json
├── query.py           # shadow_grep (type:, intent:, label~, AND)
├── webmcp.py          # WebMCP bridge (Chrome 145+)
├── diff.py            # Page diff (skeleton + delta XML)
├── a11y_capture.py    # CDP Accessibility dual capture
├── verified_heal.py   # Playwright selector verification
├── wrapper.py         # ShadowPage (Playwright)
├── mcp/server.py      # Cursor / Claude MCP tools
└── server/main.py     # FastAPI (/v1/compress, /v1/heal)
```

---

## Benchmarks

| Page | Raw HTML (tokens) | Grouped XML (tokens) | Actions | Reduction |
|------|-------------------|----------------------|---------|-----------|
| Hacker News | 8,637 | 6,704 | 227 | **−22% (1.3×)** |
| Wikipedia (Web Scraping) | 99,343 | 16,462 | 501 | **−83% (6.0×)** |
| GitHub Trending | 167,875 | 37,833 | 1,290 | **−77% (4.4×)** |

Run locally: `pip install tiktoken && python benchmarks/run.py`

---

## MCP for Cursor / Claude

```json
{
  "mcpServers": {
    "shadow-web": {
      "command": "shadow-web-mcp"
    }
  }
}
```

**Tools (15+):**

| Tool | What it does |
|------|-------------|
| `navigate` | Open URL → Action Map snapshot |
| `snapshot` | Refresh page (opt-in diff mode) |
| `click`, `fill` | Interact by data-sid |
| `compress_html` | Strip + Action Map from raw HTML |
| `compress_html_to_xml` | Grouped XML from raw HTML |
| `shadow_query`, `query_page`, `shadow_grep_html` | grep-style element filter |
| `web_search` | Brave Search (no API keys) |
| `webmcp_list_tools`, `webmcp_execute_tool` | Chrome WebMCP bridge |
| **`schema_table`** | ★ Table columns + types + rows (from HTML) |
| **`schema_form`** | ★ Form fields with validation |
| **`schema_list`** | ★ List items |
| **`schema_page`** | ★ All of the above at once |
| **`schema_session`** | ★ All from current browser session |
| **`get_page_html`** | ★ Full clean HTML from current session |

---

## browser-use Integration

Shadow Web provides out-of-the-box integration with **browser-use** (the popular agentic framework). It drops token usage by up to 90% and allows the agent to interact with elements inside **Shadow DOM** and iframes using a single line setup.

```bash
pip install "shadow-web[browser-use]"
```

```python
from browser_use import Agent
from shadow_web import ShadowTools

# Default format is "terse" (compact). Use format="xml" in get_xml_action_map when needed.
tools = ShadowTools(
    heal_api_url="http://localhost:8000/v1/heal",  # Optional: LLM fallback self-healing API
)

agent = Agent(task="...", llm=llm, tools=tools)
```

`get_xml_action_map` also accepts `query` (e.g. `intent:login`) and `format` (`terse` | `xml`) per call.

See [examples/browser_use/](examples/browser_use/) for a complete working implementation.

---

## Self-healing chain

```
click("3") → binding path → element not found?
    ↓
local heal (fuzzy label + stable attr match, 85% threshold) → no LLM, no cost
    ↓
LLM heal (DeepSeek / OpenAI via /v1/heal) → generates candidate selector
    ↓
selector verified in headless Chromium → cached to ~/.shadow-web/heal_cache.json
```

---

## SchemaSnap — token-aware data extraction

SchemaSnap tools default to **max_rows=50** to keep token usage predictable. Set `max_rows=0` for full data when you need it — but remember that a 500-row table can cost 10K+ tokens.

```
navigate(url, detail="minimal")   → load page, minimal output
schema_session()                  → tables + forms, max 50 rows/table
schema_session(max_rows=0)        → full data (potentially large)
```

**Type inference** detects: `string`, `integer`, `number`, `currency` ($/€/£), `percentage`, `date`, `email`, `url`.

---

## When NOT to use Shadow Web

- You need **one** `document.querySelector` — use Playwright directly.
- You're building a static site scraper with no interaction.
- The page is plain HTML with no Shadow DOM — overhead isn't worth it.

---

## License

MIT. Free for anything.

---

*Stars are the oxygen of open-source. If Shadow Web saved you tokens or debugging time, ★ the repo.*
