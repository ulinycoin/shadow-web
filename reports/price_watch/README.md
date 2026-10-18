# `reports/price_watch/` — snapshots, evidence and the pilot gates

Artifacts for the price/stock change monitor (the primary bet in [`ROADMAP.md`](../../ROADMAP.md),
execution plan in [`docs/FAMILY_PLAN.md`](../../docs/FAMILY_PLAN.md)). Produced by
`scripts/price_change_report.py`; the only hand-written file here is this README (plus the
selection metadata in `acceptance-30.json`).

## Two sets, one purpose

| Set | Size | Role |
|---|---|---|
| **Frozen regression** (`baseline-2026-10-17.json`, `w1-2026-10-18.json`) | 10 public DTC PDPs | “Did extraction get worse?” — same URLs every time, comparable across code changes |
| **Acceptance** (`acceptance-30.json`, `acceptance-2026-10-18.json`) | 30 PDPs, 9 US DTC Shopify stores | “Is this good enough to sell?” — the Gate A1 gate for a paid report |

The 10 URLs are a regression baseline, never a marketing claim. The 30 PDPs are the pilot gate:
**0 false `verified`** and **≥27/30 with a verifiable price**; the rest must be honestly `review` /
`unknown`. A dead target is **replaced, not counted as a pass** — `acceptance-30.json` keeps a
`replacements` log (first entry: a Koio sneaker that 404'd on 2026-10-18).

## Artifacts

| Artifact | What it is |
|---|---|
| `baseline-2026-10-17.json` / `.md` | Frozen W0 baseline (pre-evidence). Kept to show what W1 changed; `cmd_diff` now refuses it as a report source. |
| `w1-2026-10-18.json` | **W1 snapshot of the frozen 10** with plane, evidence, capture, name match, statuses. |
| `acceptance-2026-10-18.json` | **W2 acceptance run** over the 30 PDPs. |
| `accuracy-2026-10-18.{json,md}` | Strict metric + reason codes for the acceptance set. |
| `accuracy-regression-2026-10-18.{json,md}` | Same report for the frozen 10. |
| `acceptance-30.json` | The acceptance set + selection rule, price hints and replacement log. |
| `diff-2026-09-27-to-2026-10-17.{md,json}` | Real cross-run diff before W1 (no evidence; kept as history). |
| `baseline-2026-09-21.json`, `pilot-baseline-report-2026-09-21.*`, `baseline-2026-09-27.json` | Older baselines behind the “7/10 trusted” claim. History. |
| `baseline.json` / `check.json` | A byte-identical self-diff. History only; `cmd_diff` refuses it. |

## The three numbers — never mix them

1. **Legacy `trusted_70`** (pre-W2): success + confidence `high|medium` + ISO currency. It says
   nothing about the plane, the product name or stock. `7/10` (2026-09-21), `8/10` (2026-10-17) are
   this metric. Shown for comparison; **never quoted to a client**.
2. **Strict `trusted` (W2)**: success + confidence + **structured plane** (`jsonld|og_meta|schema`) +
   explicit ISO currency + `name_match ∈ {match, partial}` + `price_status == verified` +
   `stock_status == verified` + stored `price_evidence`. Gate: `trusted_80_strict` ≥80% on the frozen
   10 — measured 8/10 (2026-10-18).
3. **`acceptance_30`**: 0 false `verified` **and** ≥27/30 with a verifiable price (a price that
   exists, is attributable and whose name is not contradicted). The accuracy report prints both
   `verifiable` and `price_verified` so the gap is visible — a row can have a verifiable price and
   still be machine-`review`.

Every strict number states its definition and denominator in `accuracy-*.md`; no “93%”-style headline.

## Evidence contract (W1)

For every priced row the snapshot stores:

- `price_plane` — which plane won: `jsonld`, `og_meta`, `schema`, `regex` (or `jev_regex` when the
  opt-in LLM judge accepted a pick).
- `price_evidence` — plane, raw value, currency, path (`Product.offers.price`, `og:price:amount`,
  `table:Price`, `content_outline#3`), capped snippet, name match.
- `price_candidates` — winner + up to three rejected candidates, each with `reason`
  (`lower_priority_plane`, `not_selected_by_median`, `regex_noise_filter`,
  `disagrees_with_cross_plane`).
- `capture` — `captured_at`, `html_sha256` of the cleaned HTML, screenshot path, `final_url`,
  `http_status`, `page_class`.
- `product_name` + `name_match` + `name_match_score` (JSON-LD name → og/product meta → h1, token
  overlap with title + slug).
- `price_status` / `stock_status` (`verified|review|unknown`) and `evidence` (compact view).

When several JSON-LD `Product` nodes exist (recommendation rails, bundles), the winner is the amount
another plane corroborates instead of the first node in page order.

### Capture knobs

| Env | Default | Meaning |
|---|---|---|
| `SHADOW_WEB_EVIDENCE_DIR` | `reports/price_watch/evidence/<UTC date>` | screenshot directory (stored repo-relative) |
| `SHADOW_WEB_EVIDENCE_SCREENSHOTS` | `1` | `0` disables screenshots; `capture` still carries timestamp + hash |
| `SHADOW_WEB_EVIDENCE_FULL_PAGE` | `0` | `1` = full-page screenshot (CI should never set this) |
| `SHADOW_WEB_EVIDENCE_SNIPPET_CHARS` | `400` | capped window around the winning value (80–2000) |

Screenshots: 2 MB cap, 90-day retention, never checkout/account pages. `evidence/`, `state/`,
`outbox/` are git-ignored. Within one UTC day a re-scan overwrites `<target_id>.png`; the snapshot's
`html_sha256` is what pins the artifact, not the file name.

## Commands

```bash
# frozen 10 regression scan (evidence on by default)
.venv/bin/python3 scripts/price_change_report.py scan --json reports/price_watch/w1-$(date +%F).json

# 30-PDP acceptance run (fails closed on http/dupe/empty targets)
.venv/bin/python3 scripts/price_change_report.py scan \
  --targets reports/price_watch/acceptance-30.json \
  --json reports/price_watch/acceptance-$(date +%F).json

# strict metric + reason codes
.venv/bin/python3 scripts/price_change_report.py accuracy \
  --snapshot reports/price_watch/acceptance-$(date +%F).json \
  --out reports/price_watch/accuracy-$(date +%F).json

# cross-run diff (refused unless trusted_80_strict, evidence_100, no_name_mismatch, no_404_leak)
.venv/bin/python3 scripts/price_change_report.py diff \
  --previous reports/price_watch/w1-2026-10-18.json \
  --current  reports/price_watch/w1-2026-10-19.json \
  --out      reports/price_watch/diff-2026-10-19.md
```

`scan` exits 2 when a gate fails; the snapshot is still written. Read `evaluate_gates()` in the
output rather than treating the exit code as “no data”.

## Known flaws (as of 2026-10-18)

- **Geo localization is live.** Scanned from a Riga IP, Glossier/Koio/Rothy's serve EUR (and one
  Outdoor Voices run saw a CAD amount). A client report must pin market/locale; the scanner's IP is
  not a market.
- **Outdoor Voices has no price in a structured plane** on these PDPs — 3 acceptance rows are
  regex-only (`review`, never `high`). That is the remaining extraction gap in the acceptance set.
- **`koio_pdp` (frozen 10)** redirects to the homepage (`identity_not_pdp`, `success=False`); the
  row is a broken target and is excluded from “trusted” by construction.
- **Single-token product names** can only reach `partial` (a shade name like “Toast” appears in too
  many places to prove identity).
- **Stock is a text signal** (`Add to cart`, `InStock`, …); `stock_status=verified` means a signal was
  found, not that inventory was queried.
- **Price locale parsing** (`1.234` vs `1234`, `€1.234,56`) is still the known `schemasnap` gap; the
  regex plane records currency from the token but the amount parser can misread thousands separators.
- **Screenshots are a review aid, not proof**: the JSON-LD/og value + hash + name match are the
  evidence; a human still confirms every event before delivery.

## Status against the plan

- **W0** (2026-10-17): baseline frozen, acceptance set selected, no code changes.
- **W1** (2026-10-18): evidence pack + capture + name match + statuses; 10/10 priced rows carry
  evidence and a capture block. Tests: `tests/test_price_evidence.py` (30).
- **W2** (2026-10-18): strict metric, gates, reason codes, acceptance run.
  - Frozen 10: **8/10 strict**, 0 false `verified`.
  - 30-PDP acceptance: **27/30 strict, 30/30 verifiable, 0 false `verified`** → `acceptance_30 = PASS`;
    the 3 review rows are Outdoor Voices regex-only. One dead Koio target was replaced (logged).
  - `accuracy_report()` / `accuracy` CLI state the definition, denominator and per-row reason codes.
  - Tests: `tests/test_price_accuracy.py` (12). Full suite: 595 passed, 2 skipped.
- **Gate A1: met 2026-10-18.** The next gate is A2 — a **paid** pilot — and no Watch platform is
  built before it.
- **Gate A1** and everything after it: see `docs/FAMILY_PLAN.md` §5. A paid pilot (Gate A2) is the
  only thing that unlocks W3 — an agreement is interest, not permission.
