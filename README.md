
<!-- badges -->
[![PyPI version](https://img.shields.io/pypi/v/shadow-web.svg)](https://pypi.org/project/shadow-web/)
[![CI](https://github.com/ulinycoin/shadow-web/actions/workflows/test.yml/badge.svg)](https://github.com/ulinycoin/shadow-web/actions/workflows/test.yml)
[![Python](https://img.shields.io/pypi/pyversions/shadow-web.svg)](https://pypi.org/project/shadow-web/)
[![License](https://img.shields.io/github/license/ulinycoin/shadow-web.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/ulinycoin/shadow-web?style=flat)](https://github.com/ulinycoin/shadow-web)
[![MCP Badge](https://lobehub.com/badge/mcp/ulinycoin-shadow-web)](https://lobehub.com/mcp/ulinycoin-shadow-web)

# Shadow Web

**Cut 64–99% of tokens from web pages before your LLM sees them — then extract structured data.**  
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
| An MCP tool for Cursor/Claude | Built-in MCP server with **26 tools**, one-command setup |
| A Playwright scraper that breaks on every deploy | `heal_local.py` catches DOM drift without LLM cost |
| A Shadow DOM-heavy app (Web components, Lit, Angular) | Read-only flatten — no React/Vue breakage |
| **An agent that needs data from web pages** | SchemaSnap parses tables, forms, and lists into clean JSON |
| **Attack surface / security recon** | `security_scan.py` + CLI — forms, links, page_class rules (not pentest) |
| **Competitor monitoring & SEO content** | Shallow multi-site scans with token-bounded Action Map |
| **SaaS onboarding automation** | AgentOps form fill — schema knows fields, LLM only picks values |

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

Run the optional API locally:

```bash
export DEEPSEEK_API_KEY="..."
export SHADOW_WEB_API_KEYS="local-secret"
shadow-web-server
```

Remote and production requests are rejected unless `SHADOW_WEB_API_KEYS` is configured.

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

### Attack surface security scan

Automated **surface mapping** (not penetration testing): forms, links, `page_class`, **HTTP security headers** (HSTS, CSP, XFO, nosniff, CORS), **cookie flags** (Secure, HttpOnly, SameSite), Markdown/JSON reports.

```bash
pip install -e ".[mcp]"
playwright install chromium

# Single URL
python scripts/security_surface_scan.py https://example.com

# Shallow same-domain crawl + reports (--no-headers / --no-cookies to skip checks)
python scripts/security_surface_scan.py https://yoursite.com \
  --crawl-depth 1 --max-pages 20 \
  --json report.json --markdown report.md
```

Rule engine (importable without browser):

```python
from shadow_web.security_scan import analyze_surface, render_markdown_report

result = analyze_surface(
    "https://app.example.com/login",
    clean_html=html,
    action_map=actions,
    page_class="Static",
)
# findings: FORM_PASSWORD_GET, HEADER_MISSING_CSP, COOKIE_MISSING_HTTPONLY, ...
```

Example output: [examples/security_scan/localpdf-full-report.md](examples/security_scan/localpdf-full-report.md) (20 pages, 0 critical/high on public marketing layer). Header-only sample: [localpdf-headers-only.json](examples/security_scan/localpdf-headers-only.json).

**Does not test:** XSS, SQLi, auth bypass, or deep TLS/cipher analysis. Use only on authorized targets.

### AgentOps form fill

See the full **[Form Fill](#agentops-form-fill)** section below. Quick start:

```bash
python scripts/form_fill_demo.py https://httpbin.org/forms/post \
  --profile examples/form_fill/profile.json --json plan.json
```

### Competitor intelligence scan

Token-bounded weekly audit for programmatic SEO / compare pages:

```bash
python scripts/localpdf_competitor_scan.py --json reports/scan.json
```

Playbook: [examples/localpdf/CASE.md](examples/localpdf/CASE.md)

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
                    content_index.py → block outline (p0, p1…) + on-demand fetch
                          ↓
                    shadow_grep.py → filter before LLM
                          ↓
                    schema_snap.py → structured data (tables, forms, lists)
                          ↓
                    security_scan.py → attack surface rules (forms, links)
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
├── content_index.py   # Content block outline + on-demand text fetch
├── dom_capture.py     # Shadow DOM / iframe flatten (in-browser, read-only)
├── grouping.py        # Semantic groups (forms, nav, modals)
├── schema_snap.py     # Tables, forms, lists → JSON/CSV export
├── form_fill.py       # AgentOps form fill (auto_fill / ask / handoff)
├── security_scan.py   # Attack surface rule engine (forms, links, page_class)
├── heal_local.py      # Local selector heal + ~/.shadow-web/heal_cache.json
├── query.py           # shadow_grep (type:, intent:, label~, AND)
├── webmcp.py          # WebMCP bridge (Chrome 145+)
├── diff.py            # Page diff (skeleton + delta XML)
├── a11y_capture.py    # CDP Accessibility dual capture
├── verified_heal.py   # Playwright selector verification
├── wrapper.py         # ShadowPage (Playwright)
├── mcp/server.py      # Cursor / Claude MCP tools
└── server.py          # FastAPI (/health, /v1/compress, /v1/heal)

scripts/
├── security_surface_scan.py   # CLI: crawl + JSON/Markdown security reports
├── form_fill_demo.py          # AgentOps form fill demo (plan + execute + wizard)
├── localpdf_competitor_scan.py # Multi-site competitor snapshot
├── smoke_install.sh           # Install + pytest + one live navigate
└── cursor-setup.sh            # MCP one-command setup

deploy/
└── oci.sh                     # Oracle Cloud deployment script
```

---

## Examples

| Path | What it demonstrates |
|------|----------------------|
| [examples/golden_path/](examples/golden_path/) | Full agent loop + token budget |
| [examples/security_scan/](examples/security_scan/) | Attack surface scan reports (JSON + Markdown) |
| [examples/localpdf/](examples/localpdf/) | Competitor intel playbook + sample scan |
| [examples/form_fill/](examples/form_fill/) | AgentOps form fill playbook + profile sample |
| [examples/browser_use/](examples/browser_use/) | browser-use integration |

---

## Benchmarks

| Page | Raw HTML (tokens) | Grouped XML (tokens) | Actions | Reduction |
|------|-------------------|----------------------|---------|-----------|
| Hacker News | 8,637 | 6,704 | 227 | **−22% (1.3×)** |
| Wikipedia (Web Scraping) | 99,343 | 16,462 | 501 | **−83% (6.0×)** |
| Wikipedia + Content Index | 57,778 | 599¹ | — | **−99% (96×)** |
| GitHub Trending | 167,875 | 37,833 | 1,290 | **−77% (4.4×)** |

¹ `content_outline(max_tokens=600)` — 141 blocks, agent picks what to fetch.
Full benchmark: `python benchmarks/run.py`

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

### All 26 tools

| Category | Tool | What it does |
|----------|------|--------------|
| **Browse** | `navigate` | Open URL → snapshot (`detail`: minimal / terse / xml / full) |
| | `snapshot` | Refresh page; `diff=true` for delta only |
| | `click`, `fill` | Interact by `data-sid` |
| **AgentOps** | `form_fill_plan` | Profile JSON → fill plan (auto_fill / ask / handoff) |
| | `form_fill_execute` | Run plan (+ answers); validation_error feedback |
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
| **Content** | `content_outline` | Token-budgeted rendered-text index + coverage diagnostics |
| | `content_blocks` | Fetch selected text blocks by compact IDs |
| **Search** | `web_search` | Brave Search (no API keys) |
| **WebMCP** | `webmcp_list_tools` | Chrome 145+ page tools |
| | `webmcp_execute_tool` | Execute WebMCP tool by name |

### Recommended MCP workflow

**Browse + extract:**
```
navigate(url, detail="minimal")     # ~200 tokens — action_count, page_class
schema_session_json(max_rows=50)    # data plane — table records
shadow_query("intent:login")        # control plane — what to click
click(sid) → snapshot(diff=true)    # delta only after action
```

**Long-form content:**
```
navigate(url, detail="minimal")
content_outline(max_tokens=600)              # p0, p1… + coverage/mode/signals
content_blocks(ids="p3,p7", max_tokens=2000) # selected text only
```

The Rendered Text Index covers semantic articles and `div`/`span`-heavy apps
without site-specific selectors. Every readable text node belongs to one
bounded block; headings and paragraphs act as boundary signals rather than an
allowlist. The outline summary reports `coverage`, extraction `mode`, and
retained price `signals` so a large reduction cannot hide missing content.

**Form fill (AgentOps):**
```
form_fill_plan(profile='{"email":"...","name":"..."}', url="https://...")
form_fill_execute(plan='...', answers='{}')
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

## AgentOps Form Fill

**Give a URL + JSON profile. Shadow Web reads the form schema, fills safe fields, asks on ambiguity, and hands off on blockers — without sending 50k tokens of HTML to your LLM.**

Classic browser agents: entire page → LLM guesses selectors → silent validation failures.  
Shadow Web AgentOps split:

```
Data plane:   schema_form  → { name, type, required, minlength, options }
Control plane: Action Map → fill(sid), click(sid)
LLM job:      profile → values only (not “where to click”)
```

### Execution modes

| Mode | When | What happens | Example |
|------|------|----------------|---------|
| **`auto_fill`** | Safe, typed profile fields | `fill(sid, value)` immediately | `email`, `name`, `company`, `role`, `phone` |
| **`ask`** | Ambiguous or missing data | Structured question returned in `plan.questions` | textarea bio, `<select>` country, “How did you hear about us?” |
| **`handoff`** | Human-only UI | Stop with `status: handoff` — no blind retries | CAPTCHA, OAuth, file upload, custom datepicker, `page_class: Anti-bot` |

**Design principle:** never promise full autonomy. Pilots survive because blockers surface early instead of failing silently.

### Status codes

| `status` | Meaning |
|----------|---------|
| `completed` | Auto-fills ran (+ optional submit click) |
| `questions_pending` | Operator must answer `questions[]` then re-run with `answers` |
| `handoff` | Human step required (`blockers[]`) |
| `validation_error` | Client validation failed after fill — see `errors[]` with `sid` + field |

### Validation feedback loop

After every `fill()`:

1. `snapshot(diff=true)` — catch appeared error messages  
2. HTML5 `checkValidity()` on live DOM  
3. Preflight checks (e.g. email format before fill)

```json
{
  "status": "validation_error",
  "errors": [{
    "sid": "2",
    "field": {"name": "email", "kind": "email"},
    "message": "Please include an '@' in the email address",
    "source": "constraint_validation"
  }]
}
```

### Quick start (Python)

```python
from shadow_web.form_fill import (
    validate_profile,
    plan_from_session,
    apply_question_answers,
    execute_form_fill_plan_async,
    execute_form_fill_plan_multi_step_async,
)

validation = validate_profile(profile)  # warns: unknown keys like "companny"
plan = plan_from_session(
    url=shadow.last_url,
    clean_html=shadow.clean_html,
    action_map=shadow.action_map,
    profile=profile,
    page_class=shadow.page_class,
)

result = await execute_form_fill_plan_async(shadow, plan, validate=True)

# Multi-step wizard (enterprise onboarding)
result = await execute_form_fill_plan_multi_step_async(
    shadow, profile, max_steps=3, answers={"form0_field4": "Senior engineer"}
)
```

### CLI demo

```bash
pip install -e ".[mcp]"
playwright install chromium

# Plan only
python scripts/form_fill_demo.py https://httpbin.org/forms/post \
  --profile examples/form_fill/profile.json --json plan.json

# Execute safe fills
python scripts/form_fill_demo.py URL --profile examples/form_fill/profile.json --execute

# Multi-step wizard
python scripts/form_fill_demo.py URL --profile profile.json --execute --multi-step
```

### MCP workflow

```
form_fill_plan(profile='{"email":"you@co.com","name":"Ada","company":"Acme"}', url="https://...")
form_fill_execute(plan='...', answers='{"form0_field4":"..."}')
# Wizard: form_fill_execute(profile='...', multi_step=true, max_steps=3)
```

Playbook: [examples/form_fill/CASE.md](examples/form_fill/CASE.md)

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
| **Security scan scope** | Surface mapping — forms, links, HTTP headers, cookie flags; no XSS/SQLi; SPAs may need `capture_mode=dual` |
| **Security scan authorization** | Run only on systems you own or have explicit permission to test |
| **Form fill custom widgets** | MUI Select, date pickers → `handoff`; use `capture_mode=dual` for Shadow DOM forms |

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
