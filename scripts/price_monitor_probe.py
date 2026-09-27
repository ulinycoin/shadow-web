#!/usr/bin/env python3
"""Probe: can Shadow Web extract price+stock from 15 real store pages?

Mirrors the proposed Apify actor pipeline:
  goto → ShadowPage.refresh() [capture_ready inside]
  → schema_session_json path (tables)
  → content_outline (cards + price signals)
  → score success

Usage:
  python scripts/price_monitor_probe.py
  python scripts/price_monitor_probe.py --json reports/price-probe.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from shadow_web.content_index import build as build_content_index
from shadow_web.content_index import outline_text, quality
from shadow_web.schema_snap import export_table_json, parse_page
from shadow_web.wrapper import ShadowPage

# Mix: Shopify PDP/collection, WooCommerce demos, niche D2C — NOT Amazon.
TARGETS = [
    {"id": "allbirds_pdp", "kind": "shopify_pdp", "url": "https://www.allbirds.com/products/mens-tree-runner-go"},
    {"id": "allbirds_coll", "kind": "shopify_collection", "url": "https://www.allbirds.com/collections/mens-shoes"},
    {"id": "brooklinen_pdp", "kind": "shopify_pdp", "url": "https://www.brooklinen.com/products/classic-core-sheet-set"},
    {"id": "mvmt_coll", "kind": "shopify_collection", "url": "https://www.mvmt.com/collections/mens-watches"},
    {"id": "colourpop_coll", "kind": "shopify_collection", "url": "https://colourpop.com/collections/best-sellers"},
    {"id": "tentree_pdp", "kind": "shopify_pdp", "url": "https://www.tentree.com/products/mens-classic-cotton-t-shirt"},
    {"id": "publicgoods_pdp", "kind": "shopify_pdp", "url": "https://www.publicgoods.com/products/natural-hand-soap"},
    {"id": "gymshark_coll", "kind": "shopify_collection", "url": "https://www.gymshark.com/collections/hoodies-jackets"},
    {"id": "glossier_pdp", "kind": "shopify_pdp", "url": "https://www.glossier.com/products/boy-brow"},
    {"id": "meundies_pdp", "kind": "shopify_pdp", "url": "https://www.meundies.com/products/mens-brief"},
    {"id": "away_pdp", "kind": "d2c_pdp", "url": "https://www.awaytravel.com/suitcases/the-bigger-carry-on"},
    {"id": "spacious_shop", "kind": "woo_collection", "url": "https://themegrilldemos.com/spacious/shop/"},
    {"id": "bombas_coll", "kind": "shopify_collection", "url": "https://bombas.com/collections/mens-socks"},
    {"id": "ridge_coll", "kind": "shopify_collection", "url": "https://ridge.com/collections/wallets"},
    {"id": "everlane_pdp", "kind": "d2c_pdp", "url": "https://www.everlane.com/products/mens-organic-cotton-crew-white"},
]

_PRICE_RE = re.compile(
    r"(?:[$€£¥₹]|USD|EUR|GBP|CAD|AUD|SEK|NOK|DKK|CHF|PLN|CZK|HUF|RON|BGN|TRY|MXN|BRL|ZAR|NZD|HKD|SGD|KRW|JPY|CNY|RUB|UAH|ILS|AED|SAR|THB|IDR|MYR|PHP|VND)"
    r"\s?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?"
    r"|"
    r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?\s?"
    r"(?:USD|EUR|GBP|CAD|AUD|SEK|NOK|DKK|CHF|PLN|CZK|[$€£¥])",
    re.I,
)
_STOCK_RE = re.compile(
    r"\b(in stock|out of stock|sold out|only \d+ left|add to (cart|bag)|"
    r"unavailable|available|pre-?order|backorder|low stock|"
    r"в наличии|нет в наличии|распродан)\b",
    re.I,
)
_PRICE_KEY_RE = re.compile(r"price|cost|amount|msrp|sale|руб|цена", re.I)
_STOCK_KEY_RE = re.compile(r"stock|avail|inventory|qty|quantity", re.I)


_NOT_FOUND_RE = re.compile(r"\b404\b|page not found|not found", re.I)


def _loads_jsonld(raw: str):
    """Parse JSON-LD even when Shopify wraps it in comments / trailing commas."""
    text = (raw or "").strip()
    text = re.sub(r"^\s*<!--.*?-->\s*", "", text, flags=re.S)
    text = text.replace("/* <![CDATA[ */", "").replace("/* ]]> */", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r",(\s*[}\]])", r"\1", text)
        return json.loads(cleaned)


def _is_aggregate_offer(offers: dict) -> bool:
    """True for schema.org AggregateOffer — `lowPrice`/`highPrice` price ranges.

    "from $X" is a range floor, not the product's price, so it must never be
    presented as a high-confidence offer.
    """
    if not isinstance(offers, dict):
        return False
    types = offers.get("@type", "")
    type_list = types if isinstance(types, list) else [types]
    if any("AggregateOffer" in str(t) for t in type_list):
        return True
    empty = (None, "", 0, "0", "0.00")
    return offers.get("price") in empty and (
        offers.get("lowPrice") not in empty or offers.get("highPrice") not in empty
    )


def _offer_price_currency(offers: dict) -> tuple[object, object]:
    if not isinstance(offers, dict):
        return None, None
    spec = offers.get("priceSpecification") or {}
    if isinstance(spec, list):
        spec = spec[0] if spec else {}
    if not isinstance(spec, dict):
        spec = {}
    price = offers.get("price") or offers.get("lowPrice") or spec.get("price")
    currency = offers.get("priceCurrency") or spec.get("priceCurrency")
    return price, currency


def _jsonld_products(page) -> list[dict]:
    """Baseline competitor approach: schema.org Product/Offer from JSON-LD."""
    found: list[dict] = []
    try:
        scripts = page.locator('script[type="application/ld+json"]').all()
    except Exception:
        return found
    for loc in scripts[:12]:
        try:
            raw = loc.inner_text(timeout=2000)
            data = _loads_jsonld(raw)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            if isinstance(graph, list):
                nodes.extend(graph)
            types = node.get("@type", "")
            type_list = types if isinstance(types, list) else [types]
            if not any("Product" in str(t) for t in type_list):
                continue
            offers = node.get("offers") or node.get("Offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            offers_dict = offers if isinstance(offers, dict) else {}
            price, currency = _offer_price_currency(offers_dict)
            from_price = None
            if _is_aggregate_offer(offers_dict):
                # Range floor only: keep it as evidence, never as `price` —
                # otherwise "from $X" becomes a high-confidence product price.
                from_price, price = price, None
            found.append(
                {
                    "name": node.get("name"),
                    "sku": node.get("sku") or node.get("productID"),
                    "price": price,
                    "from_price": from_price,
                    "currency": currency,
                    "availability": offers_dict.get("availability"),
                }
            )
    return found


def _meta_product_offer(page) -> dict | None:
    """Open Graph / product meta / ShopifyAnalytics — explicit amount + currency."""
    try:
        offer = page.evaluate(
            """() => {
              const q = (sel) => document.querySelector(sel)?.getAttribute('content') || null;
              const amount = q('meta[property="og:price:amount"]')
                || q('meta[property="product:price:amount"]')
                || q('meta[itemprop="price"]');
              const currency = q('meta[property="og:price:currency"]')
                || q('meta[property="product:price:currency"]')
                || q('meta[itemprop="priceCurrency"]');
              if (amount) return {price: amount, currency, source: 'og'};
              const p = window.ShopifyAnalytics && window.ShopifyAnalytics.meta
                && window.ShopifyAnalytics.meta.product;
              if (p && p.price != null) {
                const n = Number(p.price);
                const price = Number.isInteger(n) ? (n / 100).toFixed(2) : String(p.price);
                return {price, currency: currency || null, sku: p.sku || p.id || null, source: 'shopify'};
              }
              return null;
            }"""
        )
    except Exception:
        return None
    if not isinstance(offer, dict) or offer.get("price") in (None, "", 0, "0"):
        return None
    return offer


def _parse_amount(price_str: str) -> float | None:
    s = re.sub(r"[^\d.,]", "", price_str)
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        # 130,00 → 130.00 ; 1,300 → 1300
        s = s.replace(",", ".") if re.search(r",\d{2}$", s) else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _prices_in_text(text: str, *, structured: bool = False) -> list[str]:
    """Price-like strings in free text.

    The "noise" filter (free-shipping thresholds at $100, $0/$1 chrome) belongs
    to the regex/candidate plane. Structured amounts (JSON-LD / og / schema) are
    authoritative: a real $1.00 or $100.00 SKU must stay, so callers pass
    ``structured=True`` to keep them.
    """
    raw = list(dict.fromkeys(m.group(0) for m in _PRICE_RE.finditer(text or "")))[:40]
    kept: list[str] = []
    for p in raw:
        amt = _parse_amount(p)
        if amt is None or amt <= 0:
            continue
        if not structured and (amt <= 1.0 or amt == 100.0):
            continue
        kept.append(p)
    return kept[:20]


def _strict_product_price(prices: list[str], outline: str, jsonld: list[dict], cards: int) -> bool:
    """True only if price looks like a product offer, not chrome."""
    if any(p.get("price") not in (None, "", 0, "0", "0.00") for p in jsonld):
        return True
    if cards >= 3 and len(prices) >= 2:
        return True
    # Card lines containing a kept price
    card_price_lines = [
        ln for ln in (outline or "").splitlines()
        if "| card |" in ln and _PRICE_RE.search(ln)
    ]
    if card_price_lines:
        return True
    if len(prices) >= 2:
        return True
    return False


def _stock_hits(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in _STOCK_RE.finditer(text or "")))[:10]


def _schema_price_stock(records: list[dict], tables: list[dict]) -> dict:
    typed_currency_cols = 0
    price_vals: list[str] = []
    stock_vals: list[str] = []
    for table in tables:
        for col, typ in zip(table.get("columns") or [], table.get("types") or []):
            if typ == "currency" or _PRICE_KEY_RE.search(str(col)):
                typed_currency_cols += 1
    for rec in records:
        for k, v in rec.items():
            s = str(v)
            if _PRICE_KEY_RE.search(str(k)) or _PRICE_RE.search(s):
                price_vals.extend(_prices_in_text(s) or ([s] if s else []))
            if _STOCK_KEY_RE.search(str(k)) or _STOCK_RE.search(s):
                stock_vals.append(s[:80])
    return {
        "typed_currency_cols": typed_currency_cols,
        "price_vals": list(dict.fromkeys(price_vals))[:10],
        "stock_vals": list(dict.fromkeys(stock_vals))[:10],
    }


def _parse_outline_meta(outline: str) -> dict:
    cards = signals_idx = signals_src = 0
    mode = ""
    m = re.search(r"cards=(\d+)", outline)
    if m:
        cards = int(m.group(1))
    m = re.search(r"signals=(\d+)/(\d+)", outline)
    if m:
        signals_idx, signals_src = int(m.group(1)), int(m.group(2))
    m = re.search(r"mode=(\w+)", outline)
    if m:
        mode = m.group(1)
    card_lines = [ln for ln in outline.splitlines() if "| card |" in ln]
    return {
        "cards": cards,
        "signals_indexed": signals_idx,
        "signals_source": signals_src,
        "mode": mode,
        "card_line_count": len(card_lines),
        "outline_preview": "\n".join(outline.splitlines()[:8]),
    }


def probe_one(page, target: dict) -> dict:
    tid, url, kind = target["id"], target["url"], target["kind"]
    print(f"\n── {tid} ({kind})\n   {url}", flush=True)
    t0 = time.time()
    result: dict = {
        "id": tid,
        "kind": kind,
        "url": url,
        "ok": False,
        "success": False,
        "fail_reason": "",
    }
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        title = page.title()
        http_status = resp.status if resp else None
        final_url = page.url
        jsonld = _jsonld_products(page)
        meta_offer = _meta_product_offer(page)

        shadow = ShadowPage(page)
        clean_html, _ = shadow.refresh()
        elapsed_ms = int((time.time() - t0) * 1000)

        page_class = shadow.page_class or "Static"
        ready = shadow.capture_readiness
        blocked = page_class in ("Anti-bot", "SparseShell", "Auth-gated")

        parsed = parse_page(clean_html or "", max_rows=30) if clean_html else {"tables": [], "forms": [], "lists": []}
        tables = parsed.get("tables") or []
        records: list[dict] = []
        if tables:
            # Prefer table with currency-typed columns
            best_i, best_score = 0, -1
            for i, table in enumerate(tables):
                types = table.get("types") or []
                score = len(table.get("rows") or []) + 50 * sum(1 for t in types if t == "currency")
                if score > best_score:
                    best_score, best_i = score, i
            records = export_table_json(clean_html, table_index=best_i, max_rows=30)

        schema_ps = _schema_price_stock(records, tables)

        blocks = build_content_index(clean_html or "") if clean_html else []
        q = quality(clean_html or "", blocks) if clean_html else {}
        outline = outline_text(blocks, max_tokens=600, quality_data=q) if blocks else ""
        outline_meta = _parse_outline_meta(outline)

        # Aggregate price/stock evidence across planes
        html_prices = _prices_in_text(clean_html or "")
        outline_prices = _prices_in_text(outline)
        jsonld_prices = []
        for p in jsonld:
            if p.get("price") in (None, "", 0, "0", "0.00", "0.0"):
                # AggregateOffer rows keep only `from_price` — never a price.
                continue
            label = f"{p.get('currency') or ''} {p.get('price')}".strip()
            # Structured plane: no shipping-threshold filter here.
            if _parse_amount(label) not in (None, 0.0):
                jsonld_prices.append(label)
        meta_prices = []
        if meta_offer and meta_offer.get("price") not in (None, "", 0, "0", "0.00"):
            label = f"{meta_offer.get('currency') or ''} {meta_offer['price']}".strip()
            # Structured plane: no shipping-threshold filter here.
            if _parse_amount(label) not in (None, 0.0):
                meta_prices.append(label)
        # Schema table is a structured plane too — keep $1.00 / $100.00 values.
        schema_prices = _prices_in_text(" ".join(schema_ps["price_vals"]), structured=True)
        all_prices = list(
            dict.fromkeys(
                schema_prices + outline_prices + html_prices[:8] + jsonld_prices + meta_prices
            )
        )[:15]

        stock_text = " ".join(
            [
                outline,
                " ".join(schema_ps["stock_vals"]),
                " ".join(str(p.get("availability") or "") for p in jsonld),
                (clean_html or "")[:80_000],
            ]
        )
        stock_hits = _stock_hits(stock_text)

        loose_price = bool(all_prices) or outline_meta["signals_indexed"] > 0 or bool(jsonld_prices) or bool(meta_prices)
        has_price = (
            _strict_product_price(all_prices, outline, jsonld, outline_meta["cards"])
            or bool(jsonld_prices)
            or bool(meta_prices)
        )
        has_stock = bool(stock_hits) or any(p.get("availability") for p in jsonld)

        identity_fail = ""
        if http_status and http_status >= 400:
            identity_fail = f"http_{http_status}"
        elif _NOT_FOUND_RE.search(title or ""):
            identity_fail = "title_404"
        elif kind.endswith("_pdp") and "/products/" not in (final_url or "").lower():
            identity_fail = "identity_not_pdp"

        # Success = live PDP + strict product price AND not blocked page_class
        success = (not blocked) and has_price and (not identity_fail)
        fail_reason = ""
        if identity_fail:
            fail_reason = identity_fail
        elif blocked:
            fail_reason = f"page_class:{page_class}"
        elif not has_price:
            fail_reason = "no_price" if not loose_price else "price_noise_only"

        # Which plane delivered?
        planes = []
        if schema_ps["price_vals"] or schema_ps["typed_currency_cols"]:
            planes.append("schema_table")
        if outline_meta["cards"] > 0 or outline_meta["signals_indexed"] > 0:
            planes.append("content_outline")
        if jsonld_prices:
            planes.append("jsonld")
        if meta_prices:
            planes.append("og_meta")
        if html_prices and "content_outline" not in planes and "schema_table" not in planes:
            planes.append("html_regex")

        result.update(
            {
                "ok": True,
                "success": success,
                "fail_reason": fail_reason,
                "http_status": http_status,
                "requested_url": url,
                "url": final_url or url,
                "title": title[:120],
                "elapsed_ms": elapsed_ms,
                "page_class": page_class,
                "page_class_reason": (shadow.page_class_reason or "")[:160],
                "action_count": len(shadow.action_map),
                "ready": bool(ready.ready) if ready else None,
                "ready_reason": (ready.reason if ready else "")[:120],
                "consent_dismissed": bool(ready.consent_dismissed) if ready else False,
                "table_count": len(tables),
                "record_count": len(records),
                "schema_currency_cols": schema_ps["typed_currency_cols"],
                "schema_sample": records[0] if records else None,
                "outline_cards": outline_meta["cards"],
                "outline_signals": f"{outline_meta['signals_indexed']}/{outline_meta['signals_source']}",
                "outline_mode": outline_meta["mode"],
                "outline_card_lines": outline_meta["card_line_count"],
                "jsonld_products": len(jsonld),
                "jsonld_sample": jsonld[0] if jsonld else None,
                "meta_offer": meta_offer,
                "prices": all_prices[:8],
                "stock_hits": stock_hits[:6],
                "has_price": has_price,
                "loose_price": loose_price,
                "has_stock": has_stock,
                "planes": planes,
                "clean_html_chars": len(clean_html or ""),
            }
        )
        flag = "OK" if success else "FAIL"
        print(
            f"   [{flag}] class={page_class} price={has_price} stock={has_stock} "
            f"cards={outline_meta['cards']} tables={len(tables)} jsonld={len(jsonld)} "
            f"planes={planes} {elapsed_ms}ms",
            flush=True,
        )
        if all_prices[:3]:
            print(f"   prices: {all_prices[:3]}", flush=True)
        if fail_reason:
            print(f"   reason: {fail_reason}", flush=True)
        return result
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "success": False,
                "fail_reason": f"exception:{type(exc).__name__}",
                "error": str(exc)[:300],
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
        )
        print(f"   [ERROR] {exc}", flush=True)
        return result


def summarize(results: list[dict]) -> dict:
    n = len(results)
    success = [r for r in results if r.get("success")]
    blocked = [r for r in results if str(r.get("fail_reason", "")).startswith("page_class:")]
    no_price = [r for r in results if r.get("fail_reason") == "no_price"]
    errors = [r for r in results if not r.get("ok")]
    with_stock = [r for r in results if r.get("has_stock")]
    plane_counts: dict[str, int] = {}
    for r in results:
        for p in r.get("planes") or []:
            plane_counts[p] = plane_counts.get(p, 0) + 1
    kind_stats: dict[str, dict] = {}
    for r in results:
        k = r.get("kind") or "?"
        kind_stats.setdefault(k, {"n": 0, "success": 0})
        kind_stats[k]["n"] += 1
        if r.get("success"):
            kind_stats[k]["success"] += 1

    return {
        "n": n,
        "success_count": len(success),
        "success_rate_pct": round(100 * len(success) / max(1, n), 1),
        "blocked_count": len(blocked),
        "no_price_count": len(no_price),
        "error_count": len(errors),
        "stock_rate_pct": round(100 * len(with_stock) / max(1, n), 1),
        "plane_counts": plane_counts,
        "kind_stats": kind_stats,
        "gate_70": len(success) / max(1, n) >= 0.70,
        "gate_85": len(success) / max(1, n) >= 0.85,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=Path("reports/price-probe.json"))
    parser.add_argument("--limit", type=int, default=0, help="Limit targets (0=all)")
    args = parser.parse_args()

    targets = TARGETS[: args.limit] if args.limit else TARGETS
    print("=" * 72)
    print(" PRICE & STOCK MONITOR — 15-store probe")
    print(f" Targets: {len(targets)} | {datetime.now(timezone.utc).isoformat()}")
    print("=" * 72)

    results: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        for target in targets:
            # Fresh context+page per URL — avoids cascade after SSL/redirect failures
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1400, "height": 900},
                locale="en-US",
            )
            page = context.new_page()
            try:
                results.append(probe_one(page, target))
            finally:
                context.close()
        browser.close()

    summary = summarize(results)
    payload = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "results": results,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    print(" SUMMARY")
    print("=" * 72)
    print(
        f" Success (has price, not blocked): {summary['success_count']}/{summary['n']} "
        f"= {summary['success_rate_pct']}%"
    )
    print(f" Blocked (Anti-bot/SparseShell/Auth): {summary['blocked_count']}")
    print(f" No price extracted:                 {summary['no_price_count']}")
    print(f" Errors:                             {summary['error_count']}")
    print(f" Stock signal present:               {summary['stock_rate_pct']}%")
    print(f" Planes: {summary['plane_counts']}")
    print(f" By kind: {summary['kind_stats']}")
    print(f" Gate ≥70%: {summary['gate_70']} | Gate ≥85%: {summary['gate_85']}")
    print(f"\n Wrote {args.json}")

    print("\n Per-site:")
    for r in results:
        mark = "✓" if r.get("success") else "✗"
        print(
            f"  {mark} {r['id']:<18} class={str(r.get('page_class', '-')):<12} "
            f"planes={','.join(r.get('planes') or []) or '-':<28} "
            f"{r.get('fail_reason') or 'ok'}"
        )
    return 0 if summary["gate_70"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
