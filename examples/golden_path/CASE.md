# Golden Path — agent workflow with token budget

Reproducible demo of the full Shadow Web pipeline: **structure → data → interact**.

```bash
pip install -e ".[mcp]"
playwright install chromium
python examples/golden_path/demo.py          # 2 live sites + token table
python examples/golden_path/demo.py --quick  # smoke (1 site)
```

Optional accurate tokens: `pip install tiktoken`

---

## The loop

```
┌─────────────────────────────────────────────────────────────┐
│  RAW HTML (50K–170K tokens) — never send this to the LLM   │
└───────────────────────────────┬─────────────────────────────┘
                                │ compressor (in navigate/snapshot)
                                ▼
┌─────────────────────────────────────────────────────────────┐
│  CONTROL PLANE — what to click (Action Map + shadow_grep)   │
│  navigate(detail="minimal")  →  url, action_count, groups   │
│  shadow_query("intent:login") → 5–30 matching elements      │
│  click(sid) / fill(sid)                                     │
│  snapshot(diff=true, detail="terse") → delta only           │
└───────────────────────────────┬─────────────────────────────┘
                                │ same clean_html
                                ▼
┌─────────────────────────────────────────────────────────────┐
│  DATA PLANE — what the page contains (SchemaSnap)           │
│  schema_session_json(max_rows=50) → [{col: val}, ...]       │
│  schema_session_csv()         → ready-to-save CSV           │
│  schema_session()             → tables + forms + lists      │
└─────────────────────────────────────────────────────────────┘
```

**One philosophy:** every tool defaults to a bounded payload. Escalate (`detail=full`, `max_rows=0`, `max_chars=0`) only when debugging.

---

## MCP playbook (copy-paste for Cursor)

### Read a page + extract table data

```
navigate("https://en.wikipedia.org/wiki/Web_scraping", detail="minimal")
schema_session_json(max_rows=10)
```

**3 tool calls.** No full HTML crosses the LLM context.

### Find a button, click, see what changed

```
navigate(url, detail="minimal")
shadow_query("type:button label~/submit/i", format="terse")
click("<sid from query>")
snapshot(diff=true, detail="terse")
```

### Static HTML file (no browser)

```
schema_json(html=open("page.html").read(), max_rows=50)
schema_csv(html=..., table_index=0)
```

### Form inspection before fill

```
navigate(url, detail="minimal")
schema_session()          → forms[].fields (name, type, required, label)
shadow_query("intent:login", format="terse")
fill("<sid>", "value")
```

---

## Naive vs Shadow (typical)

| Approach | Tool calls | LLM sees |
|----------|------------|----------|
| **Naive** | dump HTML + parse | 50K–170K raw tokens + schema blob |
| **Shadow** | minimal + schema_json + query | ~500–3K tokens total |

Exact numbers: run `python examples/golden_path/demo.py`.

**Sample output (W3Schools, `--quick`):**

| Metric | Tokens |
|--------|--------|
| Raw HTML | 192,295 |
| Shadow pipeline (minimal + schema_json + query) | **986** |
| Savings vs naive raw+schema | **−99%** |

---

## When to use which tool

| Goal | Tool | Not this |
|------|------|----------|
| Is page loaded? How many actions? | `navigate(detail="minimal")` | `detail="full"` |
| Table as JSON records | `schema_session_json()` | `get_page_html()` |
| Table as CSV file | `schema_session_csv()` | raw HTML → manual parse |
| Find login / cart / nav | `shadow_query("intent:login")` | read entire action_map |
| Click / type | `click(sid)`, `fill(sid)` | CSS selectors |
| After click — what changed? | `snapshot(diff=true, detail="terse")` | full re-navigate |
| Offline HTML string | `schema_json(html)` | browser session |
| Debug broken selector | `detail="xml"` once | repeated full snapshots |

---

## Real-world case: LocalPDF competitor scan

Weekly audit of 5 PDF competitor homepages — see [examples/localpdf/CASE.md](../localpdf/CASE.md).

Shadow Web role: **`minimal` + `shadow_query`** per site (~5–15K tok/week), not full-page dumps.

---

## Smoke / CI

```bash
bash scripts/smoke_install.sh
```

Installs `[mcp]`, runs unit tests + `demo.py --quick`.
