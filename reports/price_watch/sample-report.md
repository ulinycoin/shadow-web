# Competitor Price Watch — Sample Change Report

**Scope:** 10 DTC stores · baseline `2026-08-13 05:34` → check `2026-08-13 05:34`
**Changes:** 0 · **Unchanged:** 10 · **Needs review:** 2 · **Trusted:** 8/10

> This is a sample report to demonstrate the change-detection pipeline for a
> paid price/stock monitor concierge. Extraction: JSON-LD > og:price > schema table >
> content outline > regex. Confidence: high = JSON-LD/og with ISO currency,
> medium = schema with currency, low = regex only. Identity: live /products/ URL.

## Changes

_No price/stock/promo changes detected between the two scans._

## Needs review

- **tentree_pdp** — price from regex only (low confidence) (low)
- **outdoorvoices_pdp** — price from regex only (low confidence) (low)

## Unchanged

- allbirds_pdp: USD 89 · stock=out_of_stock · conf=high
- brooklinen_pdp: USD 135.15 · stock=in_stock · conf=high
- publicgoods_pdp: USD 14.95 · stock=in_stock · conf=high
- glossier_pdp: EUR 27 · stock=in_stock · conf=high
- tentree_pdp: $45 · stock=in_stock · conf=low
- colourpop_pdp: USD 6.30 · stock=in_stock · conf=high
- koio_pdp: USD 70,95 · stock=unknown · conf=high
- drsquatch_pdp: USD 7.00 · stock=in_stock · conf=high
- outdoorvoices_pdp: $18 · stock=in_stock · conf=low
- rothys_pdp: EUR 45,00 · stock=out_of_stock · conf=high

## Scan summary

| Store | Kind | Page class | Price | Stock | Confidence | Planes |
|-------|------|-----------|-------|-------|------------|--------|
| allbirds_pdp | shopify_pdp | Closed Shadow | USD 89 | out_of_stock | high | content_outline,jsonld,og_meta |
| brooklinen_pdp | shopify_pdp | Closed Shadow | USD 135.15 | in_stock | high | content_outline,jsonld |
| publicgoods_pdp | shopify_pdp | Closed Shadow | USD 14.95 | in_stock | high | content_outline,jsonld,og_meta |
| glossier_pdp | shopify_pdp | Closed Shadow | EUR 27 | in_stock | high | content_outline,jsonld,og_meta |
| tentree_pdp | shopify_pdp | Closed Shadow | $45 | in_stock | low | content_outline |
| colourpop_pdp | shopify_pdp | Closed Shadow | USD 6.30 | in_stock | high | content_outline,jsonld,og_meta |
| koio_pdp | shopify_pdp | Closed Shadow | USD 70,95 | unknown | high | content_outline,og_meta |
| drsquatch_pdp | shopify_pdp | Static | USD 7.00 | in_stock | high | content_outline,jsonld |
| outdoorvoices_pdp | d2c_pdp | Closed Shadow | $18 | in_stock | low | content_outline |
| rothys_pdp | d2c_pdp | Shadow DOM | EUR 45,00 | out_of_stock | high | content_outline,og_meta |

## Method

- Success: live PDP (HTTP <400, title not 404, URL still /products/) with a product price.
- Trusted row: success + JSON-LD/og:price + ISO-4217 currency. Gate: ≥70% trusted.
- Change severity: high = price/stock/availability flip; medium = promo/confidence collapse.
- Geo: JSON-LD/og currency follows the store's localized offer (EU IP can yield EUR on US brands). Pin locale for the client market.
- Snapshots: `reports/price_watch/*.json` — rerun `scan` daily to build history.
