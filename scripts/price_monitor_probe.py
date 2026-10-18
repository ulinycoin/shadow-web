#!/usr/bin/env python3
"""Probe: can Shadow Web extract price+stock from real store pages?

Mirrors the proposed Apify actor pipeline:
  goto → ShadowPage.refresh() [capture_ready inside]
  → schema_session_json path (tables)
  → content_outline (cards + price signals)
  → score success

W1: probe_one() also records the evidence a report needs — every price plane as
a structured candidate, the product name, and a capture block (timestamp,
clean-HTML sha256, viewport screenshot) — so the change layer can store the
winning plane instead of re-deriving it.

Usage:
  python scripts/price_monitor_probe.py
  python scripts/price_monitor_probe.py --json reports/price-probe.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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

# ---------------------------------------------------------------------------
# Evidence capture (W1): screenshot + hash + snippet live next to the value
# they claim to prove. Recording is best-effort but never silent: a skipped
# screenshot carries a reason, so a report can say "no evidence" honestly.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
MAX_SCREENSHOT_BYTES = 2_000_000
_TRUTHY = {"1", "true", "yes", "on"}


def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in _TRUTHY


def evidence_dir() -> Path:
    """Where screenshots go. Defaults to reports/price_watch/evidence/<UTC date>."""
    raw = (os.environ.get("SHADOW_WEB_EVIDENCE_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return ROOT / "reports" / "price_watch" / "evidence" / datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _repo_relative(path: Path) -> str:
    """Snapshot paths must be repo-relative to stay portable across machines."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return str(path)


def _snippet_chars() -> int:
    raw = os.environ.get("SHADOW_WEB_EVIDENCE_SNIPPET_CHARS")
    try:
        n = int(raw) if raw not in (None, "") else 400
    except (TypeError, ValueError):
        n = 400
    return max(80, min(2000, n))


def snippet_around(text: str, needle: str, limit: int | None = None) -> str | None:
    """Capped window around `needle` — the audit aid, not a copy of the page."""
    if not text or not needle:
        return None
    limit = limit or _snippet_chars()
    text = re.sub(r"\s+", " ", text)
    idx = text.find(str(needle))
    if idx < 0:
        return text[:limit] + ("…" if len(text) > limit else "")
    half = max(0, (limit - len(str(needle))) // 2)
    start = max(0, idx - half)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    lead = "…" if start > 0 else ""
    tail = "…" if end < len(text) else ""
    return f"{lead}{text[start:end]}{tail}"


def capture_screenshot(page, target_id: str) -> dict:
    """Viewport screenshot (2 MB cap). Returns a path + explicit skip reason."""
    if not _env_flag("SHADOW_WEB_EVIDENCE_SCREENSHOTS", "1"):
        return {"screenshot": None, "screenshot_skipped": "disabled_by_env"}
    full_page = _env_flag("SHADOW_WEB_EVIDENCE_FULL_PAGE", "0")
    try:
        out_dir = evidence_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{target_id}.png"
        page.screenshot(path=str(path), full_page=full_page, timeout=15_000)
        size = path.stat().st_size
        if size > MAX_SCREENSHOT_BYTES:
            path.unlink(missing_ok=True)
            return {"screenshot": None, "screenshot_skipped": f"too_large:{size}"}
        return {
            "screenshot": _repo_relative(path),
            "screenshot_bytes": size,
            "screenshot_full_page": full_page,
        }
    except Exception as exc:  # noqa: BLE001 — evidence must never fail a scan
        return {"screenshot": None, "screenshot_skipped": f"screenshot_error:{type(exc).__name__}"}


def _jsonld_name(jsonld: list[dict]) -> dict | None:
    for node in jsonld or []:
        raw = node.get("name")
        if not isinstance(raw, str):
            continue  # schema.org allows objects/lists here; only text is a name
        name = raw.strip()
        if name:
            return {"name": name[:140], "source": "jsonld"}
    return None


def product_name_of(page, jsonld: list[dict]) -> dict:
    """Product name for the identity cross-check: JSON-LD → og/product meta → h1."""
    found = _jsonld_name(jsonld)
    if found:
        return found
    try:
        meta = page.evaluate(
            """() => {
              const q = (sel) => document.querySelector(sel)?.getAttribute('content') || null;
              return {
                product: q('meta[property="product:title"]'),
                og: q('meta[property="og:title"]'),
                twitter: q('meta[name="twitter:title"]'),
                h1: document.querySelector('h1')?.innerText || null,
              };
            }"""
        )
    except Exception:  # noqa: BLE001
        meta = None
    if isinstance(meta, dict):
        for key in ("product", "og", "twitter"):
            value = " ".join(str(meta.get(key) or "").split())
            if value:
                return {"name": value[:140], "source": key}
        value = " ".join(str(meta.get("h1") or "").split())
        if value:
            return {"name": value[:140], "source": "h1"}
    return {"name": None, "source": "unknown"}


# Currency tokens that may follow/precede an amount in free text. Only codes the
# price regex already recognises are accepted, so "80 items" cannot become a code.
_CCY_TOKENS = {
    "usd": "USD", "eur": "EUR", "gbp": "GBP", "cad": "CAD", "aud": "AUD",
    "sek": "SEK", "nok": "NOK", "dkk": "DKK", "chf": "CHF", "pln": "PLN",
    "czk": "CZK", "huf": "HUF", "ron": "RON", "bgn": "BGN", "try": "TRY",
    "mxn": "MXN", "brl": "BRL", "zar": "ZAR", "nzd": "NZD", "hkd": "HKD",
    "sgd": "SGD", "krw": "KRW", "jpy": "JPY", "cny": "CNY", "rub": "RUB",
    "uah": "UAH", "ils": "ILS", "aed": "AED", "sar": "SAR", "thb": "THB",
    "idr": "IDR", "myr": "MYR", "php": "PHP", "vnd": "VND", "inr": "INR",
}
_CCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₽": "RUB"}


def _currency_from_text(raw: object) -> str | None:
    """Currency written next to a regex amount ("80 CAD", "CAD 80", "$59.99").

    Explicit token beats symbol: "$80 CAD" is CAD, not USD.
    """
    text = str(raw or "").strip()
    low = text.lower()
    suffix = re.search(r"([a-z]{3})\s*$", low)
    if suffix and suffix.group(1) in _CCY_TOKENS:
        return _CCY_TOKENS[suffix.group(1)]
    prefix = re.match(r"\s*([a-z]{3})\s*\d", low)
    if prefix and prefix.group(1) in _CCY_TOKENS:
        return _CCY_TOKENS[prefix.group(1)]
    for symbol, code in _CCY_SYMBOLS.items():
        if symbol in text:
            return code
    return None


def _price_candidates(
    *,
    jsonld: list[dict],
    meta_offer: dict | None,
    records: list[dict],
    outline_prices: list[str],
    html_prices: list[str],
    outline: str,
    clean_html: str,
) -> list[dict]:
    """Every price plane as a structured candidate: plane, raw, currency, path, value.

    Selection (which candidate wins, and why the others lost) happens in
    price_change_report.normalize_result — the probe only records what the page
    offered, so the report can store both the winner and the rejected evidence.
    """
    limit = _snippet_chars()
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(plane: str, raw: object, currency: object, path: str, source_text: str | None) -> None:
        raw_s = str(raw or "").strip()
        if not raw_s:
            return
        value = _parse_amount(raw_s)
        if not value or value <= 0:
            return
        if not currency and plane == "regex":
            # A free-text amount can still name its currency; keep it as evidence.
            currency = _currency_from_text(raw_s)
        key = (plane, raw_s, path)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "plane": plane,
                "raw": raw_s,
                "currency": (str(currency).strip() or None) if currency else None,
                "path": path,
                "value": round(value, 2),
                "snippet": snippet_around(source_text, raw_s, limit) if source_text else None,
            }
        )

    for node in jsonld or []:
        if node.get("price") in (None, "", 0, "0", "0.00", "0.0"):
            continue  # AggregateOffer floor stays `from_price`, never a candidate
        add("jsonld", node.get("price"), node.get("currency"), "Product.offers.price", json.dumps(node, ensure_ascii=False))

    if meta_offer and meta_offer.get("price") not in (None, "", 0, "0", "0.00"):
        meta_text = '<meta property="%s" content="%s">' % (
            meta_offer.get("path") or "product:price:amount",
            meta_offer.get("price"),
        )
        add(
            "og_meta",
            meta_offer.get("price"),
            meta_offer.get("currency"),
            meta_offer.get("path") or "product:price:amount",
            meta_text,
        )

    for record in records or []:
        for key, value in record.items():
            if not _PRICE_KEY_RE.search(str(key)):
                continue
            add("schema", value, None, f"table:{key}", f"{key}={value}")

    for i, raw in enumerate(outline_prices or []):
        add("regex", raw, None, f"content_outline#{i}", outline)
    for i, raw in enumerate(html_prices or []):
        add("regex", raw, None, f"html_regex#{i}", clean_html)

    return out



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
    """Open Graph / product meta / ShopifyAnalytics — explicit amount + currency + path."""
    try:
        offer = page.evaluate(
            """() => {
              const metaBy = (names) => {
                for (const n of names) {
                  const el = document.querySelector(`meta[property="${n}"], meta[name="${n}"]`);
                  if (el && el.getAttribute('content')) return {value: el.getAttribute('content'), name: n};
                }
                return null;
              };
              const amount = metaBy(['og:price:amount', 'product:price:amount']);
              const currency = metaBy(['og:price:currency', 'product:price:currency']);
              const itempropPrice = document.querySelector('meta[itemprop="price"]');
              const itempropCurrency = document.querySelector('meta[itemprop="priceCurrency"]');
              const price = amount ? amount.value : (itempropPrice ? itempropPrice.getAttribute('content') : null);
              const ccy = currency ? currency.value : (itempropCurrency ? itempropCurrency.getAttribute('content') : null);
              const path = amount ? amount.name : (itempropPrice ? 'meta[itemprop=price]' : null);
              if (price) return {price, currency: ccy, source: 'og', path};
              const p = window.ShopifyAnalytics && window.ShopifyAnalytics.meta
                && window.ShopifyAnalytics.meta.product;
              if (p && p.price != null) {
                const n = Number(p.price);
                const price = Number.isInteger(n) ? (n / 100).toFixed(2) : String(p.price);
                return {price, currency: ccy || null, sku: p.sku || p.id || null, source: 'shopify',
                        path: 'ShopifyAnalytics.meta.product.price'};
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

        # W1 evidence pack: every plane as a candidate, a capture block, the product name.
        product_name = product_name_of(page, jsonld)
        price_candidates = _price_candidates(
            jsonld=jsonld,
            meta_offer=meta_offer,
            records=records,
            outline_prices=outline_prices,
            html_prices=html_prices[:8],
            outline=outline,
            clean_html=clean_html or "",
        )
        shot = capture_screenshot(page, tid)
        capture = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "html_sha256": hashlib.sha256((clean_html or "").encode("utf-8")).hexdigest(),
            "screenshot": shot.get("screenshot"),
            "final_url": final_url or url,
            "http_status": http_status,
            "page_class": page_class,
            "html_chars": len(clean_html or ""),
        }
        for key in ("screenshot_skipped", "screenshot_bytes", "screenshot_full_page"):
            if key in shot:
                capture[key] = shot[key]

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
                "price_candidates": price_candidates,
                "product_name": product_name,
                "capture": capture,
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
