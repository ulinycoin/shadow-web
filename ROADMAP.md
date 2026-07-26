# Shadow Web — Roadmap

> Version: 0.4.0 → 1.0.0
> Last updated: 2026-07-26
> Source: meta-collegiality evaluation (Pro structural + Gemini attacker)

## Current State (v0.4.0)

### Automation Core (8.5/10 production-ready)
- ✅ Playwright MCP server with navigate/click/fill/snapshot
- ✅ DOM compression: 83-99% reduction (Action Map + data-sid)
- ✅ Rendered Text Index — 923× reduction on SPA sites, 100% coverage
- ✅ Self-healing selectors (local cache → LLM heal)
- ✅ Content Outline/Block fetch — on-demand text retrieval, max 600t outline
- ✅ SchemaSnap — tables/forms/lists → JSON/CSV
- ✅ Form fill — agent-usable from JSON profile
- ✅ Anti-detection — Camoufox integration for anti-bot sites
- ✅ Web search via Brave/Yahoo with stealth context
- ✅ Diff snapshots — page mutations only
- ✅ Standalone `shadow-web exec` agent loop (Camoufox default, single-call finish)

### Security Scanner (4.5/10 — weakest link)
- ✅ 8 analysis modules: forms, links, HTTP headers, cookies, SRI, mixed content, CORS probe, page meta
- ✅ CLI: `security_surface_scan.py` with `--crawl-depth`, `--markdown`, `--json`
- ❌ **Critical:** `analyze_forms()` blind to `<input>` outside `<form>` — zero findings on Astro/Next.js/Vite/SvelteKit
- ❌ CORS probe: same-origin Origin only, never triggers actual CORS
- ❌ Crawl depth paradox: more pages = more nav noise = fewer net findings
- ❌ No JS bundle analysis, no CWE mapping, no incremental scanning
- ❌ No Docker image, health endpoints, graceful browser restart
- ❌ CLI creates new browser per page (3-8s cold start each)

## Strategic Direction

shadow-web has two distinct products living in one repo:

1. **Automation Core** (MCP server, DOM compression, Content Index) — near-production. Primary distribution channel.
2. **Security Scanner** — pre-alpha. Needs architectural rework before it's usable on modern web.

**Decision:** Fix the automation core gaps first (operational infra, PyPI publish, Docker). Then fix the security scanner's architectural issues. Monetization comes after PyPI traction.

## Roadmap

### Phase 1: Ship the Core (v0.4.0 — July 2026)

**Goal:** Make shadow-web installable and runnable without cloning the repo.

```
Priority: PyPI publish > Docker > docs > monitoring
Effort: ~3-5 days
```

| Task | Why | Effort |
|------|-----|--------|
| **PyPI publish** (`shadow-web-mcp` package) | Primary distribution. `pip install shadow-web-mcp && uvx shadow-web-mcp` = instant MCP server | 1d |
| **Docker image** (multi-arch) | OCI deploy, CI/CD, isolated browser env | 1d |
| **Health endpoint** (`GET /health` → browser status, session count) | Gateway integration, monitoring | 0.5d |
| **Graceful browser restart** (SIGUSR1 → new context, drain old) | Reliability — browser memory leaks kill sessions | 0.5d |
| **README overhaul** — install guide, CLI reference, local dev | Adoption. Current README is spec, not guide | 1d |
| **GitHub Actions CI** — lint + test on push | Quality gate. Currently tests only run locally | 0.5d |

**Deliverable:** `pip install shadow-web-mcp && uvx shadow-web-mcp` works on a fresh macOS/Linux machine.

---

### Phase 1.5: Standalone `exec` agent (v0.4.x — July 2026)

**Goal:** Compete on product shape with BetterWright-style sub-agent delegation — without copying vault/cloaking.

| Task | Status | Detail |
|------|--------|--------|
| **CLI `shadow-web exec`** | Done | observe→act→verify loop, one JSON envelope |
| **OpenAI-compatible adapter** | Done | DeepSeek / OpenAI / OpenRouter via `base_url` |
| **Single-call finish** | Done | `done(finalAnswer)` + read-only prompt rule |
| **Named sessions** | Done | `~/.shadow-web/sessions/<name>` persistent profiles |
| **`--browser camoufox|chromium`** | Done | Default Camoufox (MCP-parity); Chromium fallback if unavailable |
| Live view / vault | Out of scope v1 | Differentiator later, not required for exec MVP |

**Deliverable:** `pip install "shadow-web[agent]" && shadow-web exec "…"` returns `{ok, answer, steps, usage, url}`.

---

### Phase 2: Fix the Security Scanner (v0.5.0 — August 2026)

**Goal:** Scanner produces useful findings on modern (SSG/SPA) web applications.

```
Priority: orphan inputs > bundle analysis > CORS > nav filter
Effort: ~5-7 days
```

| Task | Effort | Detail |
|------|--------|--------|
| **Orphan input detection** | 1d | `analyze_orphan_inputs()` — regex-based detection of `<input>`, `<textarea>`, `<select>`, `[contenteditable]`, `[role=textbox]` outside `<form>` wrappers. Tag them as `unwrapped_field`. Report count + location. Pipeline: extracted from clean HTML during `analyze_surface()`, merged into findings. |
| **API endpoint discovery** | 1.5d | Scan JS bundles and inline scripts for `fetch()`, `axios`, `$.ajax`, `/api/`, `/graphql` patterns. Collect method + path. Report as `api_endpoints` in findings. Basic (regex, no runtime analysis), sufficient to surface the attack surface. |
| **CORS probe: cross-origin** | 1d | Second CORS pass with explicit evil Origin (`https://evil.com`) only when `--aggressive` flag is set. Same-origin probe remains default. Mark same-origin results as `cors_same_origin` in findings to avoid false claims. |
| **Crawl depth: nav dedup** | 0.5d | `analyze_links()` dedup by page role (nav, content, legal) not by URL path. Links with identical `aria-label` / text / href-pattern from nav sections count once. |
| **CWE mapping** | 1d | Add `cwe_id` field to all Finding objects. Map existing detectors: cookie flags → CWE-614, SRI → CWE-345, HSTS → CWE-319, XFO → CWE-1021, CSP → CWE-693. |
| **SQLi/NoSQLi probe** | 1d | After form/orphan input detection, inject test payloads (`' OR 1=1--`, `"'"'''` etc.) into text inputs via Playwright, check response for SQL errors. Requires Page-level interaction — not static analysis. |

**Deliverable:** Scanner returns >=1 actionable finding on an Astro/Next.js site (current: 0).

---

### Phase 3: Monetization (v0.6.0 — August-September 2026)

**Goal:** Revenue without killing open-source adoption.

```
Priority: Compression API > Cloud Heal > Pro features
Effort: ~3-5 days
```

| Tier | What | Price |
|------|------|-------|
| **Free (OSS)** | MCP server, local SDK, self-healing (local cache only), SchemaSnap, security scanner | Free |
| **Compression API** | Cloud-hosted `/v1/compress` — no Playwright/Playwright install needed | $0.001/request |
| **Cloud Heal** | LLM-powered selector healing, cloud CORS probe, rate: 100/day free, then $0.01/heal | Freemium |
| **Pro Scanner** | Cross-origin CORS, API endpoint discovery, SQLi probe, incremental scanning | $20/mo |

**Distribution:**
- PyPI: `shadow-web-mcp` (free, open-source)
- Cloud API: FastAPI on GCP Cloud Run (paid)
- Plugin marketplaces: Claude Code `/plugin marketplace add ulinycoin/shadow-web` (lead gen)
- `npx skills add ulinycoin/shadow-web` for Codex/Cursor ecosystem

**Adoption milestones:**
- 100 GitHub stars → publish Compression API
- 500 stars → add Cloud Heal tier
- 1000 stars → full Pro Scanner tier

---

### Phase 4: Quality & Scale (v0.7.0+ — Q4 2026)

**Goal:** Enterprise-adjacent reliability.

| Task | Why |
|------|-----|
| **Structured output contract** — `Claim[]`, `Risk[]`, `confidence` schemas for all security scanner findings | Prose merge on security findings is fuzzy without schema. JSON-enforced, not LLM-enforced |
| **Incremental scanning** — scan only diff from last run, not full page | Saves 80%+ on repeat scans of same domain |
| **Metrics endpoint** — `/metrics` with Prometheus-style counters (requests, latency, errors, findings) | Observability for cloud deployment |
| **Rate limiting** — per-key RPM, daily cap | Required for paid API tier |
| **Session persistence** — save/restore Playwright browser context between restarts | Zero-downtime deploys |
| **Shadow DOM capture_mode auto-detection** — no manual `capture_mode="dual"` | Removes user friction |

---

## Known Gaps (tracked, not blocking)

| Gap | Status | Will fix in |
|-----|--------|-------------|
| Form analyzer blind on Astro/Next.js/Vite | ❌ Critical | Phase 2 (orphan input detection) |
| CORS probe same-origin only | ❌ | Phase 2 (aggressive flag + labeling) |
| No Docker image | ❌ | Phase 1 |
| No health endpoint | ❌ | Phase 1 |
| No metrics/telemetry | ❌ | Phase 4 |
| No API-level vuln detection | ❌ | Phase 2 (API endpoint discovery) |
| No CWE mapping | ❌ | Phase 2 |
| Russian sites mostly blocked | ❌ Known | Won't fix — out of scope |
| PyPI not published | ❌ | Phase 1 |

## Past Decisions (why not)

| Decision | Rationale |
|----------|-----------|
| No Kubernetes chart | Premature. Docker + compose sufficient until >10 paying users |
| No Terraform module | Premature. Manual OCI/gcloud deploy until API has traffic |
| No JS runtime analyzer | Puppeteer/Playwright coverage of JS surface is shallow. Better to detect endpoints via regex + fuzz via Playwright |
| No browserless/chromium-as-a-service | Latency + cost. Local browser always faster |
| No enterprise SSO | No users yet. When requested, implement |
