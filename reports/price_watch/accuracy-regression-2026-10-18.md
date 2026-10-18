# Price Watch — Accuracy Report (strict metric)

**Snapshot:** `reports/price_watch/w1-2026-10-18.json` · **Rows:** 10 · **Generated:** `2026-10-18T09:31:14.181111+00:00`

**Trusted strict:** 8/10 (80.0%) · **Trusted legacy:** 8/10 (comparison only)

> Definition: trusted = success AND confidence in {high,medium} AND price_plane in {jsonld,og_meta,schema} AND ISO-4217 currency AND name_match in {match,partial} AND price_status=verified AND stock_status=verified AND price_evidence present

## Reason codes

| Code | Rows |
|---|---|
| `jsonld_ok` | 7 |
| `regex_only` | 2 |
| `og_ok` | 1 |
| `scan_failed` | 1 |
| `stock_unknown` | 1 |

## Rows

| Store | Price | Plane | Name | Price/Stock status | Verdict | Codes |
|---|---|---|---|---|---|---|
| allbirds_pdp | USD 120 | jsonld | match | verified/verified | trusted | jsonld_ok |
| brooklinen_pdp | USD 127.20 | jsonld | match | verified/verified | trusted | jsonld_ok |
| publicgoods_pdp | USD 14.95 | jsonld | match | verified/verified | trusted | jsonld_ok |
| glossier_pdp | EUR 28 | jsonld | match | verified/verified | trusted | jsonld_ok |
| tentree_pdp | USD 45.0 | jsonld | match | verified/verified | trusted | jsonld_ok |
| colourpop_pdp | USD 6.30 | jsonld | partial | verified/verified | trusted | jsonld_ok |
| koio_pdp | EUR €60,95 | regex | match | review/unknown | failed | scan_failed, regex_only, stock_unknown |
| drsquatch_pdp | USD 7.00 | jsonld | match | verified/verified | trusted | jsonld_ok |
| outdoorvoices_pdp | USD $59.99 | regex | match | review/verified | priced_review | regex_only |
| rothys_pdp | EUR 45,00 | og_meta | partial | verified/verified | trusted | og_ok |

_No percentage in this report is a marketing claim: the denominator is the scanned set and the definition above is the only one quoted._
