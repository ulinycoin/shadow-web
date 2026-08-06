# Competitor Price Watch — Sample Change Report

**Scope:** 10 DTC stores · baseline `2026-08-06 18:52` → check `2026-08-06 18:56`
**Changes:** 0 · **Unchanged:** 10 · **Needs review:** 6

> This is a sample report to demonstrate the change-detection pipeline for a
> paid price/stock monitor concierge. Extraction: JSON-LD > schema table >
> content outline > regex. Confidence: high = JSON-LD, medium = schema/outline,
> low = regex only. Identity: canonical URL (query stripped).

## Changes

_No price/stock/promo changes detected between the two scans._

## Needs review

- **allbirds_pdp** — price from regex only (low confidence) (low)
- **colourpop_coll** — price from regex only (low confidence) (low)
- **tentree_pdp** — price from regex only (low confidence) (low)
- **spacious_shop** — price from regex only (low confidence) (low)
- **bombas_coll** — price from regex only (low confidence) (low)
- **ridge_coll** — price from regex only (low confidence) (low)

## Unchanged

- allbirds_pdp: $50 · stock=in_stock · conf=low
- brooklinen_pdp: USD 135.15 · stock=in_stock · conf=high
- colourpop_coll: $54.00 · stock=unknown · conf=low
- tentree_pdp: $20 · stock=in_stock · conf=low
- publicgoods_pdp: USD 14.95 · stock=in_stock · conf=high
- glossier_pdp: EUR 27 · stock=in_stock · conf=high
- meundies_pdp: USD 26.0 · stock=in_stock · conf=high
- spacious_shop: £ 20.00 · stock=in_stock · conf=low
- bombas_coll: €64 · stock=unknown · conf=low
- ridge_coll: $150 · stock=unknown · conf=low

## Scan summary

| Store | Kind | Page class | Price | Stock | Confidence | Planes |
|-------|------|-----------|-------|-------|------------|--------|
| allbirds_pdp | shopify_pdp | Closed Shadow | $50 | in_stock | low | content_outline |
| brooklinen_pdp | shopify_pdp | Closed Shadow | USD 135.15 | in_stock | high | content_outline,jsonld |
| colourpop_coll | shopify_collection | Closed Shadow | $54.00 | unknown | low | content_outline |
| tentree_pdp | shopify_pdp | Closed Shadow | $20 | in_stock | low | content_outline |
| publicgoods_pdp | shopify_pdp | Closed Shadow | USD 14.95 | in_stock | high | content_outline,jsonld |
| glossier_pdp | shopify_pdp | Closed Shadow | EUR 27 | in_stock | high | content_outline,jsonld |
| meundies_pdp | shopify_pdp | Closed Shadow | USD 26.0 | in_stock | high | content_outline,jsonld |
| spacious_shop | woo_collection | Static | £ 20.00 | in_stock | low | content_outline |
| bombas_coll | shopify_collection | Closed Shadow | €64 | unknown | low | content_outline |
| ridge_coll | shopify_collection | Shadow DOM | $150 | unknown | low | content_outline |

## Method

- Success: 10/10 stores extracted a product price.
- Change severity: high = price/stock/availability flip; medium = promo/confidence collapse.
- Promo detection is intentionally cheap in this sample (Phase B adds compareAt/JSON-LD offers).
- Snapshots: `reports/price_watch/*.json` — rerun `--scan` daily to build history.
