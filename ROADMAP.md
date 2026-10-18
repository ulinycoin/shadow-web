# Shadow Web — Roadmap

> Version: 0.4.4 → 1.0.0
> Last updated: 2026-10-17
> Status: open-source engine (PyPI + GitHub, MIT) + **primary bet: managed product-change monitoring concierge for DTC/e-commerce**
>
> Execution sub-plan (2026-10-17): [`docs/FAMILY_PLAN.md`](docs/FAMILY_PLAN.md) — Verify → paid pilot →
> minimal Watch; Collect deferred. Scoped to the monitor bet; it does not replace this roadmap.

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
- ~20% — engine reliability debt that blocks paid use (URL policy, MCP session isolation, LLM error handling, hard caps) — **mostly cleared in 0.4.2, verified again in 0.4.3**
- ~10% — distribution: landing page, outreach, LocalPDF dogfood case study

### Kill list — closed / frozen (2026-08-15)

1. **Security scanner as a product line** — closed. OSS feature only; **done in 0.4.2**: the CORS same-origin false verdict (the probe now sends a foreign origin) and the SRI first-party false alarm (first-party assets are `low`, third-party stay `high`). **0.4.3** made the rules actually reachable — they were reading the compressor's sanitized HTML, so `SRI_MISSING`/`MIXED_*` could never fire in the pipeline; the scan now takes `raw_html`. **Still no further investment**: the unclosed scanner gaps at the bottom of this file are quality debt, not a product.
2. **Token compression as a product / API** — closed. It is an engineering KPI (~1.6x on mixed set, overhead on tiny pages), not a buyer outcome.
3. **Hosted browser cloud / execution API** — closed until ≥3 paying accounts (Phase C trigger).
4. **Anti-bot bypass / stealth claims** — closed. Camoufox is the default engine, not a Cloudflare/CAPTCHA guarantee.
5. **SDK feature breadth** — frozen. No new engine capabilities unless they serve the monitor workflow (anti-pattern #1).
6. **LocalPDF weekly scan** — dogfood / case study only, no productization; its diff infra feeds the change-report engine.
7. **Maintenance mode (keep working, no new features):** `security_scan.py` growth, `webmcp.py`, a11y supplement, browser-use integration, exec-agent breadth, `benchmarks/`.

## Current State (v0.4.4)

### Automation Core (production-ready)
- ✅ Playwright/Camoufox browser execution: navigate/click/fill/snapshot, Shadow DOM flattening, a11y supplement, diff snapshots, web search (Brave/Yahoo)
- ✅ **Navigation URL policy** (`url_guard`): http/https on globally routable hosts only, obfuscated-IP and metadata-address forms blocked, post-redirect re-check
- ✅ DOM compression: Action Map + data-sid, grouped XML, terse payloads with **bounded page-controlled fields**
- ✅ Rendered Text Index: outline → selective content blocks with self-reported `coverage`/`mode`/`signals`/`depth_limited`. Note: "coverage" is computed by the indexer against its own notion of source text — it is an internal consistency metric (misleadingly worded as "100% coverage" before), not independent proof that no page text was dropped
- ✅ SchemaSnap: tables/forms/lists → JSON/CSV, session-native; header rows are no longer exported as data and nested tables no longer merge upward. **0.4.4**: core extracted into the published `schemasnap` package — `shadow_web.schema_snap` is a 64-line facade, fixes land once
- ✅ Self-healing selectors: local fuzzy cache → LLM fallback with Playwright verification, atomic cache writes, **query/fragment-aware cache keys**, **page context redacted before leaving the process**
- ✅ Form fill: JSON profile → plan → execute with ask/handoff; **submission is opt-in (`auto_submit=False` everywhere), at most one submit per URL, and pending questions block submit**
- ✅ Standalone `shadow-web exec` agent loop (OpenAI-compatible, bounded, single-call finish, bounded LLM client)
- ✅ MCP server (stdio): 27 tools incl. thick `agent_run` (always returns its envelope), thin navigate/schema/content tools with hard caps, **per-session browser state**
- ✅ Security surface scanner: 8 modules (forms, links, headers, cookies, SRI, mixed content, CORS, page meta) — not a pentest tool
- ✅ **595 tests passing**, CI (GitHub Actions), PyPI + Claude Code plugin marketplace + LobeHub manifest (tool list now generated from the code and guarded by `tests/test_tool_surface.py`)

### Fixed in 0.4.2 (adversarial review, 2026-09-27)

Full detail in `CHANGELOG.md`. Blockers first:

- `multi_step` form submission could not be declined and re-submitted the same form up to `max_steps`
- the flagship price-watch sample report was a **diff of a snapshot with itself** ("0 changes"), and the report path ignored its own quality gates
- structured `$100.00`/`$1.00` prices were silently unpriceable, and a SKU crossing $100 raised a high-severity `price_lost`
- `content_index._PRICE_RE` was quadratic and froze the whole process (the GIL is not released during a match) on a hostile page
- navigation had no URL validation: `file:///etc/passwd`, cloud metadata and loopback were reachable from an LLM-chosen URL
- a `.env` in the current working directory could redirect the LLM endpoint and supply its own API key

### Fixed in 0.4.3 (verified review of `src/`, 2026-09-29)

A second adversarial pass (Sonnet 5.5 over all 32 `src/` files) died on its output
budget; its reasoning was salvaged, every claim re-checked against the code, and only
the five confirmed findings were fixed. Full detail in `CHANGELOG.md`:

- **the security scanner's SRI and mixed-content rules could never fire**: they were
  handed `clean_html`, where `<script>/<link>/<meta>` and `integrity`/`rel` no longer
  exist. `analyze_surface` now takes `raw_html`; a `SCAN_ON_SANITIZED_HTML` finding
  marks the degraded mode instead of returning a silent all-clear.
- **`analyze_forms` read truncated HTML**: `id`/`required`/`pattern`/`disabled` are
  not in `KEEP_ATTRS`, so `label[for] → input[id]` was broken and no field was ever
  `required` — a systematic skew, not an edge case.
- **`classify_field_kind` could send profile PII to the wrong field**: bare substring
  matching (`"name" in "username"`, `"tel" in "hotel"`) plus an empty blob matching
  every alias. Now whole-word, and an empty blob is `unknown` (human, not auto-fill).
- **MCP session keys were recyclable** (`str(id(session))`): a new client could
  inherit a dead client's browser. Monotonic keys, a bounded registry, and a FastMCP
  lifespan that finally calls the previously-dead `_close_all_sessions()`.
- **a timed-out `agent_run` kept working**: the timeout released the concurrency slot
  while its thread/browser ran on. Cooperative `should_stop` in the loop + the slot
  held until the worker exits.

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
  - [x] Evidence pack per change — **2026-10-18**: winning plane, raw value, path, name match and a capture block (timestamp, clean-HTML sha256, viewport screenshot) are stored and rendered per change; a change without them is `UNVERIFIED` and can never be `high`
  - [x] Self-diff refusal + quality gates enforced in the diff/render path
  - [x] Rebuild the accuracy metric — **2026-10-18**: strict `trusted` = structured plane + ISO currency + name match + verified price AND stock + stored evidence. Frozen 10 → **8/10**; 30-PDP acceptance → **27/30 strict, 0 false `verified`** (`reports/price_watch/accuracy-2026-10-18.md`)
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
2. ✅ **MCP session isolation** — состояние браузера ключуется по MCP-сессии, у каждой сессии свой лок (глобальный лок просто выстроил бы всех клиентов в очередь). **0.4.3**: ключ больше не `str(id(session))` (адрес переиспользуется после GC — новый клиент мог получить браузер мёртвого), реестр ограничен `SHADOW_WEB_MCP_MAX_SESSIONS`, а `_close_all_sessions()` наконец вызывается на shutdown через lifespan
3. ✅ **`llm.chat` bounds + error handling** — таймаут/ретраи/`max_tokens` в клиенте; `agent_run` всегда возвращает конверт (`stop_reason="error"|"timeout"|"invalid"`), семафор и wall-clock лимит
4. ✅ **Thin-MCP hard caps** — `max_rows=0` больше не обходит `_MAX_ROWS`, `compress_html_to_xml` получил лимит входа, мусорные значения env не ломают импорт
5. ✅ **`agent_run` parallelism semaphore** (`SHADOW_WEB_AGENT_CONCURRENCY`)
6. ⏳ **HealCache cross-instance race** (`heal_local.py`) — ключ теперь учитывает query/fragment и не смешивает origin'ы, запись атомарна, но многопроцессная потеря записей (last-writer-wins) остаётся
7. ✅ **form_fill guardrails** — `auto_submit=False` везде, не более одной отправки на URL, неотвеченные вопросы блокируют submit, ниже-пороговые совпадения не заполняются
8. ✅ **verified_heal fails-open fix** — `_tag_matches` возвращает False на исключении, порог 0.8, containment-приём убран. ⏳ осталось: матчинг по тексту самого элемента, а не потомков; не кэшировать `:has-text` селекторы для evaluate-пути
9. ✅ **security_scan credibility** — CORS-проба шлёт foreign origin, SRI для first-party понижен до `low`. **0.4.3**: правила SRI/mixed-content стали достижимыми (скан получает `raw_html`, а не сжатый HTML, из которого вырезаны `script`/`link`/`integrity`). ⏳ осталось: CSP только для document-ответов, пустой домен cookie, `_dedupe_findings` схлопывает разные `FORM_WEAK_PASSWORD`, пробы без `url_guard` (см. остатки ревью 2026-09-29, п. 9)
10. ⏳ **De-duplicate sync/async** — один async-core + тонкий sync-адаптер (`wrapper.py` / `browser_use.py` ~90% копии)
11. ✅ **Tool-surface test** — `tests/test_tool_surface.py` строит MCP-сервер и сверяет README + `lhm.plugin.json` с кодом (это и был первый `test_mcp_server.py`); ⏳ осталось: тесты на лимиты частоты (`server.py`)

### Остаточные риски, сознательно не закрытые в 0.4.2

- ✅ **Точность извлечения цены (B14)** — закрыто 2026-10-18: при нескольких JSON-LD `Product`-узлах выигрывает сумма, подтверждённая другой плоскостью, а не первый узел; `name_match` сверяет имя с title/слагом (односложное имя — максимум `partial`). Остаток: 3 Outdoor Voices PDP без структурной цены — regex-only `review`.
- ✅ **Метрика точности (B11)** — закрыто 2026-10-18: строгая метрика «цена из структурной плоскости + ISO-валюта + совпадение имени + verified price/stock + evidence»; отчёт печатает определение, знаменатель и reason-коды, без процента-заголовка.
- **Гейт валюты внутри одной валюты** — дрейф EUR→EUR из-за локали/FX не гейтится; нужен market pin на цель.
- ✅ **Аудит provenance (M12)** — закрыто 2026-10-18: `price_plane`, `price_evidence` (plane/raw/path/snippet) и `capture` (sha256 + screenshot) пишутся в снапшот; отчёт даёт Evidence-секцию по каждому изменению.
- ✅ **`_is_trusted` (M10)** — закрыто 2026-10-18: строгая метрика требует структурную плоскость (`jev_regex` и regex в неё не входят), verified-статусы и evidence; старая логика осталась как `_is_trusted_legacy` только для чтения старых снапшотов.
- **DNS-rebinding** — проверка резолва best-effort (по умолчанию включена, `SHADOW_WEB_URL_DNS_CHECK`); Chromium резолвит повторно, полная защита требует пиннинга IP через route interception.
- **Ограничения ценового паттерна** — `nur .99 €`, `1500 руб`, `руб.1 500` дают `None`; ослаблять lookbehind нельзя (вернётся O(n²)).
- **Разделяемый lxml-парсер** — шарится между потоками; измерено, что thread-local не ускоряет (узкое место GIL), но явное владение лучше.
- **Нет lint/type-гейта в CI** (ruff/mypy/coverage отсутствуют как зависимости).
- **Артефакты `reports/`** — ✅ `sample-report.md`/`.json` перегенерированы из реального кросс-ран diff'а (13 авг → 27 сен 2026, живой скан: 1 high — allbirds USD 89→120, 5 medium; self-diff'ы `baseline.json` vs `check.json` остались историческими, но `cmd_diff` их больше не воспроизводит). В `benchmarks/results/` по-прежнему лежит вручную исправленный `…_corrected.json`.

### Остаточные риски после верифицированного ревью 2026-09-29 (в 0.4.3 не закрыты)

Пункты ниже **перепроверены по коду** (не «мнение модели»): у каждого есть файл:строка. Это очередь на 0.4.4+, не блокеры 0.4.3.

**Высокие**

1. **`verify_selector_in_html` исполняет клиентский HTML на сервере** — `verified_heal.py:154,159` (`playwright.chromium.launch` + `page.set_content(wrapped)`) вызывается из `/v1/heal` (`server.py:306`) со скриптами страницы и полным сетевым доступом. Следствие: слепой SSRF/скан внутренних портов (возвращается только bool), не эксфильтрация; endpoint за API-ключом. Фикс: `route`-interception/offline-контекст, запрет навигации.
2. **Навигация по клику и submit не проверяется `url_guard`** — прямой `goto` проверяется до запроса (`agent/tools.py:481`), но `click` (`agent/tools.py:531`) и submit формы (`form_fill.py:861`) — нет; `assert_navigation_landed` срабатывает постфактум. Нужен request-interception, а не проверка результата.
3. **Авто-дисмисс согласий кликает любой `role="dialog"`** — `capture_ready.py:96` считает consent-поверхностью любой диалог, `:153` кликает Accept/Allow/Agree/Continue. Модал «Delete account?» или OAuth-«Allow» может быть нажат раньше, чем агент его прочитает.
4. **Heal по подстроке (не порог)** — `heal_local.py:191-192` возвращает 0.92 для «Log in» ⊂ «Log in with Google», `verified_heal.py:72` с порогом 0.8 это пропускает → необратимое действие по неверному элементу («Delete» → «Delete all»).
5. **Повторный клик после исключения** — `browser_use.py:665-671`: Playwright может бросить таймаут уже после того, как клик сработал и запустил навигацию, а код кликает вылеченный селектор → двойное действие. (331027c закрыл соседнюю гонку наблюдения, не эту.)

**Средние**

6. **Локальные числа** — `schema_snap.py:697,702`: `'1.234,56 €'→1.23456`, `'12,50 €'→1250`, `'12,5'→125` + тип `integer`. (`_parse_amount` больше нет, поведение живёт в schema_snap.)
7. **`web_search` оставляет `clean_html` от прошлой страницы** — `mcp/server.py:679` обновляет только `session["shadow_page"]`: `schema_session*` читает старую страницу, `content_outline` — новую.
8. **Лимит размера запроса только по `Content-Length`** — `server.py:65-66`: chunked/без заголовка обходит 413 до буферизации тела (pydantic-лимит 2M символов срабатывает позже).
9. **HTTP-пробы `security_scan` без `url_guard`** — `:335` (`requests.head`), `:350`, `:531` (`OPTIONS`); достижимо только из CLI-скрипта с операторским URL.
10. **Редакция heal-контекста** — `heal_context.py:70-80` покрывает `textarea`/`value`/`data-*`, но не PII в текстовых узлах, `contenteditable`, `selected option` и текст inline `<script>`; fallback body-innerHTML редактится и режется до 4000 символов, но перечисленное проходит.
11. **Соседний label без ассоциации** — `schema_snap.py:500-506` берёт первый `label`-сиблинг: в формах «label/input/label/input» лейблы разъезжаются. `label[for]` теперь достижим через `raw_html`, но form-fill по-прежнему парсит `clean_html`.
12. **Регистр и типы input** — `schema_snap.py:531-533` + `_INPUT_TYPES:438-443`: `type="EMAIL"` и `button|reset|datetime` деградируют в `text` (запись произойдёт только если лейбл совпал с `SAFE_FIELD_KINDS`, иначе `ask`).
13. **`colspan` в заголовках таблиц** — `schema_snap.py:261-264` не разворачивает объединённые ячейки → смещение колонок.
14. **`node.click()` синтетический** — `dom_capture.py:192-195`, `browser_use.py:585`, `a11y_capture.py:484`: не проверяет видимость/disabled, disabled-элемент возвращает `ok:true`.
15. **Прямая запись `.value` без native setter** — `dom_capture.py:201`, `browser_use.py:594`: React-трекер съедает синтетический `input`, `onChange` не срабатывает → заполнение молча не регистрируется.
16. **Позиционные `sid`/`bind_id`** — `compressor.py:116`, `dom_capture.py:86`: вставка элемента в начале переиндексирует всё дальше → ложные appeared/disappeared в diff и устаревшие sid в agent-loop.
17. **Классификация страниц по подстроке** — `a11y_capture.py:249-250`: любой небольшой текст с «captcha» → Anti-bot; `:269-270`: `login|signin|authorize` в URL (в т.ч. `/blog/how-to-login`) → Auth-gated, то есть формы логина — основной кейс form-fill — уходят в handoff.
18. **Бюджет токенов проверяется до tool calls** — `agent/loop.py:246-257`: валидный `done(finalAnswer)` ровно на границе бюджета выбрасывается; история сообщений не обрезается (`:329`, рост ограничен `max_tokens`/`max_steps`).

**Низкие / условные**

19. **Полноширинные точки в хосте** — `url_guard.py:131-138` не нормализует U+FF0E; при `SHADOW_WEB_URL_DNS_CHECK=0` или ошибке резолва — fail-open (`:176-182`).
20. **CDP-сессия не детачится** — `a11y_capture.py:365,373`: одна сессия на захват, освобождается только с закрытием страницы.
21. **Sparse-shell ждёт 10 с** — `capture_ready.py:17,19,455`: на малолеткстовых страницах всегда выжидается дедлайн (один раз на URL); сама метка SparseShell условна (`is_sparse_shell`).
22. **Jev отправляет лейблы/задачу/URL третьей стороне** — `jev.py:28,203-205` (`api.typesafe.ai`); только при явном `SHADOW_WEB_AGENT_JEV=1`.
23. **Новый LLM-клиент на каждый `/v1/heal`** — `server.py:290`: пул соединений не переиспользуется.
24. **Локальность по IP** — `server.py:113-114`: reverse-proxy с `127.0.0.1` выглядит локальным; тихий bypass убран (нужен явный флаг, в prod 503).

## Anti-Patterns (do not do)

1. Free feature-creep: every new capability must serve one of — trustworthy product change / reliable browser workflow / faster paid integration
2. Sell token compression as the product (real win 1.6x on mixed set, overhead on tiny pages; it's an engineering KPI, not a buyer outcome)
3. Compete with browser-use/Stagehand/Playwright head-on as a platform (they own distribution; integrate/benchmark alongside instead)
4. Automate checkout/submit without boundaries — keep `auto_fill → ask → handoff` model
5. Claim anti-bot guarantees or security verdicts the scanner cannot back
6. Ship paid-pilot reports before the trust layer is fixed — a single false high-severity verdict (currency switch, wrong variant, truncated price) kills the concierge
