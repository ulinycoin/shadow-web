# Changelog

All notable changes to this project are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

[0.3.0]: https://github.com/ulinycoin/shadow-web/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/ulinycoin/shadow-web/compare/v0.2.0...v0.2.2
[0.2.0]: https://github.com/ulinycoin/shadow-web/releases/tag/v0.2.0
[0.1.0]: https://github.com/ulinycoin/shadow-web/releases/tag/v0.1.0
