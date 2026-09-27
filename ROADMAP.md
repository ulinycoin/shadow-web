# Shadow Web — Roadmap

> Version: 0.4.2 → 1.0.0
> Last updated: 2026-09-27
> Status: open-source engine (PyPI + GitHub, MIT) + **primary bet: managed product-change monitoring concierge for DTC/e-commerce**

## The Bet (decided 2026-08-15)

**Shadow Web is not an SDK business — it is an extraction-engine business.**

The MIT SDK (PyPI / GitHub / MCP / Claude Code plugin) is the credibility, distribution, and R&D layer. It stays free and open. Revenue comes from a **managed outcome**: verified competitor product-change reports (price / stock / promo / assortment) for DTC brands and e-commerce agencies.

Why this direction wins:

- **Extraction capability on hard pages** — closed Shadow DOM PDPs are handled (evidence: `reports/price-probe.json`, `reports/price_watch/`). The numbers previously quoted here were overstated and are now stated honestly: the probe's "success" criterion was "≥2 price-like strings anywhere on the page", not "the product's price", so of 14/15 "successes" only 2 came from a structured plane (JSON-LD / og). Newest baseline artifact reports **7/10 trusted** (`reports/price_watch/pilot-baseline-report-2026-09-21.md`), not 8/10. The moat claim is about *capability on rendered/closed-Shadow pages*, and the accuracy metric still has to be rebuilt as "price from a structured plane, cross-checked against the product name" (see Phase A below).
- **Recurring buyer pain** — competitor price/stock moves directly drive DTC merchandising; monitoring is a monthly subscription, not a one-off sale.
- **Concierge-first wedge** — "send 5–10 competitor URLs → free baseline scan → verified report" needs zero setup. SaaS dashboards (Prisync/Price2Spy class) compete on scale; we compete on trust and accuracy for 20–200 SKU brands.
- **No infra bet before demand** — hosted execution + alerts (Phase C) only after 3–5 paying accounts.

**Resource allocation until the Phase A gate:**

- ~70% — Product Change Intelligence pipeline: fix trust-layer bugs, evidence packs, scheduled scans, alerting
- ~20% — engine reliability debt that blocks paid use (URL policy, MCP session isolation, LLM error handling, hard caps) — **mostly cleared in 0.4.2, see below**
- ~10% — distribution: landing page, outreach, LocalPDF dogfood case study

### Kill list — closed / frozen (2026-08-15)

1. **Security scanner as a product line** — closed. OSS feature only; **done in 0.4.2**: the CORS same-origin false verdict (the probe now sends a foreign origin) and the SRI first-party false alarm (first-party assets are `low`, third-party stay `high`). **No further investment.**
2. **Token compression as a product / API** — closed. It is an engineering KPI (~1.6x on mixed set, overhead on tiny pages), not a buyer outcome.
3. **Hosted browser cloud / execution API** — closed until ≥3 paying accounts (Phase C trigger).
4. **Anti-bot bypass / stealth claims** — closed. Camoufox is the default engine, not a Cloudflare/CAPTCHA guarantee.
5. **SDK feature breadth** — frozen. No new engine capabilities unless they serve the monitor workflow (anti-pattern #1).
6. **LocalPDF weekly scan** — dogfood / case study only, no productization; its diff infra feeds the change-report engine.
7. **Maintenance mode (keep working, no new features):** `security_scan.py` growth, `webmcp.py`, a11y supplement, browser-use integration, exec-agent breadth, `benchmarks/`.

## Current State (v0.4.2)

### Automation Core (production-ready)
- ✅ Playwright/Camoufox browser execution: navigate/click/fill/snapshot, Shadow DOM flattening, a11y supplement, diff snapshots, web search (Brave/Yahoo)
- ✅ **Navigation URL policy** (`url_guard`): http/https on globally routable hosts only, obfuscated-IP and metadata-address forms blocked, post-redirect re-check
- ✅ DOM compression: Action Map + data-sid, grouped XML, terse payloads with **bounded page-controlled fields**
- ✅ Rendered Text Index: outline → selective content blocks with self-reported `coverage`/`mode`/`signals`/`depth_limited`. Note: "coverage" is computed by the indexer against its own notion of source text — it is an internal consistency metric (misleadingly worded as "100% coverage" before), not independent proof that no page text was dropped
- ✅ SchemaSnap: tables/forms/lists → JSON/CSV, session-native; header rows are no longer exported as data and nested tables no longer merge upward
- ✅ Self-healing selectors: local fuzzy cache → LLM fallback with Playwright verification, atomic cache writes, **query/fragment-aware cache keys**, **page context redacted before leaving the process**
- ✅ Form fill: JSON profile → plan → execute with ask/handoff; **submission is opt-in (`auto_submit=False` everywhere), at most one submit per URL, and pending questions block submit**
- ✅ Standalone `shadow-web exec` agent loop (OpenAI-compatible, bounded, single-call finish, bounded LLM client)
- ✅ MCP server (stdio): 27 tools incl. thick `agent_run` (always returns its envelope), thin navigate/schema/content tools with hard caps, **per-session browser state**
- ✅ Security surface scanner: 8 modules (forms, links, headers, cookies, SRI, mixed content, CORS, page meta) — not a pentest tool
- ✅ **480+ tests passing**, CI (GitHub Actions), PyPI + Claude Code plugin marketplace + LobeHub manifest (tool list now generated from the code and guarded by `tests/test_tool_surface.py`)

### Fixed in 0.4.2 (adversarial review, 2026-09-27)

Full detail in `CHANGELOG.md`. Blockers first:

- `multi_step` form submission could not be declined and re-submitted the same form up to `max_steps`
- the flagship price-watch sample report was a **diff of a snapshot with itself** ("0 changes"), and the report path ignored its own quality gates
- structured `$100.00`/`$1.00` prices were silently unpriceable, and a SKU crossing $100 raised a high-severity `price_lost`
- `content_index._PRICE_RE` was quadratic and froze the whole process (the GIL is not released during a match) on a hostile page
- navigation had no URL validation: `file:///etc/passwd`, cloud metadata and loopback were reachable from an LLM-chosen URL
- a `.env` in the current working directory could redirect the LLM endpoint and supply its own API key

### Distribution
- ✅ PyPI: `pip install shadow-web`, `uvx shadow-web-mcp`
- ✅ GitHub: ulinycoin/shadow-web (MIT)
- ✅ Claude Code plugin marketplace + Vercel `npx skills`
- ✅ Demo scripts: `scripts/demo_30s.sh`, `scripts/form_fill_demo.py`, `scripts/smoke_install.sh`
- ⚠️ GitHub stars: 9 (low). OSS is distribution/credibility layer, not a revenue channel yet.

## Monetization Direction

**Primary: managed competitor product-change monitor for DTC/e-commerce (concierge-first).**

- Form: paid concierge / design partner, not SaaS dashboard on day 1
  - 10 competitor SKUs, daily scan, change log (price/stock/promo), verified report, $149–299 pilot → $299–499/mo
- Target: EN DTC brands (20–200 SKU) and small e-commerce agencies
- Validation gate: **1 paid pilot OR 2 paid-price agreements** after free baseline; **stop if 10 contacts + thread yield zero response**
- Expansion wedge (same pipeline, Phase B+): assortment signals — new/removed products, stock flips, promo detection, pricing-table changes (LocalPDF dogfood pattern)

**Not in scope for monetization (closed):**
- Hosted browser cloud / execution API (Phase C trigger: 3–5 paying accounts)
- Closing MIT core features behind paywall
- Security scanner as paid product (quality gaps: orphan inputs, CORS false model, crawl noise — reputational risk)
- Anti-bot bypass as a service (Camoufox is not a Cloudflare/CAPTCHA guarantee)

## Phases

### Phase A — Product Change Monitor MVP (weeks 1–3)
- [x] Sample competitor change report on 10 public DTC URLs (price/stock/promo/confidence/needs_review)
- [x] Quality gates: HTTP 200, product identity check, price from product block/JSON-LD, explicit variant+currency, confidence threshold
  - 2026-08-13 live scan: 10/10 PDP success, 8/10 trusted (JSON-LD/og + ISO currency), 0× 404 leak. Two regex leftovers: tentree, Outdoor Voices.
- [ ] **Trust-layer fixes — paid-pilot blockers:**
  - [x] Currency-aware diff: compare currency before price; flag currency switch instead of false "price change, high". Missing currency on either side is now not comparable → `needs_review`
  - [x] Variant pinning in snapshots (sku/variantId) — one-sided/`None` variant is no longer a price change; `?variant=` preferred over product `sku`
  - [x] Fix `needs_review` flag when stock is unknown; availability case-folded with explicit pre-order/back-order/limited, and a lost stock signal raises `stock_signal_lost`
  - [x] Configurable price filters (hardcoded $100/$1 drop removed from the structured planes; kept only for regex candidates)
  - [ ] `_parse_amount` locale handling + unit tests (`1.234` vs `1234`, `€1.234,56`)
  - [x] Price regex anchors in content_index (quadratic backtracking removed; `$1234.56` no longer truncates)
  - [ ] Evidence pack per change: screenshot + JSON-LD/og sample + confidence — the **winning price plane is still not stored**, so `high` cannot be audited from the artifact
  - [x] Self-diff refusal + quality gates enforced in the diff/render path
  - [ ] Rebuild the accuracy metric: "price from a structured plane, cross-checked against the product name" (the old "93.3%" was a ≥2-price-strings heuristic)
- [ ] Landing page: "Send 5–10 competitor URLs for a free baseline scan"
- [ ] 10 outreach drafts (public send = OK)
- [ ] Manual concierge fulfillment: daily scan → verified report → email/Sheet
- [ ] 1 paid design partner; harden scan output + confidence gate

### Phase B — Recurring workflow (weeks 4–8, only after ≥1 paid)
- [ ] Scheduled scans, historical change store, retry/evidence/confidence
- [ ] Client-level SKU list management (no bespoke dashboards yet)
- [ ] Free → paid pilot → renewal funnel metrics
- [ ] Second segment: white-label reports for e-commerce agencies
- [ ] Assortment signals: new/removed products, stock flips, promo detection (pricing-table diff from LocalPDF dogfood)

### Phase C — Productize (after 3–5 paying accounts)
- [ ] Hosted scheduled execution + alerts (Slack/email) for the verified change feed only
- [ ] Versioning sync: single source of release notes; pyproject == app version == plugin manifest
- [ ] Decide: keep engine MIT + sell managed outcome (recommended)

## Engineering Backlog (priority order — monetization blockers first)

Страница статуса после адверсариального ревью 2026-09-27. ✅ = сделано в 0.4.2.

1. ✅ **URL scheme/host guard in `navigate`** (`url_guard.py`, все 4 точки goto + re-check после редиректа) — SSRF / `file://` / `169.254.169.254` закрыты; анти-инъекционные правила в системном промпте добавлены (как смягчение, не как граница)
2. ✅ **MCP session isolation** — состояние браузера ключуется по id MCP-сессии, у каждой сессии свой лок (глобальный лок просто выстроил бы всех клиентов в очередь)
3. ✅ **`llm.chat` bounds + error handling** — таймаут/ретраи/`max_tokens` в клиенте; `agent_run` всегда возвращает конверт (`stop_reason="error"|"timeout"|"invalid"`), семафор и wall-clock лимит
4. ✅ **Thin-MCP hard caps** — `max_rows=0` больше не обходит `_MAX_ROWS`, `compress_html_to_xml` получил лимит входа, мусорные значения env не ломают импорт
5. ✅ **`agent_run` parallelism semaphore** (`SHADOW_WEB_AGENT_CONCURRENCY`)
6. ⏳ **HealCache cross-instance race** (`heal_local.py`) — ключ теперь учитывает query/fragment и не смешивает origin'ы, запись атомарна, но многопроцессная потеря записей (last-writer-wins) остаётся
7. ✅ **form_fill guardrails** — `auto_submit=False` везде, не более одной отправки на URL, неотвеченные вопросы блокируют submit, ниже-пороговые совпадения не заполняются
8. ✅ **verified_heal fails-open fix** — `_tag_matches` возвращает False на исключении, порог 0.8, containment-приём убран. ⏳ осталось: матчинг по тексту самого элемента, а не потомков; не кэшировать `:has-text` селекторы для evaluate-пути
9. ✅ **security_scan credibility** — CORS-проба шлёт foreign origin, SRI для first-party понижен до `low`. ⏳ осталось: CSP только для document-ответов, пустой домен cookie, `_dedupe_findings` схлопывает разные `FORM_WEAK_PASSWORD`
10. ⏳ **De-duplicate sync/async** — один async-core + тонкий sync-адаптер (`wrapper.py` / `browser_use.py` ~90% копии)
11. ✅ **Tool-surface test** — `tests/test_tool_surface.py` строит MCP-сервер и сверяет README + `lhm.plugin.json` с кодом (это и был первый `test_mcp_server.py`); ⏳ осталось: тесты на лимиты частоты (`server.py`)

### Остаточные риски, сознательно не закрытые в 0.4.2

- **Точность извлечения цены (B14)** — по-прежнему берётся первый Product-узел без сверки `name`/`sku` с заголовком и слагом страницы. Это главный оставшийся источник ложных «цен»; без него метрику точности продавать нельзя.
- **Метрика точности (B11)** — «93.3%» было «на странице ≥2 похожих на цену строки». Нужна метрика «цена из структурной плоскости + совпадение с именем товара».
- **Гейт валюты внутри одной валюты** — дрейф EUR→EUR из-за локали/FX не гейтится; нужен market pin на цель.
- **Аудит provenance (M12)** — снапшоты не сохраняют, из какой плоскости взята цена, поэтому метку `high` нельзя проверить по артефакту.
- **`_is_trusted` (M10)** — считает и LLM-выбранные (Jev) цены, и эвристику `cents/100`, помеченную как `og_meta`.
- **DNS-rebinding** — проверка резолва best-effort (по умолчанию включена, `SHADOW_WEB_URL_DNS_CHECK`); Chromium резолвит повторно, полная защита требует пиннинга IP через route interception.
- **Ограничения ценового паттерна** — `nur .99 €`, `1500 руб`, `руб.1 500` дают `None`; ослаблять lookbehind нельзя (вернётся O(n²)).
- **Разделяемый lxml-парсер** — шарится между потоками; измерено, что thread-local не ускоряет (узкое место GIL), но явное владение лучше.
- **Нет lint/type-гейта в CI** (ruff/mypy/coverage отсутствуют как зависимости).
- **Артефакты `reports/`** — ✅ `sample-report.md`/`.json` перегенерированы из реального кросс-ран diff'а (13 авг → 27 сен 2026, живой скан: 1 high — allbirds USD 89→120, 5 medium; self-diff'ы `baseline.json` vs `check.json` остались историческими, но `cmd_diff` их больше не воспроизводит). В `benchmarks/results/` по-прежнему лежит вручную исправленный `…_corrected.json`.

## Anti-Patterns (do not do)

1. Free feature-creep: every new capability must serve one of — trustworthy product change / reliable browser workflow / faster paid integration
2. Sell token compression as the product (real win 1.6x on mixed set, overhead on tiny pages; it's an engineering KPI, not a buyer outcome)
3. Compete with browser-use/Stagehand/Playwright head-on as a platform (they own distribution; integrate/benchmark alongside instead)
4. Automate checkout/submit without boundaries — keep `auto_fill → ask → handoff` model
5. Claim anti-bot guarantees or security verdicts the scanner cannot back
6. Ship paid-pilot reports before the trust layer is fixed — a single false high-severity verdict (currency switch, wrong variant, truncated price) kills the concierge
