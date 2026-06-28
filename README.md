
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
| An MCP tool for Cursor/Claude | Built-in MCP server with **22 tools**, one-command setup |
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

### Golden path (recommended — run locally)

Full agent loop with token counts at each step:

```bash
pip install -e ".[mcp]"
playwright install chromium
python examples/golden_path/demo.py
```

Output: raw HTML vs `navigate(minimal)` + `schema_session_json` + `shadow_query` — side-by-side token table.  
Playbook: [examples/golden_path/CASE.md](examples/golden_path/CASE.md)

Smoke test (install + unit tests + one live site):

```bash
bash scripts/smoke_install.sh
```

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

# Or export table rows directly
from shadow_web.schema_snap import export_table_json, export_table_csv

records = export_table_json(clean_html, max_rows=50)
# [{"Name": "Alice", "Age": 30}, ...]

csv_text = export_table_csv(clean_html)
# "Name,Age\nAlice,30\n..."
```

### SchemaSnap in MCP

**From HTML string (no browser):**

| Tool | Output |
|------|--------|
| `schema_table(html)` | columns + types + rows |
| `schema_form(html)` | fields + validation |
| `schema_list(html)` | ul/ol/standalone select |
| `schema_page(html)` | all of the above |
| `schema_json(html)` | `[{column: value}, ...]` |
| `schema_csv(html)` | `"col1,col2\n..."` |

**From browser session** (after `navigate` / `snapshot`):

| Tool | Output |
|------|--------|
| `schema_session()` | tables + forms + lists |
| `schema_session_json()` | JSON records |
| `schema_session_csv()` | CSV string |
| `get_page_html(max_chars=50000)` | clean HTML (truncated by default) |

All table tools accept `max_rows=50` (default). Set `max_rows=0` for full data.

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
├── schema_snap.py     # Tables, forms, lists → JSON/CSV export
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

One-command setup:

```bash
bash scripts/cursor-setup.sh
```

Or manually:

```json
{
  "mcpServers": {
    "shadow-web": {
      "command": "shadow-web-mcp"
    }
  }
}
```

### All 22 tools

| Category | Tool | What it does |
|----------|------|--------------|
| **Browse** | `navigate` | Open URL → snapshot (`detail`: minimal / terse / xml / full) |
| | `snapshot` | Refresh page; `diff=true` for delta only |
| | `click`, `fill` | Interact by `data-sid` |
| **Filter (control plane)** | `shadow_query` | grep-style filter on live session |
| | `query_page` | Alias for shadow_query (json output) |
| | `shadow_grep_html` | Filter raw/clean HTML without browser |
| **Compress (offline)** | `compress_html` | Strip + Action Map + groups |
| | `compress_html_to_xml` | Grouped XML from HTML |
| **Data (SchemaSnap)** | `schema_table` | Table columns + types + rows |
| | `schema_form` | Form fields + validation |
| | `schema_list` | Lists + standalone selects |
| | `schema_page` | All structured data at once |
| | `schema_json` | Table → JSON records |
| | `schema_csv` | Table → CSV string |
| | `schema_session` | Structured data from browser session |
| | `schema_session_json` | JSON records from session |
| | `schema_session_csv` | CSV from session |
| | `get_page_html` | Clean HTML (`max_chars` default 50000) |
| **Search** | `web_search` | Brave Search (no API keys) |
| **WebMCP** | `webmcp_list_tools` | Chrome 145+ page tools |
| | `webmcp_execute_tool` | Execute WebMCP tool by name |

### Recommended MCP workflow

```
navigate(url, detail="minimal")     # ~200 tokens — action_count, page_class
schema_session_json(max_rows=50)    # data plane — table records
shadow_query("intent:login")        # control plane — what to click
click(sid) → snapshot(diff=true)    # delta only after action
```

See [examples/golden_path/CASE.md](examples/golden_path/CASE.md) for the full playbook.

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

SchemaSnap is the **data plane** complement to the Action Map **control plane**:

| Layer | Question | Tools |
|-------|----------|-------|
| Control | What can I click? | `navigate`, `shadow_query`, `click`, `fill` |
| Data | What data is on the page? | `schema_session_json`, `schema_session`, `schema_csv` |

Default **max_rows=50** per table. Set `max_rows=0` for full export when needed.

**Type inference:** `string`, `integer`, `number`, `currency`, `percentage`, `date`, `email`, `url`.

---

## Known limitations

| Limitation | Workaround |
|------------|------------|
| **Anti-bot / Cloudflare** headless | `page_class: Anti-bot` — stop, don't retry; use headed browser or manual step |
| **`colspan` / `rowspan` tables** | Column alignment may drift; verify row shape |
| **JS-rendered grids** (AG Grid, React Table) | May not use `<table>` — use `shadow_query` + Action Map instead |
| **Closed Shadow DOM** | `navigate(..., capture_mode="dual")` or `"a11y"` |
| **Cross-origin iframes** | Not accessible — `page_class: Iframe-heavy` |
| **Token bombs** | Never default to `detail="full"`, `get_page_html(max_chars=0)`, or `max_rows=0` unless debugging |

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
