# Shadow Web — Roadmap

> Version: 0.4.1 → 1.0.0
> Last updated: 2026-08-04
> Status: open-source engine (PyPI + GitHub) + validated monetization direction (price/stock monitor concierge)

## Current State (v0.4.1)

### Automation Core (production-ready)
- ✅ Playwright/Camoufox browser execution: navigate/click/fill/snapshot, Shadow DOM flattening, a11y supplement, diff snapshots, web search (Brave/Yahoo)
- ✅ DOM compression: Action Map + data-sid, grouped XML, terse payloads (5–30x typical, up to 923x on dense SPA pages)
- ✅ Rendered Text Index: outline → selective content blocks, 100% coverage, price-signal retention
- ✅ SchemaSnap: tables/forms/lists → JSON/CSV, session-native
- ✅ Self-healing selectors: local fuzzy cache → LLM fallback with Playwright verification, atomic cache writes
- ✅ Form fill: JSON profile → plan → execute with ask/handoff, `auto_submit=False` default, URL guard
- ✅ Standalone `shadow-web exec` agent loop (OpenAI-compatible, bounded, single-call finish)
- ✅ MCP server (stdio): 26 tools incl. thick `agent_run`, thin navigate/schema/content tools with hard caps
- ✅ Security surface scanner: 8 modules (forms, links, headers, cookies, SRI, mixed content, CORS, page meta) — not a pentest tool
- ✅ 233+ tests passing, CI (GitHub Actions), PyPI + Claude Code plugin marketplace + LobeHub manifest

### Distribution
- ✅ PyPI: `pip install shadow-web`, `uvx shadow-web-mcp`
- ✅ GitHub: ulinycoin/shadow-web (MIT)
- ✅ Claude Code plugin marketplace + Vercel `npx skills`
- ✅ Demo scripts: `scripts/demo_30s.sh`, `scripts/form_fill_demo.py`, `scripts/smoke_install.sh`
- ⚠️ GitHub stars: 9 (low). OSS is distribution/credibility layer, not a revenue channel yet.

## Monetization Direction (validated, 2026-08-04)

**Primary: managed competitor price + stock monitor for DTC/e-commerce (concierge-first).**

- Evidence: `reports/price-probe.json` — 93.3% price extraction on 15 live Shopify/WooCommerce/DTC stores incl. closed Shadow DOM
- Form: paid concierge / design partner, not SaaS dashboard on day 1
  - 10 competitor SKUs, daily scan, change log (price/stock/promo), verified report, $149–299 pilot → $299–499/mo
- Target: EN DTC brands (20–200 SKU) and small e-commerce agencies
- Validation gate: 1 paid pilot OR 2 paid-price agreements after free baseline; stop if 10 contacts + thread yield zero response

**Not in scope for monetization (yet):**
- Hosted browser cloud / execution API (competes with Browserbase/Browser Use; infra cost before revenue)
- Closing MIT core features behind paywall (kills OSS adoption)
- Security scanner as paid product (quality gaps: orphan inputs, CORS false model, crawl noise — reputational risk)
- Anti-bot bypass as a service (Camoufox is not a Cloudflare/CAPTCHA guarantee)

## Phases

### Phase A — Price Watch MVP (weeks 1–3)
- [ ] Sample competitor change report on 10 public DTC URLs (price/stock/promo/confidence/needs_review)
- [ ] Quality gates: HTTP 200, product identity check, price from product block/JSON-LD, explicit variant+currency, confidence threshold
- [ ] Manual concierge fulfillment: daily scan → verified report → email/Sheet
- [ ] Landing page: "Send 5–10 competitor URLs for a free baseline scan"
- [ ] 1 paid design partner; harden scan output + confidence gate

### Phase B — Recurring workflow (weeks 4–8, only after ≥1 paid)
- [ ] Scheduled scans, historical change store, retry/evidence/confidence
- [ ] Client-level SKU list management (no bespoke dashboards yet)
- [ ] Free → paid pilot → renewal funnel metrics
- [ ] Second segment: white-label reports for e-commerce agencies

### Phase C — Productize (after 3–5 paying accounts)
- [ ] Hosted scheduled execution + alerts (Slack/email) for the verified change feed only
- [ ] Versioning sync: single source of release notes; pyproject == app version == plugin manifest
- [ ] Decide: keep engine MIT + sell managed outcome (recommended)

## Engineering Backlog (non-blocking for monetization)

- [ ] `analyze_forms()` blind spot on framework-rendered inputs (Astro/Next/Vite) — fix before any security product claims
- [ ] CORS probe: real cross-origin OPTIONS tests
- [ ] Crawl-depth navigation noise in security scans
- [ ] JS bundle secret-leak scan (Layer 5 of manual recon)
- [ ] Docker image + graceful browser restart for hosted API (only when hosted path gets demand)

## Anti-Patterns (do not do)

1. Free feature-creep: every new capability must serve one of — trustworthy product change / reliable browser workflow / faster paid integration
2. Sell token compression as the product (real win 1.6x on mixed set, overhead on tiny pages; it's an engineering KPI, not a buyer outcome)
3. Compete with browser-use/Stagehand/Playwright head-on as a platform (they own distribution; integrate/benchmark alongside instead)
4. Automate checkout/submit without boundaries — keep `auto_fill → ask → handoff` model
5. Claim anti-bot guarantees or security verdicts the scanner cannot back
