# Changelog

All notable changes to this project are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.1] - 2026-08-01

### Fixed

- **P0 credential resolution**: `resolve_api_credentials()` now resolves provider key and default URL as an atomic pair — a DeepSeek API key no longer lands on the OpenAI endpoint when both `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` are set.
- **P0 multi-tool protocol**: nudge/hint messages are now appended *after* all tool result messages, not interleaved between them. Fixes invalid message ordering when the model issues multiple parallel tool calls and one returns a nudge.
- **P1 content_blocks stale guard**: `_ensure_content_index()` is called *before* the repeat guard so DOM changes (click/fill/navigate) correctly reset the per-page fetch budget.

### Changed

- CLI `--browser` help now correctly states Chromium fallback is opt-in (`SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK=1`), not automatic.
- README headline: "Cut up to 99%" (matching published benchmark table), not unsupported "64–99%".

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
