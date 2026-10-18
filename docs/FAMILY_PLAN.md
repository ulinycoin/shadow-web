# Shadow Family — Trusted Change Intelligence

> Execution sub-plan for `shadow-web`. **Does not replace [`ROADMAP.md`](../ROADMAP.md).**
> Date: 2026-10-17 · Version context: 0.4.4 · Status: **accepted** (collegial decision, 2026-10-17)

## 0. Why this plan exists

The family-level discussion produced five candidate capabilities: **Verify, Watch, Collect, Guard, Recipes**.
This document turns them into concrete work that serves the existing bet in `ROADMAP.md`:

> Shadow Web is not an SDK business — it is an extraction-engine business.
> Revenue comes from a managed outcome: verified competitor product-change reports
> (price / stock / promo / assortment) for DTC brands and e-commerce agencies.

The `ROADMAP.md` anti-pattern #1 says every new capability must serve one of:

1. trustworthy product change,
2. reliable browser workflow,
3. faster paid integration.

Anything that does not shorten **time-to-trusted-report** or reduce **cost/breakage of the scan pipeline**
is out of scope for this execution window. That is why this plan is narrower than the family ideation.

**One-line goal:** a customer can ask *“why did you say the price changed?”* and we can show the exact
JSON-LD/og/schema value, the product-name match, the screenshot, the timestamp, and the diff — eventually
without a human. The first pilots still require human confirmation before delivery.

## 0.1 Collegial decision (2026-10-17)

Independent consultation: **GPT 6 Astra** (`openai/gpt-6-astra` via NanoGPT), prompt = the ROADMAP bet +
this plan. Usage: ~4.3k tokens, ~$0.17.

**Joint verdict: A — one SDK + one product.** Shadow Web remains one SDK; the product is verified
price/stock change reports. Verify, Watch, Collect, Recipes, and Guard are internal capabilities, not
separate SDKs, brands, or product lines.

**Adopted from the consultation**

1. **Verify is the only P0.** Watch does not start until a **paid** pilot (Gate A2), not merely a
   pilot-ready gate. An agreement without payment is interest, not permission to build a platform.
2. **Collect is out of the 30-day scope.** URLs come from the client or manual collection. Return only
   when manual collection is a measured bottleneck for a paid account.
3. **No Watch platform before payment.** The first 1–2 pilots run on CLI + cron + email + human
   confirmation. No webhooks, dashboard, or SLA until a paid pilot asks for them.
4. **A human confirms every event before delivery.** A change is a claim: two-sided evidence plus a
   human check are required for the first pilots.

**Retained from the original plan**

- Frozen 10-URL regression baseline **plus** a 30-PDP acceptance set for Verify.
  Acceptance: 0 false `verified`, ≥27/30 with a verifiable price; the rest honestly `review` / `unknown`.
- Evidence travels with the change; no `high` without it.
- All new pipeline code stays in `scripts/`; no new MCP tools; no SDK surface expansion.
- `scripts/` is not a defense against sprawl: each workstream has a timebox and a kill criterion.

**Pivot trigger (strongest counterargument)**

A separate B2B Verify component for teams already running extraction pipelines. Switch only if **two
independent buyers** pay specifically for provenance/validation without the monitoring service, and the
integration is repeatable without the Shadow Web browser runtime. One interested developer or a popular
GitHub issue is not enough.

## 1. Scope

| Capability | Where it lands | Priority | Why |
|---|---|---|---|
| **Verify** — field-level evidence + name match | Phase A blocker | **P0** | Already listed in `ROADMAP.md` Phase A: the winning price plane is not stored; `high` cannot be audited |
| **Paid pilot sales** — outreach, offer, URL intake | Parallel to Phase A | **P0** | No engineering gate can substitute for a paying buyer |
| **Watch** — minimal two-pass change check | Phase B, **only after a paid pilot (Gate A2)** | **P1 (gated)** | Recurring revenue; agreement without payment is not permission to build a platform |
| **Collect** — catalog/pagination → SKU list | **Deferred; out of 30 days** | **P2 (trigger)** | Only when manual collection is a measured bottleneck for a paid account |
| **Recipes** — per-client JSON target/selector config | Paid onboarding only | **P2** | One config per client; no marketplace, no DSL |
| **Guard** — prompt-injection/PII sanitization | Deferred | **P2 (conditional)** | Only if LLM/heal reads untrusted page text or a real incident occurs |
| **HTTP-first** — static fetch, browser fallback | Deferred | **P2 (conditional)** | Cost/latency play; needs scan volume first |
| REST/TS SDK, hosted execution, dashboards | Not now | — | Phase C trigger is 3–5 paying accounts |
| Telegram / MTProto adapter | Not in this plan | — | Different market; archive `shadow-tg`, keep as a static-HTML fixture for a later HTTP-first test |

### Explicit non-goals for the next 30 days

- No new MCP tools; the surface stays at 27 (`tests/test_tool_surface.py` guards it).
- No new public SDK APIs in `src/shadow_web/`; pipeline work lives in `scripts/` until paid demand.
- No general-purpose crawler, no sitemap spider, no recipe marketplace.
- No LLM-first extraction; deterministic DOM/JSON-LD first, LLM only as a bounded fallback.
- No hosted browser cloud, no dashboard, no multi-tenant auth.
- No Watch platform (webhooks, dashboard, SLA) before a paid pilot.
- No Collect in the 30-day window; URLs come from the client or manual collection.
- No new runtime dependencies: `sqlite3`, `hashlib`, `json` are stdlib; screenshots come from Playwright.

## 2. Target pipeline

```
targets (SKU list / recipe)
    │
    ├─ Collect (deferred; only if a bottleneck) ── category page → product URLs → targets.json
    │
    ├─ Scan ── browser (Camoufox/Chromium) → JSON-LD / og / schema / regex
    │
    ├─ Normalize + Evidence ── winning plane, raw value, name match, screenshot, hash
    │
    ├─ Store ── snapshot + SQLite history (runs, products, changes)
    │
    ├─ Diff / Signals ── price, stock, promo, assortment; severity; confidence
    │
    └─ Report / Alert ── markdown + JSON, email/outbox + human confirmation
```

The crucial change versus today: **evidence is produced during the scan and travels with the change**,
instead of being reconstructed from a report.

## 3. Principles

1. **Evidence before features.** A new field is worthless if a customer cannot audit it.
2. **One strict definition of trusted.** Do not mix the old “≥2 price-like strings” metric with the new one.
3. **No false high.** A `high` severity change with missing evidence, name mismatch, variant ambiguity,
   or currency ambiguity is a bug, not a “needs review”.
4. **Fail visible.** A scan failure must be stored and surfaced; never overwrite a good snapshot with a failed one.
5. **Self-diff is never evidence.** Keep the existing refusal in `cmd_diff`.
6. **Scripts first, but timeboxed.** No SDK surface expansion until the monitor workflow is paid;
   `scripts/` is not permission for an unbounded platform. Each workstream has a day cap and a kill criterion.
7. **Payment is permission.** A paid pilot — not a verbal agreement — unlocks Watch. Agreement is interest.
8. **Measure on the same 10 URLs.** Every change is compared against a frozen baseline; Verify also runs
   the 30-PDP acceptance set before any paid report.

## 4. Workstreams

### W0 — Freeze the baseline (0.5 day)

**Goal:** a reproducible “before” snapshot so W1–W3 can be measured.

Tasks:
- Run the current 10-target scan and save `reports/price_watch/baseline-2026-10-17.json`:
  ```bash
  .venv/bin/python3 scripts/price_change_report.py scan \
    --json reports/price_watch/baseline-2026-10-17.json
  ```
- Save the rendered report and the current `evaluate_gates()` output.
- Record the real numbers: success, `trusted_70`, rows with `needs_review`, known false highs.
- Pick the **30-PDP acceptance set** in the chosen DTC segment; save it as
  `reports/price_watch/acceptance-30.json`. The frozen 10 URLs remain the regression baseline.
- Add a short note in `reports/price_watch/README.md` describing the baseline and its known flaws.

Acceptance:
- Artifact exists and is committed (or explicitly gitignored + path recorded).
- Baseline re-measures the legacy `trusted_70` claim (2026-09-21 said 7/10; the 2026-10-17 outcome below is not identical and explains why).
- No code changes in W0.

**W0 result (2026-10-17)** — done, no code changes:

- Scan: `reports/price_watch/baseline-2026-10-17.json` + rendered `baseline-2026-10-17.md`.
  **9/10 success, 8/10 `trusted_70`** (legacy metric), 2 rows `needs_review`, `no_404_leak` true,
  `identity_100` **false** on the `koio` row only (`koio.co/products/...` redirects to the homepage),
  `outdoorvoices` is the single regex-only row (`low`, no ISO currency).
- The old 7/10 is reproducible in magnitude but **not identical**: the same 10 URLs from an EU IP now
  yield 8/10 because Glossier (`/en-lv/`, EUR 28) and Rothy's (`/en-at/`, EUR 45) served localized
  offers — `high` under the legacy metric, not client-safe. This is the geo-pin gap, recorded, not fixed.
- Cross-run diff vs the 2026-09-27 baseline passed the diff gates (6 changes, 3 `high`:
  brooklinen `USD 135.15 → 127.20`, glossier `EUR 27 → 28`, tentree `in_stock → out_of_stock`) →
  `reports/price_watch/diff-2026-09-27-to-2026-10-17.md`. Those `high` rows are **not auditable yet**:
  no snapshot stores the winning plane or a name match. That is W1's job.
- Acceptance set: `reports/price_watch/acceptance-30.json` — 30 PDPs, 9 US DTC Shopify stores,
  USD 9.95–298, 30/30 verified HTTP 200 on `/products/<handle>` at selection; none overlap the frozen 10.
- `reports/price_watch/README.md` records the baseline, both metric definitions, known flaws and the
  evidence conventions; `evidence/`, `state/`, `outbox/` are now git-ignored.

> Important: after W1/W2 the old 7/10 is **not comparable**. The new metric is stricter and may
> legitimately drop first. Report the new denominator and the new definition; never compare across definitions.

### W1 — Evidence pack (P0, 2–3 days)

**Goal:** every `high`/`medium` price or stock row carries auditable evidence.

Changes:

1. `scripts/price_monitor_probe.py` → `probe_one()`
   - Capture a `capture` block:
     - `captured_at` (UTC ISO),
     - `html_sha256` (hash of `clean_html`; integrity of the artifact, not correctness of the price),
     - `screenshot` path (optional review aid; viewport by default, full page behind
       `SHADOW_WEB_EVIDENCE_FULL_PAGE=1`),
     - `final_url`, `http_status`, `page_class`.
   - Return structured candidates for every plane:
     ```python
     "price_candidates": [
         {"plane": "jsonld", "raw": "135.15", "currency": "USD",
          "path": "Product.offers.price", "value": 135.15},
         {"plane": "og_meta", "raw": "135.15", "currency": "USD",
          "path": "product:price:amount", "value": 135.15},
         {"plane": "schema", "raw": "$135.15", "path": "table:Price", "value": 135.15},
         {"plane": "regex", "raw": "$135.15", "path": "content_outline#3", "value": 135.15},
     ]
     ```
   - Do not store full raw HTML by default; store a capped snippet around the winning value
     (`SHADOW_WEB_EVIDENCE_SNIPPET_CHARS`, default 400).
   - Store `product_name`: JSON-LD `name`, og `product:title`, or first `h1` if available.

2. `scripts/price_change_report.py` → `_apply_price()` / `normalize_result()`
   - Extend `_apply_price(amount_raw, currency_raw, plane, *, path="", raw="")` to set:
     - `snap["price_plane"] = plane` (today only `jev_regex` is ever stored),
     - `snap["price_evidence"] = {plane, raw, currency_raw, path, value}`,
     - keep the winning candidate and the top 3 rejected candidates.
   - Add `snap["name_match"]`: `match | partial | mismatch | unknown`, computed by normalized token
     overlap between product name and page title/H1/slug. Start with a simple rule:
     normalized slug tokens + title tokens; `≥0.6` overlap = `match`, `0.3–0.6` = `partial`, `<0.3` = `mismatch`,
     missing name = `unknown`.
   - Add per-field statuses: `snap["price_status"]` and `snap["stock_status"]` in
     `verified | review | unknown`. `high` requires **both** a verified price and a verified stock signal;
     a verified price with unknown stock is a review row, not a high-confidence change.
   - JSON-LD evidence uses a path in the object (`Product.offers.price`), not a fabricated DOM selector.
     A screenshot is an aid for a human, not proof by itself.
   - If `name_match == "mismatch"`: force `needs_review=True` and never allow `high`.
   - Add a snapshot-level `evidence` block:
     ```json
     {
       "price": {"plane": "jsonld", "raw": "135.15", "currency": "USD",
                 "path": "Product.offers.price", "name_match": "match"},
       "capture": {"captured_at": "…", "html_sha256": "…", "screenshot": "…",
                   "final_url": "…", "http_status": 200, "page_class": "Static"}
     }
     ```
   - `render_markdown()`: add an **Evidence** section for every change and a compact evidence column
     in the “Needs review” table. A change without evidence is rendered as `UNVERIFIED` and never as `high`.
   - `diff_snapshots()`: copy `price_evidence` into each change entry, so the report can be audited
     without loading the full snapshot.

3. Evidence storage
   - `reports/price_watch/evidence/<YYYY-MM-DD>/<target_id>.png`.
   - `probe_one` reads `SHADOW_WEB_EVIDENCE_DIR` (default `reports/price_watch/evidence/<date>`);
     `SHADOW_WEB_EVIDENCE_SCREENSHOTS=0` disables screenshots.
   - Add `reports/price_watch/evidence/` to `.gitignore` (reports are currently tracked; screenshots must not be).
   - Retention: 90 days by default; no PII, no full-page screenshots of checkout/account pages.

Acceptance:
- 100% of `high`/`medium` rows have `price_evidence` and a `capture` block (screenshot optional).
- 0 rows with `name_match == "mismatch"` and `confidence == "high"`.
- `high` requires `price_status == "verified"` and `stock_status == "verified"`; otherwise the change is
  `review`/`unknown`, never `high`.
- Report shows evidence for every change; `UNVERIFIED` appears when evidence is missing.
- The existing test suite passes (see `ROADMAP.md` for the tracked count); new evidence tests pass.
- A manual audit of the 10 baseline URLs can answer “where did this price come from?” in under 1 minute per row.

Tests:
- `tests/test_price_evidence.py`: plane selection, winning candidate, rejected candidates, missing evidence gate,
  name-match cases (`match`, `partial`, `mismatch`, `unknown`).
- Fixtures: `tests/fixtures/price_watch/jsonld_product.json`, `og_product.json`,
  `schema_table.html`, `snapshot_with_evidence.json`.

Risks:
- Screenshot size/time; default viewport only, cap at 2 MB, never full page in CI.
- Name matching may produce false mismatches on multi-variant PDPs; start conservative (`unknown` → review,
  not `mismatch` → reject) and tune on the 10 baseline URLs.
- Evidence paths in snapshots must be relative to the repo root for portability.

### W2 — Rebuild the accuracy metric (P0, 2 days)

**Goal:** a metric that means “the product’s price, from a structured plane, matched to the product”.

New definition:

```
trusted = success
        AND confidence in {"high", "medium"}
        AND price_plane in {"jsonld", "og_meta", "schema"}
        AND ISO-4217 currency is explicit
        AND name_match in {"match", "partial"}
        AND price_status == "verified"
        AND stock_status == "verified"
        AND price_evidence is present
```

Changes:
- `_is_trusted()` uses the new definition; keep `_is_trusted_legacy()` for one release if needed
  for old reports.
- `evaluate_gates()`:
  - keep `success_80`, `identity_100`, `no_404_leak`;
  - add `evidence_100`, `no_name_mismatch`, `trusted_80_strict`, `no_false_verified`;
  - add the 30-PDP acceptance gate `acceptance_30` (0 false `verified`, ≥27/30);
  - keep `trusted_70` only as a legacy gate for old snapshots; do not use it for new reports.
- `_DIFF_REQUIRED_GATES` becomes
  `("trusted_80_strict", "evidence_100", "no_name_mismatch", "no_404_leak")`.
- Add `accuracy_report()` with per-row reason codes:
  `jsonld_ok`, `og_ok`, `schema_ok`, `regex_only`, `no_price`, `no_currency`,
  `name_mismatch`, `name_unknown`, `no_stock`, `stock_unknown`, `no_evidence`, `scan_failed`.
- Output: `reports/price_watch/accuracy-YYYY-MM-DD.json` + a short markdown summary.

Acceptance:
- **Frozen 10 URLs (regression):** ≥8/10 strict trusted, 0 false high.
- **30-PDP acceptance set (pilot):** 0 false `verified`; ≥27/30 with a verifiable price; the rest
  honestly `review` / `unknown`. This is the gate for a paid report, not a marketing accuracy claim.
- Per-field status (`verified` / `review` / `unknown`) is stored and rendered; `high` requires both
  price and stock evidence, not only a price string.
- If the 30-PDP set fails, the next action is fixing extraction/identity, not adding Watch/Collect.
- The report states the definition, denominator, and all reason codes; no “93.3%”-style claim.
- Tests: `tests/test_price_accuracy.py` with 10 synthetic rows covering every reason code.

**W1 result (2026-10-18)** — done, pipeline code only (`scripts/`, `tests/`):

- `probe_one()` returns `price_candidates` (every plane, with path + capped snippet), `product_name`
  and a `capture` block; `normalize_result()` stores `price_plane`, `price_evidence` (winner + up to 3
  rejected candidates with the reason they lost), `name_match` (+score/detail), `price_status` /
  `stock_status` and a compact `evidence` block.
- Frozen 10: **10/10 priced rows carry `price_evidence` and a capture block**; 8 verified, 2 review
  (`koio` redirects to the homepage; `outdoorvoices` is regex-only). 41 screenshots under
  `reports/price_watch/evidence/2026-10-18/` (git-ignored, 19 MB, 2 MB cap).
- Name matching: token overlap with title + slug (0.6 / 0.3 thresholds); live scores 0.67–1.0, no
  mismatch. A one-token name caps at `partial` (ColourPop "Toast"); an identity-failed page cannot
  verify a price even though its own name "matches" the homepage.
- Severity: `high` requires two-sided evidence + verified price AND stock + matched name; otherwise
  `medium` with the exact cap reason. A change without evidence renders `UNVERIFIED`.
- Several JSON-LD `Product` nodes: the winner is the amount another plane corroborates, not the first
  node in page order.
- Tests: `tests/test_price_evidence.py` (30) + `tests/fixtures/price_watch/`.

**W2 result (2026-10-18)** — done:

- Strict metric implemented (`_is_trusted`), legacy kept as `_is_trusted_legacy`; gates
  `trusted_80_strict`, `evidence_100`, `no_name_mismatch`, `no_false_verified`, `acceptance_30`
  (30-row sets only). `_DIFF_REQUIRED_GATES` = `trusted_80_strict` / `evidence_100` /
  `no_name_mismatch` / `no_404_leak` — a pre-W1 snapshot can no longer back a report.
- Frozen 10: **8/10 strict** (7 JSON-LD + 1 og; 2 review), 0 false `verified`, 0 name mismatches.
- **30-PDP acceptance (2026-10-18): 27/30 strict, 30/30 priced and verifiable, 0 false `verified`**
  → `acceptance_30 = PASS`; the 3 review rows are Outdoor Voices regex-only. One Koio target 404'd
  after selection and was **replaced** (logged in `acceptance-30.json`), not counted as a pass.
- `accuracy_report()` + `accuracy` CLI write per-row reason codes plus the definition and denominator;
  acceptance counts: `jsonld_ok` 23, `og_ok` 4, `regex_only` 3.
- Tests: `tests/test_price_accuracy.py` (12); full suite 595 passed, 2 skipped.

**Gate A1 (pilot-ready): met 2026-10-18** — W0 + W1 + W2 complete, 30-PDP acceptance passed, 0 false
`verified`. Remaining accuracy gap: 3 Outdoor Voices PDPs expose no structured price plane; fix
extraction before promising coverage, do not start W3 on the strength of an agreement.

**Gate A2 (paid pilot):** a signed **and paid** pilot. Only A2 unlocks W3. A verbal agreement or a
promise to pay is interest, not permission to build a platform.

### W3 — Minimal Watch (P1, only after Gate A2; 2–3 days)

**Goal:** daily scans with history and actionable alerts, no manual run.

Until W3 exists, deliver the first paid pilot with the existing CLI + cron + human confirmation; do not
block a paying pilot on the state store. W3 is the minimum that removes the manual bookkeeping, not a platform.

New file: `scripts/price_watch.py` (thin CLI; imports `price_change_report` and `price_monitor_probe`).
Keep `price_change_report.py` as the library/CLI for one-shot scan/diff.

Commands:

```bash
# one run, idempotent per day
.venv/bin/python3 scripts/price_watch.py run \
  --targets reports/price_watch/targets.json \
  --state reports/price_watch/state \
  --outbox reports/price_watch/outbox \
  --dry-run

# status / last successful run / failures
.venv/bin/python3 scripts/price_watch.py status --state reports/price_watch/state

# render a report from history
.venv/bin/python3 scripts/price_watch.py report --state reports/price_watch/state \
  --since 2026-10-01 --out reports/price_watch/report.md
```

State store: SQLite (`sqlite3` stdlib), `reports/price_watch/state/history.db`:

```sql
CREATE TABLE runs (
  id INTEGER PRIMARY KEY,
  scan_date TEXT NOT NULL UNIQUE,   -- YYYY-MM-DD, idempotency key
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,             -- running | ok | partial | failed
  targets_n INTEGER, success_n INTEGER, trusted_n INTEGER,
  error TEXT
);
CREATE TABLE products (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  target_id TEXT NOT NULL,
  identity TEXT NOT NULL,
  price_norm REAL, currency TEXT, stock TEXT, promo INTEGER,
  confidence TEXT, needs_review INTEGER,
  evidence_json TEXT,
  PRIMARY KEY (run_id, target_id)
);
CREATE TABLE changes (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  target_id TEXT NOT NULL,
  type TEXT NOT NULL, severity TEXT NOT NULL,
  from_json TEXT, to_json TEXT, evidence_json TEXT
);
```

Behavior:
- `run` checks `runs.scan_date`; a second run on the same day is a no-op unless `--force`.
- Retry transient failures 2× with backoff; a failed target keeps the previous price and is marked
  `scan_failed`; never delete the previous good row.
- Alert only on `high`/`medium` changes with **two-sided evidence**; `needs_review` goes to the digest,
  not to alerts.
- **A human confirms every event before it reaches the client.** The first pilots use email + a manual
  check, not an automated push.
- Alert payload: target, URL, old/new raw values, plane, name match, evidence for both sides,
  timestamp, confidence.
- Channels for the first 1–2 pilots: CLI + cron + email/manual. No webhook platform, dashboard, or SLA;
  those return only when a paid pilot asks and the manual volume hurts.
- Scheduler: cron or systemd example in the doc; no in-process daemon.

Acceptance:
- A paid pilot's agreed URLs are checked twice; both runs are `ok`/`partial` with visible reasons.
- Every delivered change has before/after evidence and a human confirmation; 0 false alerts delivered.
- Re-running the same day is a no-op; a simulated failure does not erase the previous snapshot.
- Measured manual minutes per report are recorded (the pilot's real unit economics).
- Tests: `tests/test_price_watch_state.py` for idempotency, retry, failure preservation, alert filtering.

**Gate B (paid pilot delivered):** W3 done, one paid pilot delivered, 0 false alerts, manual minutes
recorded. Only then start W5 recipes (and W4 only if collection is the measured bottleneck).

### W4 — Collect (deferred; only after a measured bottleneck)

**Goal:** turn a category page into a validated target list, not a general crawler.

**Start trigger:** a paid account's manual URL collection is measured as the bottleneck (time or
missing SKUs), and the client asks for catalog coverage. Without that, do not build it; URLs come
from the client or are collected by hand.

New file: `scripts/collect_catalog.py` (scripts-only; do not add to the SDK).

Scope:
- Input: one category URL, optional `--max-pages` (default 10, hard cap 20), same-origin only.
- Find product links via Action Map / SchemaSnap cards; pagination via `next` link, `?page=`,
  or “Load more”; dedupe by `identity_of()`.
- Validate each candidate: same host, `/products/` pattern, HTTP 200 (HEAD optional), not a collection.
- Output: `targets.json` compatible with `price_change_report.py` (`id`, `kind`, `url`), plus a
  `coverage` block: pages visited, links found, links kept, duplicates removed, rejected reasons.

Acceptance:
- 3 real DTC category pages: ≥90% of visible product links collected, 0 duplicates, <2 min per catalog.
- 10 collected URLs run through `scan` with the same gates as W2.
- No off-origin links; `url_guard` applies; no login/checkout pages collected.

### W5 — Recipes (P2, paid onboarding only; 1–2 days)

**Goal:** one JSON file per client, no new dependency, no marketplace.

Format (JSON, because the project supports Python 3.10 and has no PyYAML dependency):

```json
{
  "version": 1,
  "client": "acme",
  "locale": "en-US",
  "targets": [
    {
      "id": "acme_pdp_1",
      "url": "https://www.acme.com/products/foo",
      "kind": "shopify_pdp",
      "selector_overrides": {"price": "[data-product-price]"},
      "variant": "12345"
    }
  ],
  "evidence": {"screenshot": true, "snippet_chars": 400}
}
```

- `scripts/price_recipe.py`: load/validate/normalize into `WATCH_TARGETS`-compatible list.
- `price_change_report.py scan --recipe <path>` and `price_watch.py run --recipe <path>`.
- Validation fails closed on unknown fields, duplicate ids, non-https URLs, missing targets.
- Fixture test: one valid recipe, one invalid recipe; saved-HTML drift test for a selector override.

Acceptance:
- One recipe defines the paid pilot's agreed targets; a new client is added without code changes.
- Invalid recipe exits non-zero with a precise message.
- No public recipe registry, no DSL, no SDK API.

### W6 — Guard (P2, conditional; not in the next 30 days)

Trigger: LLM/heal receives page-controlled text (Jev is opt-in today) or a real incident occurs.

Pilot default: **disable LLM-heal** and do not send page DOM to an LLM. This is the cheapest Guard.

Only if triggered:
- Extend `src/shadow_web/heal_context.py` redaction to PII in text nodes, `contenteditable`,
  selected options, and inline script text.
- Add a bounded `content_safety` check in the scan path: hidden text, instruction-like phrases,
  suspicious links/forms; flag in evidence, never silently rewrite prices.
- Tests with fixtures; no “prompt-injection protection” marketing claim.

Do **not** build a separate security product; `ROADMAP.md` already closed the security scanner as a product line.

### W7 — HTTP-first (P2, conditional; not in the next 30 days)

Trigger: >100 scans/day or a paid client asks for lower latency/cost.

Only if triggered:
- `--http-first`: `httpx` + `lxml` fetch, parse JSON-LD/og/meta; fall back to browser when
  JSON-LD/og is absent, page is a JS shell, or `page_class` would be `SparseShell`.
- Measure: browser launches avoided, seconds saved, accuracy unchanged on the W2 baseline.
- Reuse the static-parsing lessons from `shadow-tg` (not its MCP server); no new dependency.

## 5. Order and gates

```
W0 baseline
   ↓
W1 evidence ── W2 metric ──► Gate A1: pilot-ready
   (30-PDP acceptance: 0 false verified, ≥27/30)
                                     ↓
              Sales track (parallel): 20 outreach → 5 conversations → 2 URL sets
                                     ↓
                          Gate A2: PAID pilot (payment, not agreement)
                                     ↓
                  W3 minimal watch (CLI + cron + email + human confirmation)
                                     ↓
                  Gate B: paid pilot delivered, 0 false alerts, manual minutes recorded
                                     ↓
                  W5 recipes (only if the paid client needs them)
                                     ↓
                  W4 collect (only if collection is the measured bottleneck)
                                     ↓
                  W6 guard / W7 http-first (only after a concrete trigger)
```

Hard rules:
- **Payment is permission.** A written or verbal agreement is interest; W3 starts only after Gate A2.
- Do not start W3 before Gate A1 + payment. A watch loop on unverified data automates false claims.
- Do not build Collect unless manual collection is a measured bottleneck for a paid account.
- Do not build the Watch platform (webhooks, dashboard, SLA) for the first 1–2 pilots; CLI + cron +
  email + human confirmation is the product.
- Do not start W6/W7 in this execution window. Log the trigger and move on.
- If a gate fails, fix the previous workstream; do not start the next one.

## 6. Ten-day calendar

| Day | Work | Output / decision |
|---|---|---|
| 1 | W0 baseline + pick the 30-PDP acceptance set | `baseline-2026-10-17.json`; acceptance list |
| 2–4 | W1 `capture` + `price_evidence` + name match + W2 metric + tests | evidence in snapshots; acceptance run |
| 5 | Gate A1 review + first outreach batch | 0 false `verified`, ≥27/30; 20 named prospects |
| 6–7 | Sales: 5 conversations, 2 URL sets; minimal two-pass check on confirmed URLs | pipeline + first manual change report |
| 8 | **If paid:** W3 minimal watch; **if not:** more sales, no platform | Gate A2 decision |
| 9 | Paid pilot delivery: evidence report, human confirmation, manual minutes | first paid report + unit economics |
| 10 | Gate B review | 0 false alerts + paid delivery → W5; else fix Verify/sales |

## 7. Metrics

| Metric | Definition | Target |
|---|---|---|
| `trusted_rate` | strict `_is_trusted()` / targets on the frozen 10 | ≥80% regression baseline |
| `acceptance_30` | 30-PDP acceptance set: verifiable price, correct product, no false `verified` | 0 false `verified`; ≥27/30 |
| `false_verified` | rows marked `verified` but wrong price/name/stock | 0 |
| `evidence_coverage` | rows with `price_evidence` / rows with a price | 100% for high/medium |
| `false_high` | high changes with missing evidence, name mismatch, variant/currency ambiguity | 0 |
| `name_match_rate` | `match` + `partial` / priced rows | ≥80%; `unknown` allowed but flagged |
| `scan_success` | success / targets | ≥80% |
| `identity_100` | PDP identities correct | 100%; no 404 leak |
| `paid_pilots` | paid pilots in the 30-day window | 1 paid (not merely agreed) |
| `watch_completion` | completed scheduled runs / scheduled runs (only after Gate A2) | 100% during a paid pilot |
| `false_alert_rate` | delivered alerts not confirmed | 0 |
| `manual_minutes_per_report` | human confirmation + report time for 10 PDPs | measured; use in pricing |
| `time_to_report` | scan + diff + render for 10 targets | <15 min |
| `browser_launches` | browser sessions / scan | baseline now; reduce only in W7 |

## 8. File-level checklist

| Path | Change | Status |
|---|---|---|
| `scripts/price_monitor_probe.py` | `probe_one()`: capture block, candidates, product name, screenshot/hash | done 2026-10-18 (W1) |
| `scripts/price_change_report.py` | `_apply_price` evidence, `name_match`, `_is_trusted`, gates, render, diff evidence | done 2026-10-18 (W1+W2) |
| `scripts/price_watch.py` | **new**: run/status/report, SQLite state, retries, outbox | todo (W3, after paid pilot) |
| `scripts/collect_catalog.py` | **new**: category → validated targets, pagination, dedupe | deferred (trigger only) |
| `scripts/price_recipe.py` | **new**: JSON recipe load/validate | todo (paid onboarding) |
| `tests/test_price_evidence.py` | **new**: evidence/name-match rules | done 2026-10-18 (24 tests) |
| `tests/test_price_accuracy.py` | **new**: strict metric and reason codes | done 2026-10-18 (12 tests) |
| `tests/test_price_watch_state.py` | **new**: idempotency, retry, failure preservation | todo (W3, after paid pilot) |
| `tests/test_collect_catalog.py` | **new**: fixture HTML, dedupe, same-origin | deferred (trigger only) |
| `tests/test_price_recipe.py` | **new**: valid/invalid recipe | todo (paid onboarding) |
| `tests/fixtures/price_watch/` | **new**: JSON-LD/og/schema/snapshot fixtures | done 2026-10-18 |
| `tests/fixtures/catalog/` | **new**: saved category HTML | todo |
| `reports/price_watch/acceptance-30.json` | **new**: 30-PDP acceptance set (same segment) | done 2026-10-17 (W0) |
| `reports/price_watch/evidence/` | optional screenshots; add to `.gitignore` | done 2026-10-17 (W0: `.gitignore`) |
| `reports/price_watch/baseline-2026-10-17.json` | **new**: frozen W0 baseline (10 regression URLs) | done 2026-10-17 (W0) |
| `reports/price_watch/baseline-2026-10-17.md` | **new**: rendered baseline + gates + known flaws | done 2026-10-17 (W0) |
| `reports/price_watch/diff-2026-09-27-to-2026-10-17.*` | **new**: real cross-run diff (W0 evidence) | done 2026-10-17 (W0) |
| `reports/price_watch/README.md` | baseline + evidence conventions + retention | done 2026-10-17 (W0) |
| `.gitignore` | ignore `reports/price_watch/evidence/`, `reports/price_watch/state/`, `reports/price_watch/outbox/` | done 2026-10-17 (W0) |
| `ROADMAP.md` | link to this plan; align Phase A/B checkboxes | done in this change |
| `CHANGELOG.md` | 0.4.5 entry after W1/W2 | done 2026-10-18 (under `[Unreleased]`; version bump at release) |
| `README.md` | evidence pack section after Gate A1 (not before) | later |
| `pyproject.toml` | no new dependencies; no version bump until release | — |
| `lhm.plugin.json` | no changes; no new MCP tools | — |

## 9. Open decisions

1. **Screenshot scope:** viewport by default (fast, small) vs product-block clip (more precise, needs selector).
   Start with viewport; add a clip when `selector_overrides.price` exists.
2. **Name matching:** token overlap is a starting heuristic. Do not claim “product identity verified” until
   it is tested on the 10 baseline URLs and a few multi-variant PDPs.
3. **State store:** SQLite (recommended) vs JSONL. SQLite gives idempotency and queries without a dependency.
4. **Scheduler:** cron/systemd, not an in-process daemon, until Phase C.
5. **Alert transport:** email + JSONL outbox + human confirmation for the first pilots; webhooks and
   Slack only when a paid pilot asks and the manual volume hurts.
6. **Collect scope:** category pages only, same-origin, max 20 pages; no sitemap crawling. Not in the
   30-day window.
7. **Evidence retention:** 90 days; store only viewport screenshots and a capped snippet, never full account pages.
8. **Guard/HTTP-first:** deferred; record the trigger, do not implement speculatively.
9. **Pilot offer (recommended starting point):** $199 for 14 days, up to 10 agreed PDPs, daily check,
   human-confirmed changes by email, final report; free baseline on 3 URLs; continuation $399/month
   for the same scope. Pricing is a business decision, but the offer must be concrete before outreach.
10. **Acceptance set:** 30 PDPs in the chosen DTC segment, not 10. The 10 URLs stay as a regression
    baseline; the 30-PDP set is the gate for a paid report.

## 10. Definition of done (for this plan)

- Every change in a customer report is auditable: old/new raw value, plane, name match, screenshot
  (when captured), timestamp, and hash.
- No `high` change without two-sided evidence; no report passes the diff gate with a name mismatch or
  missing evidence.
- Verify passes the 30-PDP acceptance set: 0 false `verified`, ≥27/30 with a verifiable price; the rest
  honestly `review` / `unknown`.
- One **paid** pilot is delivered: agreed URLs checked twice, every delivered change human-confirmed,
  0 false alerts, manual minutes recorded.
- A watch run (after payment) is idempotent, visible in `status`, and does not erase a good snapshot on failure.
- The strict metric is reproducible on the frozen 10 URLs and states its definition.
- The existing test suite passes; new tests cover evidence, metric, and (only after payment) state/recipes.
- `ROADMAP.md` Phase A evidence/metric checkboxes are updated only when the acceptance criteria are met.

## 11. What this means for `shadow-tg`

`shadow-tg` is **not** part of this execution window. It was a useful R&D probe: it proved that a
no-auth vertical MCP can be built quickly, and it exposed how fragile `t.me` HTML is. Keep it archived
(or in maintenance mode); if HTTP-first (W7) ever lands, its `tme_parser` can serve as one static-HTML
fixture — not as a product line, and not as a second MCP server in the family.

## 12. Decision log

- 2026-10-17: Independent consultation with GPT 6 Astra (`openai/gpt-6-astra`) returned **A — one SDK +
  one product**; family layers are internal. Verify is the only P0; Watch is gated on a **paid** pilot;
  Collect is out of the 30-day window; Recipes are paid-onboarding only.
- 2026-10-17: Payment is permission. A verbal agreement does not unlock W3; only Gate A2 (paid pilot) does.
- 2026-10-17: Verify acceptance is a 30-PDP set (0 false `verified`, ≥27/30), not the old 10-URL
  “trusted” claim. The 10-URL set is kept as a regression baseline.
- 2026-10-17: Evidence + strict metric come before scheduling. A watch loop on untrusted data is a
  false-alert generator.
- 2026-10-17: All new pipeline code lives in `scripts/` until paid demand; the SDK surface stays frozen.
  `scripts/` does not excuse an unbounded platform: timebox and kill criteria apply.
