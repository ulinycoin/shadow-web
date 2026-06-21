
<!-- badges -->
[![PyPI version](https://badge.fury.io/py/shadow-web.svg)](https://pypi.org/project/shadow-web/)
[![CI](https://github.com/ulinycoin/shadow-web/actions/workflows/test.yml/badge.svg)](https://github.com/ulinycoin/shadow-web/actions/workflows/test.yml)
[![Python](https://img.shields.io/pypi/pyversions/shadow-web.svg)](https://pypi.org/project/shadow-web/)
[![License](https://img.shields.io/github/license/ulinycoin/shadow-web.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/ulinycoin/shadow-web?style=flat)](https://github.com/ulinycoin/shadow-web)

# Shadow Web

**Cut 64–97% of tokens from web pages before your LLM sees them.**  
Open-source Python SDK that flattens Shadow DOM, builds a typed Action Map with semantic groups, and heals broken selectors — no cloud required.

```python
from shadow_web.compressor import process_html

clean_html, actions, groups = process_html(raw_html)
# ✅ actions = [{"id":"1","type":"button","label":"Buy Now","group":"Checkout"}, ...]
# ✅ 164 → 46 tokens on a typical page
```

---

## Pain point

AI agents need to see web pages. But raw HTML is full of `<script>`, `<style>`, inline CSS, and interactive elements buried in Shadow DOM trees that Playwright can't reach. A typical Wikipedia page costs **99K tokens** raw. Your LLM bill doesn't need that.

Shadow Web is what runs **between** the browser and the LLM: a compression layer that keeps only what matters — interactive elements, their labels, and a clean DOM skeleton.

---

## What you get

| Feature | Raw HTML | Playwright locators | Shadow Web |
|---------|----------|-------------------|------------|
| Token cost (Wikipedia) | 99,343 | — | **16,462** (−83%) |
| Token cost (GitHub Trending) | 167,875 | — | **37,833** (−77%) |
| Shadow DOM readable | ❌ | ❌ partial | ✅ flattened |
| Semantic groups | ❌ | ❌ | ✅ Login / Cart / Nav |
| Self-healing selectors | ❌ | ❌ | ✅ local + LLM fallback |
| Works offline | ✅ | ✅ | ✅ |
| PyPI package | — | `playwright` | `shadow-web` |

---

## Who this is for

| You're building … | Why Shadow Web |
|-------------------|----------------|
| A browser-based AI agent | Action Map + self-healing = fewer failures |
| An MCP tool for Cursor/Claude | Built-in MCP server, one-command setup |
| A Playwright scraper that breaks on every deploy | `heal_local.py` catches DOM drift without LLM cost |
| A Shadow DOM-heavy app (Web components, Lit, Angular) | Read-only flatten — no React/Vue breakage |

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

Tools: `navigate`, `snapshot`, `click`, `fill`, `compress_html`, `shadow_query`, `webmcp_list_tools`, `webmcp_execute_tool`.

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

## When NOT to use Shadow Web

- You need **one** `document.querySelector` — use Playwright directly.
- You're building a static site scraper with no interaction.
- The page is plain HTML with no Shadow DOM — overhead isn't worth it.

---

## License

MIT. Free for anything.

---

*Stars are the oxygen of open-source. If Shadow Web saved you tokens or debugging time, ★ the repo.*
