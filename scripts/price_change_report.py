#!/usr/bin/env python3
"""Phase A step 1 — sample competitor price/stock change report for DTC stores.

Reuses the extraction pipeline from price_monitor_probe.py (probe_one) and
adds the change-report layer:
  identity (canonical URL) → normalized price/stock/promo/confidence
  → snapshot JSON → diff vs previous snapshot → markdown report.

Usage:
  # 1. Baseline scan (writes snapshot)
  .venv/bin/python3 scripts/price_change_report.py --scan baseline --json reports/price_watch/baseline.json

  # 2. Check scan later (writes another snapshot)
  .venv/bin/python3 scripts/price_change_report.py --scan check --json reports/price_watch/check.json

  # 3. Diff → sample change report
  .venv/bin/python3 scripts/price_change_report.py --diff \
      --previous reports/price_watch/baseline.json \
      --current  reports/price_watch/check.json \
      --out      reports/price_watch/sample-report.md

Exit codes:
  0 — ok
  1 — I/O / parse error
  2 — gate failed: <8/10 success or identity match <10/10 (scan mode)
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

from price_monitor_probe import TARGETS, _parse_amount, probe_one  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = ROOT / "reports" / "price_watch" / "snapshot.json"

# 10 stable targets from the 15-store probe (awaytravel fail excluded).
TARGET_IDS = [
    "allbirds_pdp",
    "brooklinen_pdp",
    "tentree_pdp",
    "publicgoods_pdp",
    "glossier_pdp",
    "meundies_pdp",
    "colourpop_coll",
    "bombas_coll",
    "ridge_coll",
    "spacious_shop",
]

_PRICE_KEY_RE = re.compile(r"price|cost|amount|msrp|sale|руб|цена", re.I)
_PROMO_RE = re.compile(
    r"\b(compare[- ]?at|was|sale price|on sale|discount|% off|save \d|strike|deal)\b",
    re.I,
)


def identity_of(url: str) -> str:
    """Canonical identity: scheme://netloc/path (query params stripped)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


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
    prices = result.get("prices") or []

    # Price priority: JSON-LD (high) → schema table (medium) → outline/regex (low)
    if jsonld and jsonld.get("price") not in (None, "", 0, "0", "0.00"):
        amt = _parse_amount(str(jsonld["price"]))
        if amt and amt > 1.0 and amt != 100.0:
            snap["price_norm"] = round(amt, 2)
            snap["price_raw"] = f"{jsonld.get('currency') or ''} {jsonld['price']}".strip()
            snap["currency"] = jsonld.get("currency")
            snap["confidence"] = "high"
    if snap["price_norm"] is None and schema:
        for k, v in schema.items():
            if _PRICE_KEY_RE.search(str(k)):
                amt = _parse_amount(str(v))
                if amt and amt > 1.0 and amt != 100.0:
                    snap["price_norm"] = round(amt, 2)
                    snap["price_raw"] = f"{k}: {v}"[:80]
                    snap["confidence"] = "medium"
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

    snap["stock"] = _stock_from_hits(result.get("stock_hits") or [], jsonld)
    snap["promo"] = _promo_from(schema, jsonld)

    # Needs review: success claimed but only weak evidence, or no price at all.
    if snap["success"] and snap["confidence"] == "low":
        snap["needs_review"] = True
        snap["needs_review_reason"] = "price from regex only (low confidence)"
    if snap["success"] and snap["price_norm"] is None:
        snap["needs_review"] = True
        snap["needs_review_reason"] = "success but no normalized price"
    if snap["success"] and snap["stock"] == "unknown":
        snap["needs_review_reason"] = snap.get("needs_review_reason", "no stock signal")
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

        # price change
        pn, cn = p.get("price_norm"), c.get("price_norm")
        if pn is not None and cn is not None and abs(pn - cn) > 0.005:
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
        f"**Changes:** {len(changes)} · **Unchanged:** {len(unchanged)} · **Needs review:** {len(needs_review)}",
        "",
        "> This is a sample report to demonstrate the change-detection pipeline for a",
        "> paid price/stock monitor concierge. Extraction: JSON-LD > schema table >",
        "> content outline > regex. Confidence: high = JSON-LD, medium = schema/outline,",
        "> low = regex only. Identity: canonical URL (query stripped).",
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
        for p in needs_review:
            lines.append(f"- **{p.get('id')}** — {p.get('needs_review_reason')} ({p.get('confidence')})")
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

    ok_n = sum(1 for p in products if p.get("success"))
    lines.append("## Method")
    lines.append("")
    lines.append(f"- Success: {ok_n}/{len(products)} stores extracted a product price.")
    lines.append("- Change severity: high = price/stock/availability flip; medium = promo/confidence collapse.")
    lines.append("- Promo detection is intentionally cheap in this sample (Phase B adds compareAt/JSON-LD offers).")
    lines.append("- Snapshots: `reports/price_watch/*.json` — rerun `--scan` daily to build history.")
    lines.append("")
    return "\n".join(lines)


def cmd_scan(args: argparse.Namespace) -> int:
    targets = [t for t in TARGETS if t["id"] in TARGET_IDS]
    products = scan_targets(targets, limit=args.limit)
    snap = write_snapshot(args.json, products)

    ok_n = sum(1 for p in products if p.get("success"))
    matched = len({p.get("identity") for p in products if p.get("ok")})
    print("\n" + "=" * 72)
    print(f" SCAN DONE — {ok_n}/{len(products)} success | identities: {matched}/{len(products)}")
    print(f" Snapshot: {args.json}")
    print("=" * 72)
    for p in products:
        mark = "✓" if p.get("success") else "✗"
        print(
            f"  {mark} {p['id']:<18} price={str(p.get('price_raw'))[:28]:<28} "
            f"stock={p.get('stock'):<12} conf={p.get('confidence'):<6} "
            f"planes={','.join(p.get('planes') or []) or '-'}"
        )

    gates = {
        "success_80": ok_n / max(1, len(products)) >= 0.80,
        "identity_100": matched == len(products),
    }
    print(f" Gates: success≥80% {gates['success_80']} | identity=100% {gates['identity_100']}")
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
