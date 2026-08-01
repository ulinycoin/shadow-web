# Show HN draft — Shadow Web 0.4.1 (DO NOT PUBLISH without OK)

**Title (≤80 chars):**
Show HN: Shadow Web – cut agent browser tokens ~2× vs dumping HTML

**URL:**
https://github.com/ulinycoin/shadow-web

---

## Body

I got tired of browser agents stuffing 50–200k tokens of raw HTML into the model for a one-line answer.

Shadow Web is a local-first Python SDK + MCP + `shadow-web exec` agent:

1. Flatten Shadow DOM
2. Build a short Action Map (`data-sid` + labels + groups)
3. SchemaSnap → tables/forms/lists as JSON
4. On-demand content index for long pages
5. Bounded observe→act→verify loop that returns one JSON envelope (`complete` / `truncated` / `next_hint`)

No hosted browser cloud. Camoufox by default; Chromium opt-in.

### 30-second demo

```bash
pip install "shadow-web[agent]"
playwright install chromium
export DEEPSEEK_API_KEY=...
SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK=1 \
  shadow-web exec "What is the heading on https://example.com?" --browser chromium
```

Or: `bash scripts/demo_30s.sh`

### Fair benchmark (not compression theatre)

Same model (`deepseek-chat`), same goals, shared answer checks:

- Baseline: Playwright HTML → one LLM call (HTML capped at 24k tokens)
- Shadow Web: `shadow-web exec` tool loop

**10/10 task success** on both sides.  
**6/10 token wins**, **1.6×** fewer tokens overall vs the naive HTML dump  
(tiny pages lose to agent overhead; Wikipedia / GitHub / forms / MDN win).

Reproduce:

```bash
DEEPSEEK_API_KEY=... python benchmarks/agent_tasks.py
```

Raw: `benchmarks/results/agent_tasks_live_20260801_094423_corrected.json`

### Ask HN

What browser task would you trust an open-source local agent with first — forms, catalogs, or “read this page and answer”?

---

## Notes (internal, strip before post)

- Do not claim 64–99% or 7.3× vs raw HTML as the headline — use vs Playwright→LLM baseline.
- PyPI: publish 0.4.1 only after explicit OK.
- Soften any unverified success % until 10-task JSON is committed.
- No Price Monitor / Managed API pitch in this post.
