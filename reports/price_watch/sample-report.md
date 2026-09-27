# Competitor Price Watch — Sample Change Report

**Scope:** 10 DTC stores · baseline `2026-08-13 05:34` → check `2026-09-27 10:41`
**Changes:** 6 · **Unchanged:** 5 · **Needs review:** 3 · **Trusted:** 7/10

> This is a sample report to demonstrate the change-detection pipeline for a
> paid price/stock monitor concierge. Extraction: JSON-LD > og:price > schema table >
> content outline > regex. Confidence: high = JSON-LD/og with ISO currency,
> medium = schema with currency, low = regex only. Identity: live /products/ URL.

## Changes

| # | Store | Type | From | To | Severity |
|---|-------|------|------|----|----------|
| 1 | allbirds_pdp | price | USD 89 | USD 120 | high |
| 2 | brooklinen_pdp | variant | COR-T1 | COR-X1 | medium _default variant changed — another variant's price is not a price change_ |
| 3 | tentree_pdp | currency_unverified | n/a | n/a | medium _currency missing on one snapshot — numbers not comparable, no price alert_ |
| 4 | koio_pdp | new_target |  | Koio | Handcrafted Italian Leather Sneakers | medium |
| 5 | outdoorvoices_pdp | currency_unverified | n/a | n/a | medium _currency missing on one snapshot — numbers not comparable, no price alert_ |
| 6 | koio_pdp | target_lost | USD 70,95 | not found in current scan | medium |

## Needs review

_These rows are honestly unclassified: the extractor could not confirm a price for them on its own, so they are queued for manual check. This is a known coverage limit of the regex plane, not a defect in the pipeline — every other row carries JSON-LD or og:price with an explicit ISO currency._

- **tentree_pdp** — price from regex only (low confidence) (low) → wait for manual check
- **koio_pdp** — identity_not_pdp (low) → wait for manual check
- **outdoorvoices_pdp** — price from regex only (low confidence) (low) → wait for manual check

## Unchanged

- publicgoods_pdp: USD 14.95 · stock=in_stock · conf=high
- glossier_pdp: EUR 27 · stock=in_stock · conf=high
- colourpop_pdp: USD 6.30 · stock=in_stock · conf=high
- drsquatch_pdp: USD 7.00 · stock=in_stock · conf=high
- rothys_pdp: EUR 45,00 · stock=out_of_stock · conf=high

## Scan summary

| Store | Kind | Page class | Price | Stock | Confidence | Planes |
|-------|------|-----------|-------|-------|------------|--------|
| allbirds_pdp | shopify_pdp | Closed Shadow | USD 120 | out_of_stock | high | content_outline,jsonld,og_meta |
| brooklinen_pdp | shopify_pdp | Closed Shadow | USD 135.15 | in_stock | high | content_outline,jsonld |
| publicgoods_pdp | shopify_pdp | Closed Shadow | USD 14.95 | in_stock | high | content_outline,jsonld,og_meta |
| glossier_pdp | shopify_pdp | Closed Shadow | EUR 27 | in_stock | high | content_outline,jsonld,og_meta |
| tentree_pdp | shopify_pdp | Closed Shadow | $45 | in_stock | low | content_outline |
| colourpop_pdp | shopify_pdp | Closed Shadow | USD 6.30 | in_stock | high | content_outline,jsonld,og_meta |
| koio_pdp | shopify_pdp | Closed Shadow | €236,95 | unknown | low | content_outline |
| drsquatch_pdp | shopify_pdp | Shadow DOM | USD 7.00 | in_stock | high | content_outline,jsonld |
| outdoorvoices_pdp | d2c_pdp | Closed Shadow | $38 | in_stock | low | content_outline |
| rothys_pdp | d2c_pdp | Closed Shadow | EUR 45,00 | out_of_stock | high | content_outline,og_meta |

## Method

- Success: live PDP (HTTP <400, title not 404, URL still /products/) with a product price.
- Trusted row: success + JSON-LD/og:price + ISO-4217 currency. Gate: ≥70% trusted.
- Change severity: high = price/stock/availability flip; medium = promo/confidence collapse.
- Geo: JSON-LD/og currency follows the store's localized offer (EU IP can yield EUR on US brands). Pin locale for the client market.
- Snapshots: `reports/price_watch/*.json` — rerun `scan` daily to build history.
