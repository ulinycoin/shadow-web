# Changelog

All notable changes to this project are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.4.4] - 2026-10-11

The extraction core moves out of the tree. `shadow_web.schema_snap` becomes a facade
over the published `schemasnap` package, and running this suite against the shared
core flushed out four correctness bugs that both copies had inherited — proof that
the duplication was costing correctness, not just maintenance.

### Changed

- **Extraction core extracted into `schemasnap>=0.1.9`** — a new runtime dependency,
  installed automatically by `pip install shadow-web` (no extra, no flag).
  `src/shadow_web/schema_snap.py` went from a 773-line fork to a 64-line facade: it
  re-exports `parse_tables`, `parse_forms`, `parse_lists`, `parse_page`,
  `table_to_records`, `table_to_csv`, `export_table_json`, `export_table_csv` and
  maps the core's `source` vocabulary back to the one callers already use
  (`native` → `table`, `aria` → `aria_grid`). Names not re-exported (private helpers)
  are proxied through module `__getattr__`. Every existing import path is untouched —
  `shadow_web.schema_snap`, the package `__init__`, `form_fill`, `security_scan`,
  `agent/tools` and the MCP server all keep working, and the 27 MCP tools are
  unchanged.
- **Wider virtual-table planes**: the core also detects Tailwind grid and
  pseudo-table (`Box-row`) layouts, so tables can now appear where a page previously
  yielded none. On Bootstrap-style layouts this can produce false positives — filter
  on `source` when you need the old precision.

### Fixed

Fixes 1–5 live in `schemasnap` 0.1.9; they reached this release only because the
shadow-web suite was pointed at the shared core.

- `parse_tables("<!-- c -->")` and `parse_tables(None)` raised instead of returning
  no tables.
- **Zero-padded ids were silently rewritten**: `0406`/`007` were inferred as
  `integer` and coerced, changing the value. They stay `string`.
- ARIA grids with a single column were discarded by a `min_cols=2` rule → parsed.
- An ARIA grid without `role=columnheader` swallowed its first data row into the
  header → headers are generated as `col_1…col_N` and every data row is kept.
- An empty `role=table` `<div>` (or empty `<table>`) surfaced as a phantom table
  with zero columns and zero rows → no table is emitted
  (`test_empty_grid_yields_no_rows` → `test_empty_grid_yields_no_table`).

## [0.4.3] - 2026-09-29

Verification release: a second adversarial pass (Sonnet 5.5 over all of `src/`) was
turned into a code-verified list, and every confirmed finding is fixed with a test
that failed before the fix. The unverified remainder is parked in `ROADMAP.md`.

### Added

- **`raw_html` scan input + `SCAN_ON_SANITIZED_HTML`**: `analyze_surface(..., raw_html=…)`
  runs SRI, mixed-content and form analysis on the DOM as served. Callers that pass
  only `clean_html` still work but get an explicit `info` finding, so a sanitized
  input can no longer masquerade as a clean result.
- **`SHADOW_WEB_MCP_MAX_SESSIONS`** (default 8) caps live MCP sessions; idle sessions
  past the cap have their browsers reclaimed.
- **`SHADOW_WEB_AGENT_CANCEL_GRACE`** (default 15s) bounds how long a timed-out
  `agent_run` keeps its concurrency slot while the worker unwinds.
- **ARIA grid plane in SchemaSnap**: `parse_tables`/`parse_page` now read div-based
  grids (`role=table|grid` > `role=row` > `role=columnheader|cell|gridcell`) next to
  real `<table>` elements — the markup most modern admin and back-office UIs ship,
  which XPath `//table` never saw. Every table result carries `source`
  (`"table"` / `"aria_grid"`); a grid nested in another grid (or in a `<table>`) is
  not counted twice; `export_table_json`, `export_table_csv`, the MCP `schema_*`
  tools and the session helpers inherit the new plane unchanged.

### Fixed

- **SRI and mixed-content could never fire in the scanner**: `analyze_surface` fed
  the compressor's `clean_html` to `check_sri()`/`check_mixed_content()`, but that
  HTML has `<script>/<link>/<meta>` deleted and `integrity`/`rel` dropped by
  `KEEP_ATTRS` — the rules inspected exactly the elements that were already gone.
  The unit tests stayed green only because they hand raw HTML straight to the rules.
  `scripts/security_surface_scan.py` now passes `raw_html=await page.content()`;
  regression tests drive `process_html` first (`tests/test_security_scan.py`).
- **Form analysis ran on truncated HTML**: `parse_forms(clean_html)` never saw `id`,
  `required`, `pattern` or `disabled` (none are in `KEEP_ATTRS`), so the
  `label[for] → input[id]` association was broken and required-field detection always
  returned false — a systematic skew of `analyze_forms`, not an edge case.
- **Profile PII could be auto-filled into the wrong field**: `classify_field_kind`
  matched aliases as bare substrings in both directions (`"name" in "username"`,
  `"tel" in "hotel"`) and an empty label blob matched every alias, returning the
  longest one (`"business email"`) for a field it knew nothing about. Matching is now
  whole-word (`_RANKED_ALIAS_PATTERNS`, precomputed once) and an empty blob returns
  `unknown`, which routes the field to the human instead of filling it.
- **MCP session keys could be recycled**: `_SESSIONS` was keyed by
  `str(id(session))`, and CPython reuses the address of a collected object, so a
  client that disconnected could hand its registry entry — browser and captured page
  included — to the next client. Keys are now monotonic (`_SESSION_IDENTITIES`, with
  the session object held so its `id()` cannot be recycled at all), the registry is
  bounded by `SHADOW_WEB_MCP_MAX_SESSIONS`, and a FastMCP lifespan finally calls
  `_close_all_sessions()` on shutdown — that function had no production caller, so
  every disconnected client left a full browser running.
- **A timed-out `agent_run` kept working**: `asyncio.wait_for(asyncio.to_thread(...))`
  cannot cancel a thread, so the timeout released the agent semaphore while the
  browser was still busy and real concurrency exceeded the cap. The loop now takes a
  `should_stop` hook (polled between steps and between tool dispatches) and the slot
  is held until the worker has exited, bounded by `SHADOW_WEB_AGENT_CANCEL_GRACE`.
  Regression tests: `tests/test_agent_run_timeout.py`, `tests/test_agent_loop.py`.
- **Click navigated, then the capture crashed**: `_perform_action` ended with a
  `domcontentloaded` wait that the already-loaded old document satisfied
  immediately, so the following `refresh()` could run its `evaluate()` while the new
  document was committing and raise "Execution context was destroyed" — the click
  had in fact succeeded. The post-click wait is now navigation-aware (it compares
  against the pre-click URL and waits for the new document to load), and `refresh()`
  in both the sync and async wrappers retries once when a navigation destroys the
  context. Non-navigation failures still raise on the first attempt. Regression
  tests: `tests/test_navigation_race.py`.
- **Cell text spliced hidden subtrees together**: `text_content()` concatenated
  `display:none` sort menus and tooltips into the value, turning a column header
  into `IdAscendingDescending`. Cell text now skips hidden subtrees (`hidden`,
  `aria-hidden="true"`, inline `display:none` / `visibility:hidden`) and joins
  inline parts with a space (`CityBerlin` -> `City Berlin`). Applies to both planes.
- **Zero-padded identifiers were coerced to numbers**: a column of ids like `0406`
  or `007` inferred as `integer` and was exported as `406` / `7`. Zero-padded digit
  runs now stay strings.

## [0.4.2] - 2026-09-27

Security and trust release: everything below was found by an adversarial review of
the whole repository and is covered by a regression test that failed before the fix.
Several entries are **breaking** for callers that relied on the old defaults — they
are marked.

### Security

- **Navigation URL policy (SSRF / local file disclosure)**: agent-driven navigation
  (`navigate`, `web_search`, `form_fill_plan`, agent `navigate` tool) now goes through
  `shadow_web.url_guard`. Only `http`/`https` on globally routable hosts are allowed:
  `file:`, `javascript:`, `data:`, `blob:` and friends are rejected, as are loopback,
  private, link-local and cloud-metadata addresses — including the obfuscated IPv4
  forms browsers accept (`http://2130706433/`, `http://0x7f000001/`, `http://127.1/`,
  `[::1]`, IPv4-mapped IPv6) and internal host names. The final URL after redirects is
  re-checked. Escape hatch for local development and tests:
  `SHADOW_WEB_ALLOW_PRIVATE_URLS=1`. Optional best-effort DNS check (default on) is
  controlled by `SHADOW_WEB_URL_DNS_CHECK`.
- **Heal context redaction**: a broken-selector heal request no longer ships raw page
  markup to the (possibly third-party) heal endpoint — `textarea` contents, input
  `value`s (including password/hidden fields) and `data-*` secret attributes are
  stripped and the payload is length-bounded. `SHADOW_WEB_HEAL_SHARE_CONTEXT=0` sends
  only a minimal label stub.
- **`session_name` path traversal**: persistent-profile names are validated against
  `[A-Za-z0-9._-]{1,64}` and the resolved profile path must stay inside
  `~/.shadow-web/sessions`.
- **`.env` hijack**: dotenv discovery no longer reads `.env` from the current working
  directory or its parent (a cloned repository could redirect the LLM base URL and
  supply its own API key, sending prompts and page content to an attacker). Repository
  root, explicit `--env-file`, and `extra_paths` keep working. The developer-machine
  path hardcoded in published library code is gone.
- **Prompt injection**: the exec agent's system prompt now states that page content is
  data and never instructions (mitigation, not a substitute for the URL policy).

### Fixed

- **Form submission could not be declined and repeated (P0)**: `multi_step` hardcoded
  `auto_submit=True`, ignoring the caller's wishes and re-submitting the same form up
  to `max_steps` (three real submits of one lead form in a single MCP call). Both
  multi-step executors take `auto_submit` (default `False`) and submit at most once per
  URL; `form_fill_execute` exposes the flag. **Breaking**: `build_form_fill_plan` now
  defaults to `auto_submit=False`.
- **Forms were submitted with pending questions (P0)**: a plan with unanswered `ask`
  steps no longer carries a submit step, and the executors return `questions_pending`
  before clicking.
- **Wrong-field filling (P0)**: below-threshold field matches no longer carry a `sid`
  (a company name could be typed into a "Full name" input); such fields become a
  `low_confidence_match` handoff instead of a silent auto-fill.
- **Price monitoring false alarms**: currency and variant gates treat a missing value
  as not comparable (a localization flip is no longer a high-severity price change);
  `$100.00`/`$1.00` products are no longer skipped, which also removes a spurious
  `price_lost` when a SKU crosses $100; stock availability is case-folded with explicit
  pre-order/back-order/limited states, and a lost stock signal now raises
  `stock_signal_lost` instead of silence; AggregateOffer `lowPrice` ("from $X") can no
  longer be reported as a high-confidence price.
- **Price monitoring missed changes**: a target that disappears between scans is
  reported as `target_lost` instead of vanishing silently.
- **Promo detection false positives**: promo is derived from structured evidence
  (`compareAtPrice`, sale/list-price keys, an amount after "was") instead of substring
  matches on the first 60 characters of any field, so product copy no longer flips
  `promo` and raises alerts.
- **Self-diff evidence (P0)**: `cmd_diff` refuses to compare a snapshot with itself
  (identical or missing `scanned_at`), and the diff path re-evaluates the quality gates
  before rendering, so a report can no longer be produced from evidence the pipeline
  already rejected.
- **`content_index._PRICE_RE` (remote DoS)**: the quadratic amount pattern froze the
  whole process on a hostile page (CPython's `re` does not release the GIL during a
  match) — measured 6.9 s on a 8 kB adversarial input. The anchored replacement is
  linear (0.005 s) with identical matches on the existing fixtures; `quality()` no
  longer reports perfect coverage/retention when nothing could be parsed, and deep-DOM
  truncation is flagged (`depth_limited`) instead of silently returning no content.
- **Table extraction**: a header row is no longer exported as data, a `<th>` inside
  `<tbody>` keeps its column names, and rows of a nested `<table>` no longer merge into
  the outer table.
- **Degenerate input**: empty, whitespace-only, comment-only and `None` HTML now
  returns empty results consistently across `process_html`/`parse_*` instead of raising
  `ParserError`/`TypeError`.
- **XML generation**: XML-illegal control characters in a page title or `aria-label`
  no longer abort compression with `ValueError`; values are sanitised instead.
- **Bounded page-controlled fields**: `href`, `placeholder` and `bind_id` are clipped
  in the Action Map, so one page cannot push hundreds of kilobytes through a single
  observation.
- **MCP hard caps**: `max_rows=0` no longer bypasses `SHADOW_WEB_MCP_MAX_ROWS`, and
  `compress_html_to_xml` enforces the HTML size cap like its siblings. Malformed cap
  environment variables are ignored with a warning instead of breaking import.
- **`agent_run` envelope**: the tool now always returns its documented envelope
  (`stop_reason="error"|"timeout"|"invalid"`) instead of raising, validates the browser
  engine, bounds concurrency (`SHADOW_WEB_AGENT_CONCURRENCY`) and wall-clock time
  (`SHADOW_WEB_AGENT_TIMEOUT`).
- **LLM calls are bounded**: the OpenAI-compatible client sets `timeout`
  (`SHADOW_WEB_LLM_TIMEOUT`, default 60 s), `max_retries`, and a per-completion
  `max_tokens` cap (`SHADOW_WEB_LLM_MAX_TOKENS`), so one slow provider can no longer
  stall a run (and its browser) for minutes.
- **Heal cache keying**: the key includes query string and fragment, and non-http URLs
  no longer share one namespace — a selector healed on one SPA route/checkout step is no
  longer reused on another. **Breaking**: existing `~/.shadow-web/heal_cache.json`
  entries are invalidated (a cache miss, nothing else).
- **Selector verification**: `LABEL_MATCH_THRESHOLD` raised to 0.8, plain substring
  containment acceptance removed, and `_tag_matches` fails closed — "Logout" no longer
  verifies as the healed "Login".
- **Cookie-consent auto-dismiss**: candidates must live inside a consent context
  (cookie/consent/gdpr markers, `role="dialog"`, `aria-modal`) and `a[href]` /
  `input[type=submit]` are excluded, so a checkout "Continue" CTA is no longer clicked
  during capture.
- **MCP session isolation**: browser state is keyed per MCP session with a per-session
  lock, so concurrent clients no longer interleave `navigate`/`click` on one shared
  page (the previous lock covered browser creation only).
- **Navigate failure hygiene**: a failed navigation clears the cached page/action map
  instead of leaving a stale `ShadowPage` that later `click(sid)` would act on.
- **Security scanner false verdicts**: the CORS probe now sends a foreign origin
  (a same-origin echo is normal, not a misconfiguration) and SRI findings for
  first-party assets are `low` severity instead of `high`.

### Changed

- README benchmarks: removed the unreproducible "−99% / 96×" row. The Rendered Text
  Index is documented as a retrieval budget, the compression table as raw-HTML → XML,
  and the end-to-end agent benchmark (1.6× total, overhead on 4/10 small pages) is
  stated next to it.
- README/`lhm.plugin.json` tool surface synced to the code (27 tools) and now guarded by
  `tests/test_tool_surface.py`, which fails if the README, the LobeHub manifest and
  `create_mcp_server()` disagree.
- `.env` loading is centralised on `runtime_env` (the server no longer has its own
  divergent loader).
- `reports/price_watch/sample-report.md`/`.json` are regenerated from a real
  cross-run diff (2026-08-13 → 2026-09-27 live scan). The previous pair was a
  diff of one scan against itself; `baseline.json`/`check.json` are kept as
  history, and `cmd_diff` now refuses to reproduce that report.
- `new_target` is reported at `medium` (assortment information) instead of
  `high`, matching the report's own severity legend where `high` means a
  price/stock/availability change.

## [0.4.1] - 2026-08-01

### Fixed

- **P0 credential resolution**: `resolve_api_credentials()` now resolves provider key and default URL as an atomic pair — a DeepSeek API key no longer lands on the OpenAI endpoint when both `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` are set.
- **P0 credential proxy override**: `SHADOW_WEB_LLM_BASE_URL` overrides the provider default URL when a named provider key is selected (DeepSeek/OpenAI/OpenRouter proxy use case).
- **P0 multi-tool protocol**: nudge/hint messages are now appended *after* all tool result messages, not interleaved between them. Fixes invalid message ordering when the model issues multiple parallel tool calls and one returns a nudge.
- **P1 content_blocks stale guard**: `_ensure_content_index()` is called *before* the repeat guard so DOM changes (click/fill/navigate) correctly reset the per-page fetch budget.

### Changed

- CLI `--browser` help now correctly states Chromium fallback is opt-in (`SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK=1`), not automatic.
- README headline: "Cut up to 99%" (matching published benchmark table), not unsupported "64–99%".
- Agent task benchmark (`benchmarks/agent_tasks.py`): 10 bounded tasks, Playwright→LLM oneshot baseline, shared answer checks, `--limit` / `--no-llm-baseline`.
- Paste-ready demo: `scripts/demo_30s.sh` + README 30-second Chromium path.

### Added

- Runtime preflight: load project `.env` for LLM keys; MCP/`exec` responses expose `browser_type` / `browser_fallback`; Camoufox→Chromium fallback is **opt-in** via `SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK=1` (fail-loud by default).
- Thick MCP tool `agent_run(goal)` — server-side bounded browser task, one JSON envelope (`stop_reason`, `complete`, `truncated`, `next_hint`). Not for bulk scrapes; thin tools stay for pagination.
- `exec` / `done` completeness contract: `done(finalAnswer, complete=…, next_hint=…)`; budget stops (`max_steps` / `max_tokens` / `no_progress`) set `truncated=true` + recovery hint.
- `exec` observe budget: dense pages (`action_count > 40`) auto-downgrade to minimal Action Map; new `shadow_query` tool; `--max-tokens` (default 15000).
- Soft finish nudge after a non-empty `schema_page` result (call `done` unless a fact is still missing).
- `exec` Content Index tools: `content_outline` + `content_blocks` for SPA/catalogs when SchemaSnap is empty; empty-schema nudge routes there.
- `exec` content budget: outline default 400t / blocks 800t, max 5 block ids, one `content_blocks` per page (repeat blocked + hard done-nudge).

## [0.4.0] - 2026-07-26

### Added

- Standalone agent loop: `shadow-web exec` CLI + `run_agent_task()` with observe→act→verify, OpenAI-compatible model adapter, `done(finalAnswer)` single-call finish, and named sessions under `~/.shadow-web/sessions/`.
- `exec` browser engines: `--browser camoufox|chromium` (default Camoufox, automatic Chromium fallback).
- Optional extra: `pip install "shadow-web[agent]"` (includes `openai` + `camoufox`).
- Console helper: `shadow-web models` for DeepSeek / OpenAI / OpenRouter env setup.

### Fixed

- Sync a11y capture skips CDP on Firefox/Camoufox (DOM-only), matching the async MCP path — `shadow-web exec` with default Camoufox no longer fails on navigate/refresh.

## [0.3.5] - 2026-07-18

### Added

- Scroll-until-content in capture readiness: under-hydrated first paints scroll the viewport to trigger lazy feeds before capture.
- Feed outline ranking: repeating mid-size items (no prices required) surface above engagement chrome; summary exposes `feeds=N`.
- Catalog outline ranking: priced product cards surface inside the first token budget instead of filter/nav chrome.
- Universal capture readiness: multilingual cookie-consent dismiss + wait for text/card hydration before first DOM capture.
- `SparseShell` page class when a page stays a cookie/anti-bot frame with almost no readable content.
- Rendered Text Index coverage, duplicate-overhead, extraction-mode, and price-signal diagnostics.
- Structural text clustering for `div`/`span`-heavy pages without site-specific selectors or tag allowlists.

### Changed

- Content indexing now assigns every readable text node to one bounded block, using semantic tags only as boundary signals.

### Removed

- Dead helpers: unused `resolve_binding` / `_RESOLVE_SCRIPT`, unused `SECURITY_HEADER_KEYS`, orphan `search_bypass_strategies.md`.
- Unused imports across capture/heal/query modules; heal endpoint now uses `get_llm_client`.

### Fixed

- Price-signal diagnostics now recognize major retail currencies (€, $, £, ¥, ₽ and ISO/local codes), not only rubles.
- Content Index MCP tools now read from the current browser session instead of requiring full HTML in every tool call.
- Outline budgets now apply to the actual output, large blocks remain discoverable, and long outlines expose a continuation offset.
- Nested content blocks no longer duplicate text; empty HTML and invalid token budgets return safe empty results.

## [0.3.4] - 2026-07-18

### Added

- Session-native Content Index MCP tools (`content_outline`, `content_blocks`) with token budgets and continuation offsets.

### Fixed

- Content Index session wiring and pre-content list retention for linked language/boilerplate lists.

## [0.3.3] - 2026-07-18

### Fixed

- Prevent healed browser actions from executing twice after a stale binding.
- Keep OpenAI and DeepSeek API keys, endpoints, and default models isolated.
- Require configured API authentication for production and remote requests.
- Make the OCI deploy install server dependencies and use a dedicated health endpoint.
- Make `cursor-setup.sh` create or merge `.cursor/mcp.json`.

### Added

- Payload limits and non-blocking compression/LLM calls in the FastAPI service.
- Installable `shadow-web-server` entrypoint inside the Python wheel.
- Regression tests for single healed-action execution, provider routing, auth, and payload limits.

## [0.3.2] - 2026-07-08

### Added

- **Cookie flag audit** in attack surface scan: Secure, HttpOnly, SameSite, `__Host-`/`__Secure-` prefix rules, third-party cookies.
- CLI flag `--no-cookies` to skip cookie checks.
- Tests for cookie rule engine.

### Changed

- README: security scan docs include cookie flag checks.

## [0.3.1] - 2026-07-08

### Added

- **HTTP security headers** in attack surface scan: `fetch_http_headers`, `analyze_http_headers` (HSTS, CSP, clickjacking, nosniff, CORS, HTTP→HTTPS redirect).
- CLI flag `--no-headers` to skip header checks.
- Tests for header rule engine; example `examples/security_scan/localpdf-headers-only.json`.

### Changed

- README: security scan docs now include HTTP header checks.

## [0.3.0] - 2026-07-08

### Added

- **AgentOps Form Fill** (`shadow_web.form_fill`) — LLM-assisted SaaS onboarding without feeding raw HTML to the model.
  - Execution modes: `auto_fill` (safe profile fields), `ask` (ambiguous fields), `handoff` (CAPTCHA/OAuth/file/anti-bot).
  - `validate_profile()` — catches typos like `companny` at plan time.
  - Post-fill **validation feedback loop**: `snapshot(diff=true)` + HTML5 `checkValidity()` → `status: validation_error`.
  - `execute_form_fill_plan_multi_step_async()` — wizard flows (plan → execute → snapshot → plan).
  - `link_form_to_actions()` — bridges `schema_form` fields to Action Map `sid`s.
- **MCP tools** (22 → **24**): `form_fill_plan`, `form_fill_execute`.
- **Attack surface security scan** (`shadow_web.security_scan`, `scripts/security_surface_scan.py`).
- **Competitor intelligence scan** (`scripts/localpdf_competitor_scan.py`) + example playbooks.
- `examples/form_fill/` — profile sample, CASE.md playbook.
- `examples/security_scan/` — sample JSON/Markdown reports.

### Changed

- README: Form Fill section, security scan docs, examples index.
- MCP skill docs updated for form fill workflow.

## [0.2.2] - 2026-07-03

### Added

- SchemaSnap export helpers (`export_table_json`, `export_table_csv`).
- Golden path demo and smoke install script.
- `web_search` MCP tool with Brave/Yahoo fallback.

## [0.2.0] - 2026-06

### Added

- SchemaSnap: `parse_tables`, `parse_forms`, `parse_lists`, `parse_page`.
- MCP `schema_*` session tools.
- Page classifier (`page_class`), diff snapshots, WebMCP bridge.

## [0.1.0] - 2026-05

### Added

- Initial release: DOM compression, Action Map, shadow_grep, self-healing, Playwright wrapper, FastAPI `/v1/compress` and `/v1/heal`.

[0.4.4]: https://github.com/ulinycoin/shadow-web/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/ulinycoin/shadow-web/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/ulinycoin/shadow-web/compare/v0.4.1...v0.4.2
[0.4.0]: https://github.com/ulinycoin/shadow-web/compare/v0.3.6...v0.4.0
[0.3.6]: https://github.com/ulinycoin/shadow-web/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/ulinycoin/shadow-web/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/ulinycoin/shadow-web/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/ulinycoin/shadow-web/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/ulinycoin/shadow-web/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/ulinycoin/shadow-web/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/ulinycoin/shadow-web/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/ulinycoin/shadow-web/compare/v0.2.0...v0.2.2
[0.2.0]: https://github.com/ulinycoin/shadow-web/releases/tag/v0.2.0
[0.1.0]: https://github.com/ulinycoin/shadow-web/releases/tag/v0.1.0
