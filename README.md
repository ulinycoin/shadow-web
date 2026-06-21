# Shadow Web: Shift-Left Web SDK & API for AI Agents

Shadow Web is an open-source Python SDK for LLM/AI agents. It flattens Shadow DOM and same-origin iframes (read-only), strips HTML bloat, builds a grouped **Action Map**, and supports local self-healing selectors — no cloud required for the core workflow.

## Why Shadow Web?

1. **Token savings:** Measured **64–97% XML reduction** vs raw HTML on benchmark pages (see table below).
2. **Action Map + semantic groups:** LLMs interact via short IDs and logical blocks (`Login Form`, `Navigation`).
3. **Local-first:** DOM capture + compression run on your machine. Optional local MCP server for Cursor/Claude.

---

## Benchmarks (measured)

Запуск локально:

```bash
pip install tiktoken           # опционально: для точного подсчета токенов (cl100k_base)
python benchmarks/run.py       # запуск бенчмарков через Playwright (Hacker News, Wikipedia, GitHub)
```

**Метод подсчета:** По умолчанию используется оценка `chars/4`. При установленном пакете `tiktoken` подсчет токенов производится с помощью кодировщика `cl100k_base` (GPT-4 / DeepSeek).

| Page | Raw HTML (tokens) | Grouped XML (tokens) | Actions | Token Reduction |
|------|-------------------|----------------------|---------|-----------------|
| Hacker News | 8,637 | 6,704 | 227 | **-22.4% (1.3x)** |
| Wikipedia (Web Scraping) | 99,343 | 16,462 | 501 | **-83.4% (6.0x)** |
| GitHub Trending | 167,875 | 37,833 | 1,290 | **-77.5% (4.4x)** |

**Примечания:**
- **Grouped XML** — это структурированная XML-карта действий (`xml_map`), которая отправляется LLM-агенту.
- **Actions** — количество интерактивных элементов на странице.
- Бенчмарки используют модуль `dom_capture` для выпрямления Shadow DOM и same-origin iframe-ов в реальном времени перед сжатием.

---

## Installation

```bash
pip install shadow-web
playwright install chromium   # required for ShadowPage / MCP navigate tools
```

**Extras:**

```bash
pip install "shadow-web[mcp]"          # Cursor / Claude MCP server
pip install "shadow-web[server]"         # optional FastAPI heal API
pip install "shadow-web[browser-use]"  # browser-use integration deps
pip install "shadow-web[all]"            # everything
```

**From source (development):**

```bash
git clone https://github.com/ulinycoin/shadow-web.git
cd shadow-web
pip install -e ".[dev,server,mcp]"
playwright install chromium
```

### Local MCP (Cursor / Claude Desktop)

```json
{
  "mcpServers": {
    "shadow-web": {
      "command": "shadow-web-mcp"
    }
  }
}
```

Optional `.env`:

```bash
DEEPSEEK_API_KEY=...                    # for /v1/heal LLM fallback
SHADOW_WEB_HEAL_URL=http://127.0.0.1:8000/v1/heal
```

---

## Quick start

### Compress HTML (no browser)

```python
from shadow_web.compressor import process_html, generate_grouped_xml_map

raw_html = open("page.html").read()
clean_html, action_map, groups = process_html(raw_html)
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
    clean_html, xml_map = shadow.refresh()
    print(shadow.capture_stats)  # shadow_hosts, iframes, etc.
```

### shadow_grep — filter before LLM

Instead of sending the full Action Map (500+ elements on GitHub/Wikipedia), query relevant actions:

```python
# After shadow.refresh()
result = shadow.query("intent:login")           # QueryResult
text = shadow.query("intent:login", fmt="terse")  # compact LLM text
subset = shadow.query("type:button; group:Checkout", fmt="xml")

print(text)
# # shadow_grep: intent:login (2/1290)
# @1 button Sign in [Login Form]
# @2 input[email] Email [Login Form]
```

**Query syntax (AND semantics):**

| Filter | Example |
|--------|---------|
| Type | `type:button`, `type:input` |
| Group | `group:Login Form` |
| Intent preset | `intent:login`, `intent:checkout`, `intent:buy` |
| ID list | `id:1,3,5` |
| Label regex | `label~/checkout/i` |
| Placeholder | `placeholder~email` |
| Href | `href:/cart` |
| Free text | `checkout` (matches label/group/type) |
| Combined | `type:button intent:login` or `type:button; intent:login` |

**MCP:** `shadow_query(query="intent:login", format="terse")` after `navigate(url)`.

### WebMCP Bridge (Chrome 145+ preview)

When a page exposes `document.modelContext` tools, Shadow Web switches to **webmcp mode** automatically — no DOM snapshot needed:

```python
shadow = ShadowPage(page, prefer_webmcp=True)
clean_html, xml_map = shadow.refresh()

print(shadow.interaction_mode)  # "webmcp" or "action_map"
if shadow.webmcp.available:
    result = shadow.execute_tool("search_products", {"query": "dog toy"})
    # or by Action Map id:
    shadow.execute_tool_by_sid("1", {"query": "dog toy"})
```

**MCP tools:**
- `webmcp_list_tools()` — detect tools on current page
- `webmcp_execute_tool(name, arguments='{"query":"x"}')`

Fallback: if no WebMCP tools → normal Action Map + Shadow DOM flatten (unchanged).

### Page diff — delta snapshots (opt-in)

After the first full snapshot, send only what changed — skeleton (url, title, group names) + appeared/changed/disappeared actions with breadcrumbs:

```python
shadow.refresh()              # full Action Map XML (baseline)
shadow.click("3")
_, delta_xml = shadow.refresh(diff=True)  # delta only on same URL

print(shadow.diff_terse())
# # diff https://example.com
# groups: Login Form, Navigation
# ## appeared
# @4 Checkout > button[Buy now] (#4)
```

**MCP:** `snapshot(diff=True)` after `navigate(url)` — first call is always full; subsequent calls on the same URL return delta XML + `diff_terse`.

URL change resets baseline → next snapshot is full again.

### Phase 2 — a11y dual mode + verified heal

**Dual capture** (`capture_mode="auto"` by default) — DOM flatten + CDP Accessibility tree supplement for closed Shadow DOM:

```python
shadow = ShadowPage(page, capture_mode="auto")  # dom | a11y | dual | auto
shadow.refresh()
print(shadow.capture_stats)  # capture_source, a11y_supplement_nodes, shadow_hosts
```

- `auto` — a11y supplement when shadow hosts exist and AX tree exposes uncovered interactives
- `dual` — always merge uncovered a11y nodes
- a11y bindings click via CDP `backendDOMNodeId` (closed shadow safe)

**Verified heal** — selectors validated before cache (local + API):

```python
shadow = ShadowPage(page, verify_heal=True, heal_api_url="http://127.0.0.1:8000/v1/heal")
```

Server `/v1/heal` loads `context_html` in headless Chromium; rejects unverified selectors (422).

Env: `SHADOW_WEB_API_KEYS=key1,key2`, `SHADOW_WEB_RATE_LIMIT=100` (per key / 24h).

### Self-healing (local → LLM)

```python
shadow = ShadowPage(page, heal_api_url="http://127.0.0.1:8000/v1/heal")
shadow.click("3")  # binding → local fuzzy heal → LLM API fallback
```

---

## Folder structure

```
src/shadow_web/
  compressor.py      # DOM strip + Action Map + groups
  dom_capture.py     # Shadow DOM / iframe flatten (in-browser, read-only)
  grouping.py        # Semantic groups (forms, nav, modals)
  heal_local.py      # Local selector heal + ~/.shadow-web/heal_cache.json
  query.py           # shadow_grep (type:, intent:, label~, AND filters)
  webmcp.py          # WebMCP bridge (detect + executeTool)
  diff.py            # Page diff (skeleton + delta XML)
  a11y_capture.py    # CDP Accessibility dual capture (closed shadow)
  verified_heal.py   # Playwright selector verification
  wrapper.py         # ShadowPage (Playwright)
  mcp/server.py      # Local MCP tools
server/              # Optional FastAPI (/v1/compress, /v1/heal)
benchmarks/          # Token benchmarks + fixtures
tests/
```

---

## API server (optional, local)

```bash
uvicorn server.main:app --reload --port 8000
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

```bash
curl -X POST http://localhost:8000/v1/compress \
  -H "Content-Type: application/json" \
  -d '{"html": "<html><body><button>Buy</button></body></html>"}'
```

---

## License

MIT License. Free for development and commercial use.
