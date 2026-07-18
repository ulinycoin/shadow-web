# Changelog

All notable changes to this project are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Catalog outline ranking: priced product cards surface inside the first token budget instead of filter/nav chrome.
- Universal capture readiness: multilingual cookie-consent dismiss + wait for text/card hydration before first DOM capture.
- `SparseShell` page class when a page stays a cookie/anti-bot frame with almost no readable content.
- Rendered Text Index coverage, duplicate-overhead, extraction-mode, and price-signal diagnostics.
- Structural text clustering for `div`/`span`-heavy pages without site-specific selectors or tag allowlists.

### Changed

- Content indexing now assigns every readable text node to one bounded block, using semantic tags only as boundary signals.

### Fixed

- Price-signal diagnostics now recognize major retail currencies (€, $, £, ¥, ₽ and ISO/local codes), not only rubles.
- Content Index MCP tools now read from the current browser session instead of requiring full HTML in every tool call.
- Outline budgets now apply to the actual output, large blocks remain discoverable, and long outlines expose a continuation offset.
- Nested content blocks no longer duplicate text; empty HTML and invalid token budgets return safe empty results.

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

[0.3.2]: https://github.com/ulinycoin/shadow-web/compare/v0.3.1...v0.3.2
[0.3.3]: https://github.com/ulinycoin/shadow-web/compare/v0.3.2...v0.3.3
[0.3.1]: https://github.com/ulinycoin/shadow-web/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/ulinycoin/shadow-web/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/ulinycoin/shadow-web/compare/v0.2.0...v0.2.2
[0.2.0]: https://github.com/ulinycoin/shadow-web/releases/tag/v0.2.0
[0.1.0]: https://github.com/ulinycoin/shadow-web/releases/tag/v0.1.0
