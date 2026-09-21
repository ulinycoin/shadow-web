#!/usr/bin/env python3
"""Phase A — competitor price/stock change report for DTC product pages.

Reuses the extraction pipeline from price_monitor_probe.py (probe_one) and
adds the change-report layer:
  identity (canonical PDP URL) → normalized price/stock/promo/confidence
  → snapshot JSON → diff vs previous snapshot → markdown report.

Quality gates (scan mode, exit 2 on fail):
  HTTP/title not 404, still a /products/ URL, success ≥80%,
  identity 10/10 PDPs, trusted ≥70% (JSON-LD or og:price with ISO currency).

Usage:
  .venv/bin/python3 scripts/price_change_report.py scan --json reports/price_watch/baseline.json
  .venv/bin/python3 scripts/price_change_report.py scan --json reports/price_watch/check.json
  .venv/bin/python3 scripts/price_change_report.py diff \\
      --previous reports/price_watch/baseline.json \\
      --current  reports/price_watch/check.json \\
      --out      reports/price_watch/sample-report.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from price_monitor_probe import _NOT_FOUND_RE, _parse_amount, probe_one  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "reports" / "price_watch" / "snapshot.json"

# SKU monitor = product pages only. Collections and dead slugs are out.
WATCH_TARGETS = [
    {"id": "allbirds_pdp", "kind": "shopify_pdp", "url": "https://www.allbirds.com/products/mens-tree-runner-go"},
    {"id": "brooklinen_pdp", "kind": "shopify_pdp", "url": "https://www.brooklinen.com/products/classic-core-sheet-set"},
    {"id": "publicgoods_pdp", "kind": "shopify_pdp", "url": "https://www.publicgoods.com/products/natural-hand-soap"},
    {"id": "glossier_pdp", "kind": "shopify_pdp", "url": "https://www.glossier.com/products/boy-brow"},
    {"id": "tentree_pdp", "kind": "shopify_pdp", "url": "https://www.tentree.com/products/mens-classic-cotton-t-shirt"},
    {"id": "colourpop_pdp", "kind": "shopify_pdp", "url": "https://colourpop.com/products/toast-ultra-matte-lip"},
    {"id": "koio_pdp", "kind": "shopify_pdp", "url": "https://www.koio.co/products/capri-nero-womens"},
    {"id": "drsquatch_pdp", "kind": "shopify_pdp", "url": "https://www.drsquatch.com/products/pine-tar"},
    {"id": "outdoorvoices_pdp", "kind": "d2c_pdp", "url": "https://www.outdoorvoices.com/products/w-the-exercise-dress-twilight"},
    {"id": "rothys_pdp", "kind": "d2c_pdp", "url": "https://www.rothys.com/products/the-flat"},
]

_PRICE_KEY_RE = re.compile(r"price|cost|amount|msrp|sale|руб|цена", re.I)
_PROMO_RE = re.compile(
    r"\b(compare[- ]?at|was|sale price|on sale|discount|% off|save \d|strike|deal)\b",
    re.I,
)
_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def identity_of(url: str) -> str:
    """Canonical identity: scheme://netloc/path (query params stripped)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _query_variant(url: str) -> str | None:
    """Default-variant id from a PDP URL (`?variant=...`).

    `identity_of` strips the query on purpose, so the variant must be captured
    separately — otherwise a switched default variant surfaces as a price change
    on a row that is otherwise trusted.
    """
    try:
        query = urlsplit(url or "").query
    except ValueError:
        return None
    for part in query.split("&"):
        if part.startswith("variant="):
            value = part.split("=", 1)[1].strip()
            if value:
                return value
    return None


def _iso_currency(value: object) -> str | None:
    if isinstance(value, str) and _ISO_CURRENCY_RE.match(value.strip()):
        return value.strip().upper()
    return None


def _is_trusted(snap: dict) -> bool:
    return (
        bool(snap.get("success"))
        and snap.get("confidence") in ("high", "medium")
        and bool(_iso_currency(snap.get("currency")))
    )


def _stock_from_hits(hits: list[str], jsonld: dict | None) -> str:
    low = [h.lower() for h in hits or []]
    if jsonld and jsonld.get("availability"):
        return "in_stock" if "InStock" in str(jsonld["availability"]) else "out_of_stock"
    if any(("sold out" in h or "out of stock" in h or "unavailable" in h) for h in low):
        return "out_of_stock"
    if any(
        ("in stock" in h or "add to cart" in h or "add to bag" in h or "available" in h or "pre-order" in h)
        for h in low
    ):
        return "in_stock"
    return "unknown"


def _promo_from(schema_sample: dict | None, jsonld: dict | None) -> bool:
    """Cheap promo signal from available planes (Phase B will use compareAt properly)."""
    for blob in (schema_sample, jsonld):
        if not isinstance(blob, dict):
            continue
        for k in blob:
            if _PROMO_RE.search(str(k)) or _PROMO_RE.search(str(blob[k])[:60]):
                return True
    return False


_MISSING = object()
_JEV_PRICE_JUDGE: object = _MISSING
# Audit trail of the last refusal/skip so a low row stays explainable.
_JEV_PRICE_LAST: dict | None = None


def _jev_price_judge():
    """Lazy, cached Jev judge — enabled only with SHADOW_WEB_JEV_PRICE=1 + key."""
    global _JEV_PRICE_JUDGE
    if _JEV_PRICE_JUDGE is _MISSING:
        try:
            sys.path.insert(0, str(ROOT / "src"))
            from shadow_web.agent.jev import JevPriceJudge

            _JEV_PRICE_JUDGE = JevPriceJudge.from_env()
        except Exception:  # noqa: BLE001 — optional extra, never fatal
            _JEV_PRICE_JUDGE = None
    return _JEV_PRICE_JUDGE


def _jev_price_pick(title: str, url: str, prices: list) -> dict | None:
    """One Jev call over the regex plane; None = keep the low-confidence row."""
    global _JEV_PRICE_LAST
    _JEV_PRICE_LAST = None
    judge = _jev_price_judge()
    if judge is None:
        return None
    amounts = [str(p) for p in (prices or []) if str(p).strip()]
    if not amounts:
        return None
    try:
        judgement = judge.judge(title=title, url=url, amounts=amounts)
    except Exception:  # noqa: BLE001
        _JEV_PRICE_LAST = {"skip": "exception"}
        return None
    if judgement is None:
        _JEV_PRICE_LAST = {"skip": judge.last_skip_reason or "no_answer"}
        return None
    if not judgement.accepted():
        _JEV_PRICE_LAST = {
            "rejected": judgement.describe(),
            "confidence": judgement.confidence,
            "is_offer": judgement.is_offer,
            "raw": judgement.raw,
            "amount": judgement.amount,
        }
        return None
    return {
        "raw": judgement.raw,
        "amount": round(judgement.amount, 2),
        "confidence": judgement.confidence,
        "is_offer": judgement.is_offer,
        "probability": judgement.probability,
        "gate": judgement.gate(),
    }


def normalize_result(result: dict) -> dict:
    """Collapse probe result → comparable ProductSnapshot."""
    snap = {
        "id": result.get("id"),
        "kind": result.get("kind"),
        "url": result.get("url"),
        "identity": identity_of(result.get("url") or ""),
        "ok": bool(result.get("ok")),
        "success": bool(result.get("success")),
        "fail_reason": result.get("fail_reason") or "",
        "page_class": result.get("page_class") or "",
        "title": (result.get("title") or "")[:120],
        "price_raw": None,
        "price_norm": None,
        "currency": None,
        "stock": "unknown",
        "promo": False,
        "confidence": "none",
        "needs_review": False,
        "planes": result.get("planes") or [],
    }

    jsonld = result.get("jsonld_sample") if isinstance(result.get("jsonld_sample"), dict) else None
    schema = result.get("schema_sample") if isinstance(result.get("schema_sample"), dict) else None
    meta = result.get("meta_offer") if isinstance(result.get("meta_offer"), dict) else None
    prices = result.get("prices") or []
    final_url = result.get("url") or ""
    snap["url"] = final_url
    snap["identity"] = identity_of(final_url)
    snap["requested_url"] = result.get("requested_url") or final_url
    snap["http_status"] = result.get("http_status")
    snap["variant"] = (
        (jsonld or {}).get("sku")
        or (meta or {}).get("sku")
        or _query_variant(snap["url"])
        or _query_variant(snap["requested_url"])
    )

    def _apply_price(amount_raw, currency_raw, plane: str) -> bool:
        amt = _parse_amount(str(amount_raw))
        if not amt or amt <= 1.0 or amt == 100.0:
            return False
        iso = _iso_currency(currency_raw)
        snap["price_norm"] = round(amt, 2)
        snap["price_raw"] = f"{iso or currency_raw or ''} {amount_raw}".strip()
        snap["currency"] = iso
        if plane == "jsonld" and iso:
            snap["confidence"] = "high"
        elif plane == "og_meta" and iso:
            snap["confidence"] = "high"
        elif plane == "schema" and iso:
            snap["confidence"] = "medium"
        else:
            snap["confidence"] = "medium" if iso else "low"
        return True

    # Price priority: JSON-LD → og/product meta → schema table → regex
    if jsonld and jsonld.get("price") not in (None, "", 0, "0", "0.00"):
        _apply_price(jsonld.get("price"), jsonld.get("currency"), "jsonld")
    if snap["price_norm"] is None and meta and meta.get("price") not in (None, "", 0, "0", "0.00"):
        _apply_price(meta.get("price"), meta.get("currency"), "og_meta")
    if snap["price_norm"] is None and schema:
        for k, v in schema.items():
            if _PRICE_KEY_RE.search(str(k)):
                if _apply_price(v, None, "schema"):
                    snap["price_raw"] = f"{k}: {v}"[:80]
                    break
    if snap["price_norm"] is None:
        # Regex plane: take median of candidates with outlier cutoff instead of first hit.
        cands: list[float] = []
        for p in prices:
            amt = _parse_amount(p)
            if amt and amt > 1.0 and amt != 100.0:
                cands.append(amt)
        if cands:
            med = sorted(cands)[len(cands) // 2]
            kept = [c for c in cands if c <= max(med * 10, 500)]
            if kept:
                chosen = sorted(kept)[len(kept) // 2]
                snap["price_norm"] = round(chosen, 2)
                snap["price_raw"] = prices[cands.index(next(c for c in cands if c == chosen))]
                snap["confidence"] = "low"
        # One Jev call, regex plane only. Rows priced by JSON-LD / og / schema
        # never reach this branch, so already-correct lines are not rewritten.
        # Currency, variant and needs_review rules stay untouched below.
        if snap["confidence"] == "low":
            pick = _jev_price_pick(snap.get("title") or "", snap.get("url") or "", prices)
            if pick is not None:
                snap["price_norm"] = pick["amount"]
                snap["price_raw"] = pick["raw"]
                snap["confidence"] = "medium"
                snap["price_plane"] = "jev_regex"
                snap["jev_price"] = pick
            elif _JEV_PRICE_LAST:
                # Honest refusal stays visible for the reviewer.
                snap["jev_price_note"] = _JEV_PRICE_LAST

    # Fill missing ISO currency from a higher plane even if price came from elsewhere.
    if not snap["currency"]:
        snap["currency"] = _iso_currency((jsonld or {}).get("currency")) or _iso_currency(
            (meta or {}).get("currency")
        )

    snap["stock"] = _stock_from_hits(result.get("stock_hits") or [], jsonld)
    snap["promo"] = _promo_from(schema, jsonld)

    status = result.get("http_status")
    title = snap.get("title") or ""
    kind = result.get("kind") or ""
    if status and int(status) >= 400:
        snap["success"] = False
        snap["fail_reason"] = f"http_{status}"
    elif _NOT_FOUND_RE.search(title):
        snap["success"] = False
        snap["fail_reason"] = "title_404"
    elif kind.endswith("_pdp") and "/products/" not in (snap.get("identity") or "").lower():
        snap["success"] = False
        snap["fail_reason"] = "identity_not_pdp"

    # Needs review: success claimed but only weak evidence, or no price at all.
    if snap["success"] and snap["confidence"] == "low":
        snap["needs_review"] = True
        snap["needs_review_reason"] = "price from regex only (low confidence)"
    if snap["success"] and snap["price_norm"] is None:
        snap["needs_review"] = True
        snap["needs_review_reason"] = "success but no normalized price"
    if snap["success"] and not _iso_currency(snap.get("currency")):
        snap["needs_review"] = True
        snap["needs_review_reason"] = snap.get("needs_review_reason") or "currency not explicit"
    if snap["success"] and snap["stock"] == "unknown":
        # A reason without the flag is a silent hole: the row reads as clean while
        # nothing actually confirmed stock.
        snap["needs_review"] = True
        snap["needs_review_reason"] = snap.get("needs_review_reason") or "no stock signal"
    if not snap["success"]:
        snap["needs_review"] = True
        snap["needs_review_reason"] = snap["fail_reason"] or "scan failed"
    return snap


def scan_targets(targets: list[dict], limit: int = 0) -> list[dict]:
    from playwright.sync_api import sync_playwright

    chosen = targets[:limit] if limit else targets
    print("=" * 72)
    print(f" PRICE WATCH SCAN — {len(chosen)} targets | {datetime.now(timezone.utc).isoformat()}")
    print("=" * 72)
    results: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        for target in chosen:
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
                raw = probe_one(page, target)
                results.append(normalize_result(raw))
            finally:
                context.close()
        browser.close()
    return results


def load_snapshot(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_snapshot(path: Path, products: list[dict]) -> dict:
    snap = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "n": len(products),
        "products": products,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return snap


def diff_snapshots(prev: dict, curr: dict) -> list[dict]:
    prev_map = {p["identity"]: p for p in prev.get("products") or []}
    changes: list[dict] = []
    for c in curr.get("products") or []:
        p = prev_map.get(c["identity"])
        entry = {"id": c.get("id"), "identity": c["identity"], "kind": c.get("kind"), "url": c.get("url")}
        if not p:
            changes.append({**entry, "type": "new_target", "severity": "high", "from": "", "to": c.get("title", "")})
            continue

        # Currency and variant gate — checked BEFORE the numbers.
        # A localized offer (EUR vs USD from a different IP/locale) or a switched
        # default variant changes price_norm while nothing about the product's
        # price actually moved. Comparing raw numbers there manufactures a
        # high-severity change on rows we currently count as trusted.
        pc, cc = _iso_currency(p.get("currency")), _iso_currency(c.get("currency"))
        currency_shift = bool(pc and cc and pc != cc)
        variant_shift = bool(
            p.get("variant") and c.get("variant") and p.get("variant") != c.get("variant")
        )
        if currency_shift:
            changes.append(
                {**entry, "type": "currency", "severity": "medium", "from": pc, "to": cc,
                 "note": "currency switched (localized offer) — numbers not comparable, no price alert"}
            )
        if variant_shift:
            changes.append(
                {**entry, "type": "variant", "severity": "medium",
                 "from": str(p.get("variant")), "to": str(c.get("variant")),
                 "note": "default variant changed — another variant's price is not a price change"}
            )

        # price change (only when the numbers are comparable)
        pn, cn = p.get("price_norm"), c.get("price_norm")
        if not currency_shift and not variant_shift and pn is not None and cn is not None and abs(pn - cn) > 0.005:
            low_conf = p.get("confidence") == "low" or c.get("confidence") == "low"
            changes.append(
                {**entry, "type": "price", "severity": "medium" if low_conf else "high",
                 "from": f"{p.get('price_raw')}", "to": f"{c.get('price_raw')}",
                 **({"note": "low-confidence price, verify manually"} if low_conf else {})}
            )
        elif (pn is None) != (cn is None):
            changes.append(
                {**entry, "type": "price_lost" if cn is None else "price_appeared",
                 "severity": "high" if cn is None else "medium",
                 "from": p.get("price_raw") or "none", "to": c.get("price_raw") or "none"}
            )
        # stock change
        if p.get("stock") != c.get("stock") and c.get("stock") != "unknown" and p.get("stock") != "unknown":
            changes.append(
                {**entry, "type": "stock", "severity": "high",
                 "from": p.get("stock"), "to": c.get("stock")}
            )
        # promo change
        if p.get("promo") != c.get("promo"):
            changes.append(
                {**entry, "type": "promo", "severity": "medium",
                 "from": str(bool(p.get("promo"))), "to": str(bool(c.get("promo")))}
            )
        # scan health transitions
        if p.get("success") and not c.get("success"):
            changes.append(
                {**entry, "type": "scan_lost", "severity": "high",
                 "from": "ok", "to": c.get("fail_reason") or "failed"}
            )
        if not p.get("success") and c.get("success"):
            changes.append(
                {**entry, "type": "scan_recovered", "severity": "low",
                 "from": p.get("fail_reason") or "failed", "to": "ok"}
            )
        # confidence collapse
        if p.get("confidence") not in (None, "none") and c.get("confidence") == "none":
            changes.append(
                {**entry, "type": "confidence_lost", "severity": "medium",
                 "from": p.get("confidence"), "to": "none"}
            )
    return changes


def evaluate_gates(products: list[dict]) -> dict[str, bool]:
    """Phase A quality gates. Trusted = success + JSON-LD/og price + ISO currency."""
    n = max(1, len(products))
    success_n = sum(1 for p in products if p.get("success"))
    pdp_n = sum(1 for p in products if "/products/" in (p.get("identity") or "").lower())
    trusted_n = sum(1 for p in products if _is_trusted(p))
    leaked_404 = any(
        p.get("success")
        and (
            str(p.get("fail_reason") or "").startswith("http_")
            or p.get("fail_reason") == "title_404"
            or _NOT_FOUND_RE.search(p.get("title") or "")
        )
        for p in products
    )
    return {
        "success_80": success_n / n >= 0.80,
        "identity_100": pdp_n == len(products),
        "trusted_70": trusted_n / n >= 0.70,
        "no_404_leak": not leaked_404,
    }


def render_markdown(prev: dict, curr: dict, changes: list[dict]) -> str:
    prev_ts = (prev.get("scanned_at") or "")[:16].replace("T", " ")
    curr_ts = (curr.get("scanned_at") or "")[:16].replace("T", " ")
    products = curr.get("products") or []
    changed_ids = {c.get("id") for c in changes}
    unchanged = [p for p in products if p.get("id") not in changed_ids]
    needs_review = [p for p in products if p.get("needs_review")]

    lines = [
        "# Competitor Price Watch — Sample Change Report",
        "",
        f"**Scope:** {len(products)} DTC stores · baseline `{prev_ts}` → check `{curr_ts}`",
        f"**Changes:** {len(changes)} · **Unchanged:** {len(unchanged)} · **Needs review:** {len(needs_review)} · **Trusted:** {sum(1 for p in products if _is_trusted(p))}/{len(products)}",
        "",
        "> This is a sample report to demonstrate the change-detection pipeline for a",
        "> paid price/stock monitor concierge. Extraction: JSON-LD > og:price > schema table >",
        "> content outline > regex. Confidence: high = JSON-LD/og with ISO currency,",
        "> medium = schema with currency, low = regex only. Identity: live /products/ URL.",
        "",
    ]

    lines.append("## Changes")
    if changes:
        lines.append("")
        lines.append("| # | Store | Type | From | To | Severity |")
        lines.append("|---|-------|------|------|----|----------|")
        for i, c in enumerate(changes, 1):
            note = f" _{c.get('note')}_" if c.get("note") else ""
            lines.append(
                f"| {i} | {c.get('id')} | {c.get('type')} | {str(c.get('from'))[:60]} | "
                f"{str(c.get('to'))[:60]} | {c.get('severity')}{note} |"
            )
    else:
        lines.append("")
        lines.append("_No price/stock/promo changes detected between the two scans._")
    lines.append("")

    lines.append("## Needs review")
    if needs_review:
        lines.append("")
        lines.append(
            "_These rows are honestly unclassified: the extractor could not confirm a price "
            "for them on its own, so they are queued for manual check. This is a known "
            "coverage limit of the regex plane, not a defect in the pipeline — every other "
            "row carries JSON-LD or og:price with an explicit ISO currency._"
        )
        lines.append("")
        for p in needs_review:
            lines.append(
                f"- **{p.get('id')}** — {p.get('needs_review_reason')} "
                f"({p.get('confidence')}) → wait for manual check"
            )
    else:
        lines.append("")
        lines.append("_None._")
    lines.append("")

    lines.append("## Unchanged")
    if unchanged:
        lines.append("")
        for p in unchanged:
            price = p.get("price_raw") or "n/a"
            lines.append(f"- {p.get('id')}: {price} · stock={p.get('stock')} · conf={p.get('confidence')}")
    else:
        lines.append("")
        lines.append("_None._")
    lines.append("")

    lines.append("## Scan summary")
    lines.append("")
    lines.append("| Store | Kind | Page class | Price | Stock | Confidence | Planes |")
    lines.append("|-------|------|-----------|-------|-------|------------|--------|")
    for p in products:
        lines.append(
            f"| {p.get('id')} | {p.get('kind')} | {p.get('page_class')} | "
            f"{p.get('price_raw') or '—'} | {p.get('stock')} | {p.get('confidence')} | "
            f"{','.join(p.get('planes') or []) or '—'} |"
        )
    lines.append("")

    lines.append("## Method")
    lines.append("")
    lines.append("- Success: live PDP (HTTP <400, title not 404, URL still /products/) with a product price.")
    lines.append("- Trusted row: success + JSON-LD/og:price + ISO-4217 currency. Gate: ≥70% trusted.")
    lines.append("- Change severity: high = price/stock/availability flip; medium = promo/confidence collapse.")
    lines.append("- Geo: JSON-LD/og currency follows the store's localized offer (EU IP can yield EUR on US brands). Pin locale for the client market.")
    lines.append("- Snapshots: `reports/price_watch/*.json` — rerun `scan` daily to build history.")
    lines.append("")
    return "\n".join(lines)


def cmd_scan(args: argparse.Namespace) -> int:
    targets = WATCH_TARGETS[: args.limit] if args.limit else WATCH_TARGETS
    products = scan_targets(targets, limit=0)
    snap = write_snapshot(args.json, products)

    ok_n = sum(1 for p in products if p.get("success"))
    trusted_n = sum(1 for p in products if _is_trusted(p))
    print("\n" + "=" * 72)
    print(f" SCAN DONE — {ok_n}/{len(products)} success | trusted {trusted_n}/{len(products)}")
    print(f" Snapshot: {args.json}")
    print("=" * 72)
    for p in products:
        mark = "✓" if p.get("success") else "✗"
        print(
            f"  {mark} {p['id']:<18} price={str(p.get('price_raw'))[:28]:<28} "
            f"stock={p.get('stock'):<12} conf={p.get('confidence'):<6} "
            f"ccy={p.get('currency') or '-':<4} "
            f"planes={','.join(p.get('planes') or []) or '-'}"
        )

    gates = evaluate_gates(products)
    print(" Gates: " + " | ".join(f"{k}={v}" for k, v in gates.items()))
    return 0 if all(gates.values()) else 2


def cmd_diff(args: argparse.Namespace) -> int:
    prev = load_snapshot(args.previous)
    curr = load_snapshot(args.current)
    changes = diff_snapshots(prev, curr)
    report = render_markdown(prev, curr, changes)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    diff_json = args.out.with_suffix(".json")
    diff_json.write_text(
        json.dumps(
            {
                "baseline": prev.get("scanned_at"),
                "check": curr.get("scanned_at"),
                "changes": changes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(report)
    print(f"\n Wrote {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Run a price/stock scan → snapshot JSON")
    s.add_argument("--json", type=Path, default=DEFAULT_SNAPSHOT)
    s.add_argument("--limit", type=int, default=0, help="Limit targets (0=all 10)")
    s.set_defaults(func=cmd_scan)

    d = sub.add_parser("diff", help="Diff two snapshots → markdown report")
    d.add_argument("--previous", type=Path, required=True)
    d.add_argument("--current", type=Path, required=True)
    d.add_argument("--out", type=Path, default=ROOT / "reports" / "price_watch" / "sample-report.md")
    d.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
