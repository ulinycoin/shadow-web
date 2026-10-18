#!/usr/bin/env python3
"""Phase A — competitor price/stock change report for DTC product pages.

Reuses the extraction pipeline from price_monitor_probe.py (probe_one) and
adds the change-report layer:
  identity (canonical PDP URL) → normalized price/stock/promo
  → evidence (plane, raw, path, capture, name match) → snapshot JSON
  → diff vs previous snapshot → markdown report.

Metrics:
  strict `_is_trusted` = success + confidence + structured plane + ISO-4217
  currency + name match + verified price AND stock + stored evidence.
  `_is_trusted_legacy` is the pre-W1 definition, kept only to read old snapshots.

Gates (scan mode, exit 2 on fail): success ≥80%, PDP identity 10/10,
`trusted_80_strict`, `evidence_100`, `no_name_mismatch`, `no_false_verified`,
`no_404_leak`; a 30-row set also evaluates `acceptance_30` (0 false `verified`,
≥27/30 verifiable). `trusted_70` (legacy) is informational.

Diff mode re-checks `trusted_80_strict` / `evidence_100` / `no_name_mismatch` /
`no_404_leak` on both snapshots and refuses a self-diff (identical `scanned_at`)
— exit 2, no report written.

Usage:
  .venv/bin/python3 scripts/price_change_report.py scan --json reports/price_watch/w1.json
  .venv/bin/python3 scripts/price_change_report.py scan --targets reports/price_watch/acceptance-30.json \\
      --json reports/price_watch/acceptance.json
  .venv/bin/python3 scripts/price_change_report.py diff \\
      --previous reports/price_watch/w1.json \\
      --current  reports/price_watch/w2.json \\
      --out      reports/price_watch/report.md
  .venv/bin/python3 scripts/price_change_report.py accuracy \\
      --snapshot reports/price_watch/acceptance.json \\
      --out      reports/price_watch/accuracy.json
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

from price_monitor_probe import (  # noqa: E402
    _NOT_FOUND_RE,
    _currency_from_text,
    _parse_amount,
    probe_one,
)

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
    r"("
    r"compare[- ]?at|sale[- ]?price|on sale|regular price|list price|"
    # Bare "was" is only a promo signal when an amount follows it ("was $120");
    # prose like "this shoe was designed for the trail" must not match.
    r"was\s*[$\u20ac\u00a3\u00a5\u20bd]?\s*\d|"
    r"discount|%\s*off|save\s+\d|strikethrough|deal price"
    r")",
    re.I,
)
# Keys that carry prose, not pricing structure: a product description reading
# "this shoe was designed for the trail" must not register as a promotion.
_PROMO_SKIP_KEYS = re.compile(
    r"^(description|name|title|headline|summary|review|text|body|alt|keywords|brand)$",
    re.I,
)
# Structured promos are the only reliable signal: an explicit compare-at / sale
# price, or a price specification that is not the regular price.
_PROMO_STRUCTURED_KEYS = re.compile(
    r"(compare[-_]?at|was[-_]?price|list[-_]?price|regular[-_]?price|strike|discount|sale[-_]?price)",
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
    """Strict W2 metric: "the product's price, from a structured plane, matched".

    This is the number a client may see. It is deliberately narrower than the
    legacy gate: a regex price, an implicit currency, an unconfirmed product
    name or unknown stock all disqualify the row.
    """
    evidence = snap.get("price_evidence") or {}
    return (
        bool(snap.get("success"))
        and snap.get("confidence") in ("high", "medium")
        and snap.get("price_plane") in STRUCTURED_PLANES
        and bool(_iso_currency(snap.get("currency")))
        and snap.get("name_match") in ("match", "partial")
        and snap.get("price_status") == "verified"
        and snap.get("stock_status") == "verified"
        and evidence.get("raw") is not None
    )


def _is_trusted_legacy(snap: dict) -> bool:
    """Pre-W2 definition, kept only to read old snapshots without rewriting them."""
    return (
        bool(snap.get("success"))
        and snap.get("confidence") in ("high", "medium")
        and bool(_iso_currency(snap.get("currency")))
    )


# schema.org availability → internal stock state. Case-insensitive on purpose:
# stores emit `InStock`, `instock` and full URLs interchangeably, and lumping
# PreOrder/BackOrder/LimitedAvailability into out_of_stock both flipped real
# sell-outs and hid state changes.
_AVAILABILITY_STATES = {
    "instock": "in_stock",
    "onlineonly": "in_stock",
    "instoreonly": "in_stock",
    "limitedavailability": "limited",
    "preorder": "preorder",
    "presale": "preorder",
    "backorder": "backorder",
    "outofstock": "out_of_stock",
    "soldout": "out_of_stock",
    "discontinued": "out_of_stock",
}


def _availability_state(value: object) -> str:
    """Map a schema.org availability token (bare or URL) to an internal state."""
    tail = str(value or "").strip().rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    token = re.sub(r"[^a-z]", "", tail.lower())
    return _AVAILABILITY_STATES.get(token, "unknown")


def _stock_from_hits(hits: list[str], jsonld: dict | None) -> str:
    low = [h.lower() for h in hits or []]
    if jsonld and jsonld.get("availability"):
        state = _availability_state(jsonld["availability"])
        if state != "unknown":
            return state
    if any(("sold out" in h or "soldout" in h or "out of stock" in h or "outofstock" in h
            or "unavailable" in h or "discontinued" in h) for h in low):
        return "out_of_stock"
    if any(("back order" in h or "backorder" in h) for h in low):
        return "backorder"
    if any(("pre-order" in h or "preorder" in h or "pre order" in h or "presale" in h) for h in low):
        return "preorder"
    if any(("low stock" in h or ("only " in h and " left" in h)) for h in low):
        return "limited"
    if any(("in stock" in h or "instock" in h or "add to cart" in h or "add to bag" in h
            or "available" in h) for h in low):
        return "in_stock"
    return "unknown"


def _promo_from(schema_sample: dict | None, jsonld: dict | None) -> bool:
    """Promo signal from structured evidence only.

    An earlier version searched the first 60 characters of *any* key or value
    for bare words like "was"/"deal", inside a 60-char window, so ordinary
    product copy ("this shoe was designed for the trail") produced recurring
    false "promotion changed" alerts.  Now only pricing keys are inspected and
    only structured markers count.
    """
    for blob in (schema_sample, jsonld):
        if not isinstance(blob, dict):
            continue
        for key, value in blob.items():
            key_text = str(key).strip()
            if _PROMO_SKIP_KEYS.search(key_text):
                continue
            if _PROMO_STRUCTURED_KEYS.search(key_text):
                return True
            if _PRICE_KEY_RE.search(key_text) and _PROMO_RE.search(str(value)[:80]):
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


STRUCTURED_PLANES = frozenset({"jsonld", "og_meta", "schema"})
_PLANE_ORDER = ("jsonld", "og_meta", "schema")
# The shipping-threshold / chrome filter belongs to the regex plane only: a real
# $1.00 or $100.00 SKU from JSON-LD / og / schema must still be priced.
_REGEX_NOISE_MAX = 1.0
_REGEX_THRESHOLD_NOISE = 100.0
_NAME_STOPWORDS = {
    "the", "and", "for", "with", "of", "a", "an", "to", "in", "on", "at", "by",
    "from", "your", "our", "new", "sale", "shop", "buy", "online",
}


def _candidate_value(raw: object) -> float | None:
    try:
        value = _parse_amount(str(raw))
    except (TypeError, ValueError):
        return None
    if not value or value <= 0:
        return None
    return round(float(value), 2)


def _candidates_from_result(result: dict) -> list[dict]:
    """Price candidates from the probe, with a compatibility path for old shapes.

    The probe (W1) already returns `price_candidates`; tests and pre-W1
    snapshots pass `jsonld_sample` / `meta_offer` / `schema_sample` / `prices`,
    which are synthesized into the same structure so there is one selection path.
    """
    provided = result.get("price_candidates")
    if isinstance(provided, list) and provided:
        out: list[dict] = []
        for cand in provided:
            if not isinstance(cand, dict):
                continue
            value = cand.get("value")
            if value is None:
                value = _candidate_value(cand.get("raw"))
            else:
                try:
                    value = round(float(value), 2)
                except (TypeError, ValueError):
                    value = None
            if not value or value <= 0:
                continue
            out.append(
                {
                    **cand,
                    "plane": str(cand.get("plane") or "regex"),
                    "value": value,
                    "path": str(cand.get("path") or ""),
                    "currency": cand.get("currency"),
                }
            )
        return out

    out = []
    jsonld = result.get("jsonld_sample")
    if isinstance(jsonld, dict):
        value = _candidate_value(jsonld.get("price"))
        if value:
            out.append(
                {
                    "plane": "jsonld",
                    "raw": str(jsonld.get("price")),
                    "currency": jsonld.get("currency"),
                    "path": "Product.offers.price",
                    "value": value,
                    "snippet": None,
                }
            )
    meta = result.get("meta_offer")
    if isinstance(meta, dict):
        value = _candidate_value(meta.get("price"))
        if value:
            out.append(
                {
                    "plane": "og_meta",
                    "raw": str(meta.get("price")),
                    "currency": meta.get("currency"),
                    "path": meta.get("path") or "product:price:amount",
                    "value": value,
                    "snippet": None,
                }
            )
    schema = result.get("schema_sample")
    if isinstance(schema, dict):
        for key, raw in schema.items():
            if not _PRICE_KEY_RE.search(str(key)):
                continue
            value = _candidate_value(raw)
            if value:
                out.append(
                    {
                        "plane": "schema",
                        "raw": str(raw),
                        "currency": None,
                        "path": f"table:{key}",
                        "value": value,
                        "snippet": None,
                    }
                )
                break
    for i, raw in enumerate(result.get("prices") or []):
        value = _candidate_value(raw)
        if value:
            out.append(
                {
                    "plane": "regex",
                    "raw": str(raw),
                    "currency": None,
                    "path": f"regex#{i}",
                    "value": value,
                    "snippet": None,
                }
            )
    return out


def _select_price(candidates: list[dict]) -> tuple[dict | None, list[dict]]:
    """Pick the winning candidate and keep why the others lost.

    Structured planes win by priority (JSON-LD → og → schema). The regex plane
    keeps its median-of-candidates rule, which is what makes a page with both a
    $14 shipping line and a $120 product price pick the product price.
    """
    if not candidates:
        return None, []

    def agreement(cand: dict) -> int:
        """How many other planes corroborate this exact amount."""
        value = cand.get("value")
        if value is None:
            return 0
        return sum(
            1
            for other in candidates
            if other is not cand
            and other.get("plane") != cand.get("plane")
            and other.get("value") is not None
            and abs(float(other["value"]) - float(value)) <= 0.005
        )

    for plane in _PLANE_ORDER:
        bucket = [c for c in candidates if c.get("plane") == plane and c.get("value")]
        if not bucket:
            continue
        # A PDP can emit several Product nodes (recommendations, bundles). The
        # first one is not necessarily the product: prefer the amount another
        # plane corroborates; ties keep page order.
        winner = max(bucket, key=agreement)
        rejected = []
        for other in candidates:
            if other is winner:
                continue
            if other.get("plane") == plane and agreement(other) == 0:
                reason = "disagrees_with_cross_plane"
            else:
                reason = "lower_priority_plane"
            rejected.append({**other, "reason": other.get("reason") or reason})
        return winner, rejected

    regex = [c for c in candidates if c.get("plane") == "regex" and c.get("value")]
    kept: list[dict] = []
    dropped: list[dict] = []
    for cand in regex:
        if cand["value"] > _REGEX_NOISE_MAX and cand["value"] != _REGEX_THRESHOLD_NOISE:
            kept.append(cand)
        else:
            dropped.append({**cand, "reason": "regex_noise_filter"})
    if not kept:
        return None, dropped

    median = sorted(c["value"] for c in kept)[len(kept) // 2]
    eligible = sorted(
        (c for c in kept if c["value"] <= max(median * 10, 500)), key=lambda c: c["value"]
    )
    winner = eligible[len(eligible) // 2]
    rejected = dropped + [
        {**cand, "reason": "not_selected_by_median"} for cand in kept if cand is not winner
    ]
    return winner, rejected


def _apply_price(snap: dict, cand: dict) -> bool:
    value = cand.get("value")
    if not value:
        return False
    iso = _iso_currency(cand.get("currency"))
    raw = str(cand.get("raw") or "").strip()
    snap["price_norm"] = round(float(value), 2)
    snap["price_raw"] = f"{iso or cand.get('currency') or ''} {raw}".strip()
    snap["currency"] = iso
    snap["price_plane"] = cand.get("plane")
    if cand.get("plane") in ("jsonld", "og_meta") and iso:
        snap["confidence"] = "high"
    elif cand.get("plane") == "schema" and iso:
        snap["confidence"] = "medium"
    elif cand.get("plane") == "regex":
        # A regex price stays low even when the amount names its currency: the
        # value is still a guess about which number on the page is the product.
        snap["confidence"] = "low"
    else:
        snap["confidence"] = "medium" if iso else "low"
    return True


def _name_tokens(text: object) -> list[str]:
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return [w for w in words if len(w) > 1 and w not in _NAME_STOPWORDS]


def name_match_of(product_name: object, title: object, url: object) -> dict:
    """Token overlap between the product name and the page title + slug.

    Deliberately conservative: called only on the winning price's page, and an
    unknown name is `unknown` (review), never `match`.
    """
    name_tokens = list(dict.fromkeys(_name_tokens(product_name)))
    if not name_tokens:
        return {
            "name_match": "unknown",
            "score": None,
            "matched": [],
            "missing": [],
            "reason": "no product name captured",
        }
    page_tokens = set(_name_tokens(title))
    slug = urlsplit(url or "").path.rstrip("/").rsplit("/", 1)[-1]
    page_tokens |= set(_name_tokens(slug.replace("-", " ").replace("_", " ")))
    matched = [t for t in name_tokens if t in page_tokens]
    score = len(matched) / len(name_tokens)
    if score >= 0.6:
        verdict = "match"
    elif score >= 0.3:
        verdict = "partial"
    else:
        verdict = "mismatch"
    # A one-token name ("Toast", "Flat") appears in far too many places to prove
    # identity; it can be corroborated, never confirmed.
    single_token = len(name_tokens) == 1
    if single_token and verdict == "match":
        verdict = "partial"
    return {
        "name_match": verdict,
        "score": round(score, 2),
        "matched": matched[:8],
        "missing": [t for t in name_tokens if t not in page_tokens][:8],
        "single_token": single_token,
    }


def normalize_result(result: dict) -> dict:
    """Collapse probe result → comparable ProductSnapshot with auditable evidence.

    W1 contract: a priced row carries `price_plane`, `price_evidence` (plane,
    raw value, path, currency, snippet), `name_match`, per-field statuses and a
    `capture` block. A row without that evidence can still be compared, but it
    can never produce a `high` change.
    """
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
        "product_name": None,
        "name_match": "unknown",
        "price_plane": None,
        "price_evidence": {},
        "price_candidates": [],
        "price_status": "unknown",
        "stock_status": "unknown",
        "capture": result.get("capture") or {},
    }

    jsonld = result.get("jsonld_sample") if isinstance(result.get("jsonld_sample"), dict) else None
    schema = result.get("schema_sample") if isinstance(result.get("schema_sample"), dict) else None
    meta = result.get("meta_offer") if isinstance(result.get("meta_offer"), dict) else None
    final_url = result.get("url") or ""
    snap["url"] = final_url
    snap["identity"] = identity_of(final_url)
    snap["requested_url"] = result.get("requested_url") or final_url
    snap["http_status"] = result.get("http_status")
    # Variant identity: a `?variant=` id tracks the selected variant, while a
    # product-level `sku` is the same for every variant — prefer the query id.
    snap["variant"] = (
        _query_variant(snap["url"])
        or _query_variant(snap["requested_url"])
        or (jsonld or {}).get("sku")
        or (meta or {}).get("sku")
    )

    # Product identity (evidence, not decoration): JSON-LD name → probe capture.
    name_blob = result.get("product_name")
    if isinstance(name_blob, dict):
        snap["product_name"] = name_blob.get("name") or None
        snap["product_name_source"] = name_blob.get("source") or "unknown"
    elif isinstance(name_blob, str) and name_blob.strip():
        snap["product_name"] = name_blob.strip()
        snap["product_name_source"] = "probe"
    elif jsonld and jsonld.get("name"):
        snap["product_name"] = str(jsonld["name"]).strip()[:140]
        snap["product_name_source"] = "jsonld"

    candidates = _candidates_from_result(result)
    winner, rejected = _select_price(candidates)
    if winner is not None:
        _apply_price(snap, winner)
        snap["price_evidence"] = {
            "plane": winner.get("plane"),
            "raw": winner.get("raw"),
            "currency_raw": winner.get("currency"),
            "path": winner.get("path") or "",
            "value": winner.get("value"),
            "snippet": winner.get("snippet"),
            "name_match": "unknown",  # filled once the name check below runs
        }
        snap["price_candidates"] = [winner, *rejected][:4]
        # Regex-plane rows may still get one bounded LLM pick (opt-in only).
        if snap["price_plane"] == "regex" and snap["confidence"] == "low":
            pick = _jev_price_pick(snap.get("title") or "", snap.get("url") or "", result.get("prices") or [])
            if pick is not None:
                snap["price_norm"] = pick["amount"]
                snap["price_raw"] = pick["raw"]
                snap["confidence"] = "medium"
                snap["price_plane"] = "jev_regex"
                snap["price_evidence"].update(
                    {
                        "plane": "jev_regex",
                        "raw": pick["raw"],
                        "value": pick["amount"],
                        "path": "jev_regex",
                        # The JPick may name its own currency; do not inherit the
                        # rejected regex candidate's.
                        "currency_raw": _currency_from_text(pick["raw"]),
                    }
                )
                snap["jev_price"] = pick
            elif _JEV_PRICE_LAST:
                # Honest refusal stays visible for the reviewer.
                snap["jev_price_note"] = _JEV_PRICE_LAST

    # Fill missing ISO currency from a higher plane even if price came from elsewhere.
    if not snap["currency"]:
        snap["currency"] = _iso_currency((jsonld or {}).get("currency")) or _iso_currency(
            (meta or {}).get("currency")
        )

    # AggregateOffer `lowPrice` ("from $X") is a range floor, not a product
    # price: keep it as evidence only, never as price_norm / high confidence.
    raw_from = (jsonld or {}).get("from_price")
    if raw_from not in (None, "", 0, "0", "0.00"):
        from_amt = _parse_amount(str(raw_from))
        if from_amt:
            snap["from_price"] = round(from_amt, 2)

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

    # Product-name cross-check: does the page we priced actually sell that product?
    match = name_match_of(snap.get("product_name"), title, final_url)
    snap["name_match"] = match["name_match"]
    snap["name_match_score"] = match["score"]
    snap["name_match_detail"] = {k: v for k, v in match.items() if k != "name_match"}
    if snap["price_evidence"]:
        snap["price_evidence"]["name_match"] = snap["name_match"]
    if snap["name_match"] == "mismatch" and snap["confidence"] == "high":
        # Never let a wrong-product row read as high confidence; medium keeps it
        # visible in reports while the gates stop counting it as trusted (W2).
        snap["confidence"] = "medium"
        snap["confidence_capped"] = "name_mismatch"

    # Per-field status: `verified` needs a structured plane, an explicit ISO
    # currency and a name that is at least partially matched. Everything else is
    # honestly `review` — never silently promoted.
    if snap["price_norm"] is None:
        snap["price_status"] = "unknown"
    elif (
        snap["success"]  # an identity-failed page (redirect to home) cannot verify a price
        and snap["price_plane"] in STRUCTURED_PLANES
        and _iso_currency(snap.get("currency"))
        and snap["name_match"] in ("match", "partial")
    ):
        snap["price_status"] = "verified"
    else:
        snap["price_status"] = "review"
    snap["stock_status"] = "verified" if snap.get("stock") not in (None, "unknown") else "unknown"
    snap["stock_evidence"] = {
        "state": snap.get("stock"),
        "source": "jsonld"
        if (jsonld or {}).get("availability")
        else ("dom" if snap.get("stock") not in (None, "unknown") else None),
        "hits": (result.get("stock_hits") or [])[:4],
    }

    # Needs review: success claimed but only weak evidence, or no price at all.
    if snap["success"] and snap["confidence"] == "low":
        snap["needs_review"] = True
        snap["needs_review_reason"] = "price from regex only (low confidence)"
    if snap["success"] and snap["price_norm"] is None:
        snap["needs_review"] = True
        snap["needs_review_reason"] = "success but no normalized price"
    if snap["success"] and snap["price_norm"] is None and snap.get("from_price") is not None:
        # "from $X" is a range floor: the row is deliberately unpriced and can
        # never be alerted on as a product price.
        snap["needs_review_reason"] = (
            "aggregate 'from' price only (AggregateOffer.lowPrice) — not a product price"
        )
    if snap["success"] and not _iso_currency(snap.get("currency")):
        snap["needs_review"] = True
        snap["needs_review_reason"] = snap.get("needs_review_reason") or "currency not explicit"
    if snap["success"] and snap["stock"] == "unknown":
        # A reason without the flag is a silent hole: the row reads as clean while
        # nothing actually confirmed stock.
        snap["needs_review"] = True
        snap["needs_review_reason"] = snap.get("needs_review_reason") or "no stock signal"
    if snap["success"] and snap["name_match"] == "unknown":
        snap["needs_review"] = True
        snap["needs_review_reason"] = snap.get("needs_review_reason") or (
            "product name not captured — identity unconfirmed"
        )
    if snap["success"] and snap["name_match"] == "mismatch":
        snap["needs_review"] = True
        snap["needs_review_reason"] = snap.get("needs_review_reason") or (
            "product name does not match page title/slug — wrong product risk"
        )
    if snap["success"] and snap["price_status"] == "review":
        # One invariant: a priced row that is not `verified` is a review row.
        snap["needs_review"] = True
        snap["needs_review_reason"] = snap.get("needs_review_reason") or (
            "price not verified: plane/currency/name unconfirmed"
        )
    if not snap["success"]:
        snap["needs_review"] = True
        snap["needs_review_reason"] = snap.get("needs_review_reason") or snap["fail_reason"] or "scan failed"

    snap["evidence"] = {
        "price": {
            "plane": snap.get("price_plane"),
            "raw": (snap.get("price_evidence") or {}).get("raw"),
            "currency": _iso_currency(snap.get("currency")) or (snap.get("price_evidence") or {}).get("currency_raw"),
            "path": (snap.get("price_evidence") or {}).get("path"),
            "name_match": snap.get("name_match"),
            "status": snap.get("price_status"),
        },
        "capture": snap.get("capture") or {},
    }
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


# Bumped when the snapshot row shape changes: 1 = pre-W1 (no evidence),
# 2 = W1+ (price_plane / price_evidence / name_match / capture / statuses).
SNAPSHOT_SCHEMA_VERSION = 2


def write_snapshot(path: Path, products: list[dict]) -> dict:
    snap = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "n": len(products),
        "products": products,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return snap


def _mark_review(row: dict, reason: str) -> None:
    """Sticky review flag: the first reason wins, but the flag always sticks."""
    row["needs_review"] = True
    row["needs_review_reason"] = row.get("needs_review_reason") or reason


def _row_claim_ok(row: dict) -> bool:
    """A claim is auditable only with a stored plane/value and a capture block."""
    evidence = row.get("price_evidence") or {}
    capture = row.get("capture") or {}
    return bool(
        evidence.get("plane")
        and evidence.get("raw") is not None
        and capture.get("captured_at")
        and capture.get("html_sha256")
    )


def _claim_issues(prev_row: dict, curr_row: dict, *, price: bool = True, stock: bool = True) -> list[str]:
    """Why a two-sided claim is not `high`. Empty list = fully evidenced."""
    issues: list[str] = []
    for label, row in (("previous", prev_row), ("current", curr_row)):
        if not _row_claim_ok(row):
            issues.append(f"{label} row has no price evidence/capture")
        if price and row.get("price_status") != "verified":
            issues.append(f"{label} price {row.get('price_status') or 'unknown'}")
        if stock and row.get("stock_status") != "verified":
            issues.append(f"{label} stock {row.get('stock_status') or 'unknown'}")
        if row.get("name_match") not in ("match", "partial"):
            issues.append(f"{label} name {row.get('name_match') or 'unknown'}")
    return issues


def _with_evidence(change: dict, prev_row: dict, curr_row: dict) -> dict:
    """Carry both sides' evidence into the change so it is auditable standalone."""
    change["from_evidence"] = prev_row.get("price_evidence") or {}
    change["to_evidence"] = curr_row.get("price_evidence") or {}
    change["from_capture"] = prev_row.get("capture") or {}
    change["to_capture"] = curr_row.get("capture") or {}
    change["from_status"] = {
        "price": prev_row.get("price_status"),
        "stock": prev_row.get("stock_status"),
        "name": prev_row.get("name_match"),
    }
    change["to_status"] = {
        "price": curr_row.get("price_status"),
        "stock": curr_row.get("stock_status"),
        "name": curr_row.get("name_match"),
    }
    return change


def diff_snapshots(prev: dict, curr: dict) -> list[dict]:
    prev_map = {p["identity"]: p for p in prev.get("products") or []}
    changes: list[dict] = []
    for c in curr.get("products") or []:
        p = prev_map.get(c["identity"])
        entry = {"id": c.get("id"), "identity": c["identity"], "kind": c.get("kind"), "url": c.get("url")}
        if not p:
            # A target appearing for the first time is assortment information,
            # not a price/stock flip: the report's own legend reserves "high"
            # for price/stock/availability changes, and marking new rows high
            # inflated the severity counts (every row is "new" in a baseline run).
            changes.append({**entry, "type": "new_target", "severity": "medium", "from": "", "to": c.get("title", "")})
            continue

        # Currency and variant gate — checked BEFORE the numbers.
        # A localized offer (EUR vs USD from a different IP/locale) or a switched
        # default variant changes price_norm while nothing about the product's
        # price actually moved. Comparing raw numbers there manufactures a
        # high-severity change on rows we currently count as trusted.
        # A missing currency on either side is not comparable either: unlabelled
        # numbers (EUR 27 → 29 with currency=None) must go to needs_review.
        pc, cc = _iso_currency(p.get("currency")), _iso_currency(c.get("currency"))
        currency_shift = bool(pc and cc and pc != cc)
        currency_unverified = not (pc and cc)
        # `!=` covers the None↔value case: a variant picked up on only one side
        # is still another variant's price, never a price change.
        variant_shift = p.get("variant") != c.get("variant")
        if currency_shift:
            changes.append(
                {**entry, "type": "currency", "severity": "medium", "from": pc, "to": cc,
                 "note": "currency switched (localized offer) — numbers not comparable, no price alert"}
            )
        if currency_unverified:
            _mark_review(c, "currency not comparable between scans")
            changes.append(
                {**entry, "type": "currency_unverified", "severity": "medium",
                 "from": pc or "n/a", "to": cc or "n/a",
                 "note": "currency missing on one snapshot — numbers not comparable, no price alert"}
            )
        if variant_shift:
            changes.append(
                {**entry, "type": "variant", "severity": "medium",
                 "from": str(p.get("variant")), "to": str(c.get("variant")),
                 "note": "default variant changed — another variant's price is not a price change"}
            )

        # price change (only when the numbers are comparable)
        comparable = not currency_shift and not currency_unverified and not variant_shift
        pn, cn = p.get("price_norm"), c.get("price_norm")
        if comparable and pn is not None and cn is not None and abs(pn - cn) > 0.005:
            low_conf = p.get("confidence") == "low" or c.get("confidence") == "low"
            issues = _claim_issues(p, c)
            if low_conf:
                issues.insert(0, "low-confidence price")
            changes.append(
                _with_evidence(
                    {
                        **entry,
                        "type": "price",
                        "severity": "medium" if issues else "high",
                        "from": f"{p.get('price_raw')}",
                        "to": f"{c.get('price_raw')}",
                        **(
                            {"note": "severity capped — " + "; ".join(issues[:3]) + "; verify manually"}
                            if issues
                            else {}
                        ),
                    },
                    p,
                    c,
                )
            )
        elif comparable and (pn is None) != (cn is None):
            if cn is None and c.get("from_price") is not None:
                # The row lost its structured price but still reports an
                # aggregate "from $X": that is not a sell-out or a lost price.
                _mark_review(c, "aggregate 'from' price only — not a product price")
                changes.append(
                    _with_evidence(
                        {
                            **entry,
                            "type": "price_unverified",
                            "severity": "medium",
                            "from": p.get("price_raw") or "none",
                            "to": f"from {c.get('from_price')} (aggregate range, not a product price)",
                            "note": "AggregateOffer.lowPrice is not the product price — no alert",
                        },
                        p,
                        c,
                    )
                )
            else:
                # A vanished price can only be high when the previous side is
                # fully evidenced: the current side has no price to cross-check.
                lost_issues = [
                    issue for issue in _claim_issues(p, c, stock=False) if issue.startswith("previous")
                ]
                appeared_issues = [
                    issue for issue in _claim_issues(p, c) if issue.startswith("current")
                ]
                issues = lost_issues if cn is None else appeared_issues
                changes.append(
                    _with_evidence(
                        {
                            **entry,
                            "type": "price_lost" if cn is None else "price_appeared",
                            "severity": ("high" if cn is None else "medium") if not issues else "medium",
                            "from": p.get("price_raw") or "none",
                            "to": c.get("price_raw") or "none",
                            **(
                                {"note": "severity capped — " + "; ".join(issues[:3])}
                                if issues and cn is None
                                else {}
                            ),
                        },
                        p,
                        c,
                    )
                )
        # stock change. `unknown` is deliberately excluded from the normal
        # comparison, but losing the signal on a row we had as in_stock is an
        # alert (a monitoring hole must not read as "no change").
        ps, cs = p.get("stock") or "unknown", c.get("stock") or "unknown"
        if ps != cs:
            if ps == "in_stock" and cs == "unknown":
                changes.append(
                    _with_evidence(
                        {
                            **entry,
                            "type": "stock_signal_lost",
                            "severity": "medium",
                            "from": ps,
                            "to": cs,
                            "note": "stock signal lost — sell-out not confirmed, verify manually",
                        },
                        p,
                        c,
                    )
                )
            elif ps != "unknown" and cs != "unknown":
                issues = _claim_issues(p, c)
                changes.append(
                    _with_evidence(
                        {
                            **entry,
                            "type": "stock",
                            "severity": "medium" if issues else "high",
                            "from": ps,
                            "to": cs,
                            **(
                                {"note": "severity capped — " + "; ".join(issues[:3])}
                                if issues
                                else {}
                            ),
                        },
                        p,
                        c,
                    )
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

    # A target that disappeared from the current scan is a change too: without
    # this, a delisted/renamed PDP silently vanishes from the report and its
    # price history is lost (and a renamed product looks like a brand-new row).
    current_identities = {c["identity"] for c in curr.get("products") or []}
    for identity, p in prev_map.items():
        if identity in current_identities:
            continue
        changes.append(
            {
                "id": p.get("id"),
                "identity": identity,
                "kind": p.get("kind"),
                "url": p.get("url"),
                "type": "target_lost",
                "severity": "medium",
                "from": str(p.get("price_raw") or p.get("price_norm") or ""),
                "to": "not found in current scan",
            }
        )
    return changes


def _false_verified(products: list[dict]) -> list[dict]:
    """A `verified` row that violates its own invariants is a bug, not a review.

    Wrong plane, implicit currency or a mismatched name must never coexist with
    `price_status == "verified"`; this gate catches a regression in that logic
    instead of trusting the label.
    """
    return [
        p
        for p in products
        if p.get("price_status") == "verified"
        and (
            p.get("price_plane") not in STRUCTURED_PLANES
            or not _iso_currency(p.get("currency"))
            or p.get("name_match") not in ("match", "partial")
            or not p.get("success")
        )
    ]


def _evidence_100(products: list[dict]) -> bool:
    """Every row we present as high/medium confidence carries its evidence."""
    claimed = [p for p in products if p.get("confidence") in ("high", "medium")]
    return all(
        (p.get("price_evidence") or {}).get("raw") is not None
        and (p.get("capture") or {}).get("captured_at")
        and (p.get("capture") or {}).get("html_sha256")
        for p in claimed
    )


def acceptance_summary(products: list[dict]) -> dict:
    """W2 30-PDP gate: 0 false `verified`, ≥27/30 with a verifiable price."""
    priced = [p for p in products if p.get("price_norm") is not None]
    verifiable = [
        p
        for p in priced
        if p.get("price_status") != "unknown" and p.get("name_match") != "mismatch"
    ]
    false_verified = _false_verified(products)
    return {
        "n": len(products),
        "priced": len(priced),
        "verifiable": len(verifiable),
        # Machine-verified is stricter than "verifiable": it also needs the
        # structured plane, the name match and a verified stock signal.
        "price_verified": sum(1 for p in priced if p.get("price_status") == "verified"),
        "false_verified": len(false_verified),
        "false_verified_ids": [p.get("id") for p in false_verified],
        "passes": len(products) >= 30 and not false_verified and len(verifiable) >= 27,
    }


def evaluate_gates(products: list[dict]) -> dict[str, bool]:
    """Quality gates. `trusted_70` is legacy; `trusted_80_strict` is the W2 metric.

    `acceptance_30` is only evaluated on a 30-row set: the gate belongs to the
    pilot acceptance run, not to a 10-URL regression scan.
    """
    n = max(1, len(products))
    success_n = sum(1 for p in products if p.get("success"))
    pdp_n = sum(1 for p in products if "/products/" in (p.get("identity") or "").lower())
    trusted_strict_n = sum(1 for p in products if _is_trusted(p))
    trusted_legacy_n = sum(1 for p in products if _is_trusted_legacy(p))
    leaked_404 = any(
        p.get("success")
        and (
            str(p.get("fail_reason") or "").startswith("http_")
            or p.get("fail_reason") == "title_404"
            or _NOT_FOUND_RE.search(p.get("title") or "")
        )
        for p in products
    )
    mismatch_n = sum(1 for p in products if p.get("name_match") == "mismatch")
    gates = {
        "success_80": success_n / n >= 0.80,
        "identity_100": pdp_n == len(products),
        "trusted_70": trusted_legacy_n / n >= 0.70,
        "trusted_80_strict": trusted_strict_n / n >= 0.80,
        "evidence_100": _evidence_100(products),
        "no_name_mismatch": mismatch_n == 0,
        "no_false_verified": not _false_verified(products),
        "no_404_leak": not leaked_404,
    }
    if len(products) >= 30:
        gates["acceptance_30"] = acceptance_summary(products)["passes"]
    return gates


def _fmt_evidence(ev: dict | None) -> str:
    """One-line, human-auditable description of a stored price plane."""
    if not ev:
        return "no evidence"
    bits: list[str] = []
    if ev.get("plane"):
        bits.append(f"plane `{ev.get('plane')}`")
    if ev.get("path"):
        bits.append(f"path `{ev.get('path')}`")
    if ev.get("raw") is not None:
        bits.append(f"raw `{ev.get('raw')}`")
    if ev.get("currency_raw"):
        bits.append(f"ccy `{ev.get('currency_raw')}`")
    if ev.get("name_match"):
        bits.append(f"name `{ev.get('name_match')}`")
    return " · ".join(bits) or "no evidence"


def _fmt_capture(cap: dict | None) -> str:
    if not cap or not cap.get("captured_at"):
        return "no capture block"
    sha = str(cap.get("html_sha256") or "")
    bits = [f"captured `{str(cap.get('captured_at'))[:19]}Z`" if str(cap.get("captured_at")).endswith("+00:00") else f"captured `{cap.get('captured_at')}`"]
    if sha:
        bits.append(f"sha256 `{sha[:12]}…`")
    if cap.get("screenshot"):
        bits.append(f"screenshot `{cap.get('screenshot')}`")
    elif cap.get("screenshot_skipped"):
        bits.append(f"screenshot skipped ({cap.get('screenshot_skipped')})")
    if cap.get("final_url"):
        bits.append(f"final `{str(cap.get('final_url'))[:90]}`")
    return " · ".join(bits)


# Change types that make a claim about a price/stock value and therefore need
# two-sided evidence. Assortment/health types (new_target, scan_lost, …) do not.
_EVIDENCE_CLAIM_TYPES = frozenset({"price", "price_lost", "price_appeared", "stock"})


def _change_is_evidenced(change: dict) -> bool:
    """Two-sided evidence: both snapshots must carry the plane/value.

    `price_lost` / `price_appeared` are one-sided by nature — the missing side
    has no price to evidence — so only the present side is required.
    """
    to_ev = change.get("to_evidence") or {}
    from_ev = change.get("from_evidence") or {}
    if change.get("type") == "price_lost":
        return from_ev.get("raw") is not None
    if change.get("type") == "price_appeared":
        return to_ev.get("raw") is not None
    return to_ev.get("raw") is not None and from_ev.get("raw") is not None


def _evidence_cell(change: dict) -> str:
    if change.get("type") not in _EVIDENCE_CLAIM_TYPES:
        return "n/a"
    return "ok" if _change_is_evidenced(change) else "UNVERIFIED"


def accuracy_report(products: list[dict], *, snapshot: str = "") -> dict:
    """Per-row reason codes behind the strict metric (FAMILY_PLAN §4 W2).

    The report never prints a bare percentage: it states the definition, the
    denominator and every reason code, so a client can see *why* a row is not
    trusted instead of trusting a headline.
    """
    counts: dict[str, int] = {}
    rows: list[dict] = []
    for p in products:
        codes: list[str] = []
        if not p.get("success"):
            codes.append("scan_failed")
        if p.get("price_norm") is None:
            codes.append("no_price")
        else:
            plane = p.get("price_plane")
            codes.append(
                {"jsonld": "jsonld_ok", "og_meta": "og_ok", "schema": "schema_ok"}.get(plane, "regex_only")
            )
            if not _iso_currency(p.get("currency")):
                codes.append("no_currency")
            if (p.get("price_evidence") or {}).get("raw") is None:
                codes.append("no_evidence")
        name = p.get("name_match")
        if name == "mismatch":
            codes.append("name_mismatch")
        elif name in (None, "unknown"):
            codes.append("name_unknown")
        if p.get("stock") in (None, "unknown"):
            codes.append("stock_unknown")
        elif p.get("stock_status") != "verified":
            codes.append("no_stock")
        for code in codes:
            counts[code] = counts.get(code, 0) + 1
        if _is_trusted(p):
            verdict = "trusted"
        elif not p.get("success"):
            verdict = "failed"
        elif p.get("price_norm") is None:
            verdict = "no_price"
        else:
            verdict = "priced_review"
        rows.append(
            {
                "id": p.get("id"),
                "url": p.get("url"),
                "price": p.get("price_raw"),
                "plane": p.get("price_plane"),
                "name_match": name,
                "price_status": p.get("price_status"),
                "stock_status": p.get("stock_status"),
                "verdict": verdict,
                "reason_codes": codes,
            }
        )
    n = max(1, len(products))
    strict_n = sum(1 for p in products if _is_trusted(p))
    legacy_n = sum(1 for p in products if _is_trusted_legacy(p))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": snapshot,
        "definition": (
            "trusted = success AND confidence in {high,medium} AND price_plane in "
            "{jsonld,og_meta,schema} AND ISO-4217 currency AND name_match in {match,partial} "
            "AND price_status=verified AND stock_status=verified AND price_evidence present"
        ),
        "n": len(products),
        "trusted_strict": strict_n,
        "trusted_legacy": legacy_n,
        "trusted_strict_rate": round(strict_n / n, 3),
        "reason_counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        # The acceptance gate belongs to a 30-row pilot set; a 10-URL regression
        # scan must not be rendered as if it were the pilot gate.
        "acceptance": acceptance_summary(products) if len(products) >= 30 else None,
        "rows": rows,
    }


def render_accuracy_markdown(report: dict) -> str:
    acc = report.get("acceptance") or None
    lines = [
        "# Price Watch — Accuracy Report (strict metric)",
        "",
        f"**Snapshot:** `{report.get('snapshot')}` · **Rows:** {report.get('n')} · "
        f"**Generated:** `{report.get('generated_at')}`",
        "",
        f"**Trusted strict:** {report.get('trusted_strict')}/{report.get('n')} "
        f"({report.get('trusted_strict_rate', 0) * 100:.1f}%) · "
        f"**Trusted legacy:** {report.get('trusted_legacy')}/{report.get('n')} (comparison only)",
        "",
        f"> Definition: {report.get('definition')}",
        "",
    ]
    if acc:
        lines += [
            "## Acceptance set (30-PDP pilot gate)",
            "",
            f"- Rows: {acc.get('n')} · priced: {acc.get('priced')} · verifiable: {acc.get('verifiable')} · "
            f"machine-verified: {acc.get('price_verified', '—')}",
            f"- False `verified`: {acc.get('false_verified')}"
            + (f" ({', '.join(acc.get('false_verified_ids') or [])})" if acc.get("false_verified_ids") else ""),
            f"- Gate (0 false `verified`, ≥27/30): **{'PASS' if acc.get('passes') else 'FAIL'}**",
            "",
        ]
    if report.get("reason_counts"):
        lines += ["## Reason codes", "", "| Code | Rows |", "|---|---|"]
        lines += [f"| `{code}` | {count} |" for code, count in report["reason_counts"].items()]
        lines.append("")
    lines += [
        "## Rows",
        "",
        "| Store | Price | Plane | Name | Price/Stock status | Verdict | Codes |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report.get("rows") or []:
        lines.append(
            f"| {row.get('id')} | {row.get('price') or '—'} | {row.get('plane') or '—'} | "
            f"{row.get('name_match') or '—'} | {row.get('price_status')}/{row.get('stock_status')} | "
            f"{row.get('verdict')} | {', '.join(row.get('reason_codes') or []) or '—'} |"
        )
    lines += [
        "",
        "_No percentage in this report is a marketing claim: the denominator is the scanned set and "
        "the definition above is the only one quoted._",
        "",
    ]
    return "\n".join(lines)


def evidence_coverage(products: list[dict]) -> dict:
    """W1 acceptance measure: do priced rows actually carry their evidence?"""
    priced = [p for p in products if p.get("price_norm") is not None]
    with_price_ev = [p for p in priced if (p.get("price_evidence") or {}).get("raw") is not None]
    with_capture = [p for p in products if (p.get("capture") or {}).get("captured_at")]
    verified = [p for p in products if p.get("price_status") == "verified"]
    return {
        "rows": len(products),
        "priced": len(priced),
        "price_evidence": len(with_price_ev),
        "capture": len(with_capture),
        "price_verified": len(verified),
        "price_evidence_pct": round(100 * len(with_price_ev) / max(1, len(priced)), 1),
        "capture_pct": round(100 * len(with_capture) / max(1, len(products)), 1),
    }


def render_markdown(prev: dict, curr: dict, changes: list[dict]) -> str:
    prev_ts = (prev.get("scanned_at") or "")[:16].replace("T", " ")
    curr_ts = (curr.get("scanned_at") or "")[:16].replace("T", " ")
    products = curr.get("products") or []
    changed_ids = {c.get("id") for c in changes}
    unchanged = [p for p in products if p.get("id") not in changed_ids]
    needs_review = [p for p in products if p.get("needs_review")]
    claims = [c for c in changes if c.get("type") in _EVIDENCE_CLAIM_TYPES]
    evidenced_n = sum(1 for c in claims if _change_is_evidenced(c))

    lines = [
        "# Competitor Price Watch — Change Report",
        "",
        f"**Scope:** {len(products)} DTC stores · baseline `{prev_ts}` → check `{curr_ts}`",
        f"**Changes:** {len(changes)} · **Evidenced price/stock claims:** {evidenced_n}/{len(claims)} · **Unchanged:** {len(unchanged)} · "
        f"**Needs review:** {len(needs_review)} · **Trusted strict:** "
        f"{sum(1 for p in products if _is_trusted(p))}/{len(products)} · **Trusted legacy:** "
        f"{sum(1 for p in products if _is_trusted_legacy(p))}/{len(products)}",
        "",
        "> Extraction: JSON-LD > og:price > schema table > content outline > regex. Confidence:",
        "> high = JSON-LD/og with ISO currency, medium = schema with currency, low = regex only.",
        "> Every `high`/`medium` change must carry the plane, the raw value, the product-name match",
        "> and a capture block; a change without them is printed as `UNVERIFIED` and must not be",
        "> delivered as a claim.",
        "",
    ]

    lines.append("## Changes")
    if changes:
        lines.append("")
        lines.append("| # | Store | Type | From | To | Severity | Evidence |")
        lines.append("|---|-------|------|------|----|----------|----------|")
        for i, c in enumerate(changes, 1):
            note = f" _{c.get('note')}_" if c.get("note") else ""
            lines.append(
                f"| {i} | {c.get('id')} | {c.get('type')} | {str(c.get('from'))[:60]} | "
                f"{str(c.get('to'))[:60]} | {c.get('severity')}{note} | "
                f"{_evidence_cell(c)} |"
            )
    else:
        lines.append("")
        lines.append("_No price/stock/promo changes detected between the two scans._")
    lines.append("")

    if changes:
        lines.append("## Evidence")
        lines.append("")
        for c in claims:
            lines.append(f"### {c.get('id')} — {c.get('type')} ({c.get('severity')})")
            if not _change_is_evidenced(c):
                lines.append("")
                lines.append(
                    "**UNVERIFIED** — no plane/value/capture stored for this change. Treat it as a "
                    "lead for manual review, never as a delivered claim."
                )
            else:
                lines.append("")
                lines.append(f"- from: {_fmt_evidence(c.get('from_evidence'))}")
                lines.append(f"- to: {_fmt_evidence(c.get('to_evidence'))}")
                capture = c.get("to_capture") or c.get("from_capture") or {}
                lines.append(f"- capture: {_fmt_capture(capture)}")
            lines.append("")

    lines.append("## Needs review")
    if needs_review:
        lines.append("")
        lines.append(
            "_Rows the extractor could not confirm on its own (no structured plane, no explicit "
            "currency, no stock signal, or an unconfirmed product name). They are queued for a "
            "manual check; they are not delivered as claims._"
        )
        lines.append("")
        lines.append("| Store | Reason | Confidence | Evidence |")
        lines.append("|-------|--------|------------|----------|")
        for p in needs_review:
            ev = p.get("price_evidence") or {}
            ev_text = f"{ev.get('plane')}/{ev.get('path')}" if ev.get("plane") else "—"
            lines.append(
                f"| {p.get('id')} | {p.get('needs_review_reason') or '—'} | "
                f"{p.get('confidence')} | {ev_text} |"
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
            ev = p.get("price_evidence") or {}
            plane = f" · plane={ev.get('plane')}" if ev.get("plane") else ""
            lines.append(
                f"- {p.get('id')}: {price} · stock={p.get('stock')} · conf={p.get('confidence')}"
                f"{plane} · name={p.get('name_match')}"
            )
    else:
        lines.append("")
        lines.append("_None._")
    lines.append("")

    lines.append("## Scan summary")
    lines.append("")
    lines.append("| Store | Kind | Page class | Price | Stock | Confidence | Plane | Status (price/stock) |")
    lines.append("|-------|------|-----------|-------|-------|------------|-------|----------------------|")
    for p in products:
        lines.append(
            f"| {p.get('id')} | {p.get('kind')} | {p.get('page_class')} | "
            f"{p.get('price_raw') or '—'} | {p.get('stock')} | {p.get('confidence')} | "
            f"{p.get('price_plane') or '—'} | {p.get('price_status')}/{p.get('stock_status')} |"
        )
    lines.append("")

    lines.append("## Method")
    lines.append("")
    lines.append("- Success: live PDP (HTTP <400, title not 404, URL still /products/) with a product price.")
    lines.append("- Trusted strict (W2): success + confidence high/medium + structured plane + ISO-4217 currency + name match + verified price and stock + stored evidence; gate ≥80%.")
    lines.append("- Trusted legacy: success + confidence high/medium + ISO-4217 currency; shown for comparison only, never quoted to a client.")
    lines.append("- Evidence: `price_evidence` = plane + raw value + path + currency + snippet; `capture` = timestamp, clean-HTML sha256, screenshot, final URL.")
    lines.append("- Name match: token overlap between the product name (JSON-LD / og / h1) and page title + slug; ≥0.6 `match`, 0.3–0.6 `partial`, <0.3 `mismatch`, missing `unknown`.")
    lines.append("- Severity: `high` only when both sides are evidenced, price and stock are `verified`, and the name matches; otherwise `medium` with the cap reason.")
    lines.append("- Geo: JSON-LD/og currency follows the store's localized offer (EU IP can yield EUR on US brands). Pin locale per client market.")
    lines.append("- Snapshots: `reports/price_watch/*.json`; screenshots: `reports/price_watch/evidence/<date>/` (git-ignored, 90-day retention).")
    lines.append("")
    return "\n".join(lines)


def load_targets(path: Path) -> list[dict]:
    """Load a targets/acceptance JSON ({targets: [...]} or a bare list).

    Fails closed: https only, non-empty, unique ids — a typo must not silently
    shrink the acceptance set (the gate is meaningless with fewer rows).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("targets") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"no targets in {path}")
    out: list[dict] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SystemExit(f"{path}: target #{i} is not an object")
        url = str(row.get("url") or "").strip()
        target_id = str(row.get("id") or f"target_{i}").strip()
        if not url.startswith("https://"):
            raise SystemExit(f"{path}: {target_id}: url must be https, got {url!r}")
        if target_id in seen:
            raise SystemExit(f"{path}: duplicate target id {target_id!r}")
        seen.add(target_id)
        out.append({"id": target_id, "kind": row.get("kind") or "shopify_pdp", "url": url})
    return out


def cmd_scan(args: argparse.Namespace) -> int:
    targets = load_targets(args.targets) if getattr(args, "targets", None) else list(WATCH_TARGETS)
    if args.limit:
        targets = targets[: args.limit]
    products = scan_targets(targets, limit=0)
    write_snapshot(args.json, products)

    ok_n = sum(1 for p in products if p.get("success"))
    strict_n = sum(1 for p in products if _is_trusted(p))
    legacy_n = sum(1 for p in products if _is_trusted_legacy(p))
    print("\n" + "=" * 72)
    print(
        f" SCAN DONE — {ok_n}/{len(products)} success | trusted strict {strict_n}/{len(products)} "
        f"| trusted legacy {legacy_n}/{len(products)}"
    )
    print(f" Snapshot: {args.json}")
    print("=" * 72)
    for p in products:
        mark = "✓" if p.get("success") else "✗"
        print(
            f"  {mark} {p['id']:<18} price={str(p.get('price_raw'))[:28]:<28} "
            f"stock={p.get('stock'):<12} conf={p.get('confidence'):<6} "
            f"plane={str(p.get('price_plane')):<9} name={str(p.get('name_match')):<8} "
            f"status={p.get('price_status')}/{p.get('stock_status')}"
        )

    gates = evaluate_gates(products)
    print(" Gates: " + " | ".join(f"{k}={v}" for k, v in gates.items()))
    cov = evidence_coverage(products)
    print(
        f" Evidence: price={cov['price_evidence']}/{cov['priced']} priced rows "
        f"({cov['price_evidence_pct']}%) | capture={cov['capture']}/{cov['rows']} "
        f"| price_verified={cov['price_verified']}"
    )
    return 0 if all(gates.values()) else 2


def cmd_accuracy(args: argparse.Namespace) -> int:
    snapshot = load_snapshot(args.snapshot)
    products = snapshot.get("products") or []
    report = accuracy_report(products, snapshot=str(args.snapshot))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = args.out.with_suffix(".md")
    md_path.write_text(render_accuracy_markdown(report), encoding="utf-8")
    acc = report.get("acceptance") or {}
    print(
        f" STRICT TRUSTED — {report['trusted_strict']}/{report['n']} "
        f"(legacy {report['trusted_legacy']}/{report['n']})"
    )
    print(f" Reasons: {report['reason_counts']}")
    if report["n"] >= 30 and acc:
        print(
            f" Acceptance: verifiable {acc['verifiable']}/{acc['n']} | false verified {acc['false_verified']} "
            f"| {'PASS' if acc['passes'] else 'FAIL'}"
        )
    print(f" Wrote {args.out} + {md_path}")
    if report["n"] >= 30 and not acc["passes"]:
        return 2
    return 0 if report["trusted_strict_rate"] >= 0.80 else 2


def _is_self_diff(prev: dict, curr: dict) -> bool:
    """True when the two snapshots are the same scan — that is not evidence.

    Identical (or missing) `scanned_at` means the diff proves nothing about
    change detection, so it must never be rendered as a report.
    """
    a, b = prev.get("scanned_at"), curr.get("scanned_at")
    if not a or not b:
        return True
    return str(a) == str(b)


# Gates that must hold before a diff may be rendered. success_80 / identity_100
# stay scan-level warnings; a report is only as good as its evidence, so the
# strict metric, evidence coverage and name agreement are hard requirements.
# `trusted_70` is intentionally absent: it is the legacy definition.
_DIFF_REQUIRED_GATES = ("trusted_80_strict", "evidence_100", "no_name_mismatch", "no_404_leak")


def _gate_failures(snapshots: list[tuple[str, dict]]) -> list[str]:
    failed: list[str] = []
    for label, snap in snapshots:
        gates = evaluate_gates(snap.get("products") or [])
        failed.extend(
            f"{label}:{name}" for name in _DIFF_REQUIRED_GATES if not gates.get(name)
        )
    return failed


def cmd_diff(args: argparse.Namespace) -> int:
    prev = load_snapshot(args.previous)
    curr = load_snapshot(args.current)
    if _is_self_diff(prev, curr):
        print(
            " REFUSED: baseline and check are the same scan "
            f"(scanned_at={prev.get('scanned_at') or 'missing'}) — a self-diff proves no change detection.",
            file=sys.stderr,
        )
        print(" No report written. Rerun `scan` for the check snapshot first.", file=sys.stderr)
        return 2
    failed = _gate_failures([("baseline", prev), ("check", curr)])
    if failed:
        print(
            " REFUSED: quality gates failed for " + ", ".join(failed) + " — report not written.",
            file=sys.stderr,
        )
        print(
            " A snapshot the pipeline already rejected cannot back a customer report; "
            "fix extraction and rescan.",
            file=sys.stderr,
        )
        return 2
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
    s.add_argument("--limit", type=int, default=0, help="Limit targets (0=all)")
    s.add_argument(
        "--targets",
        type=Path,
        default=None,
        help="JSON target list ({targets:[{id,kind,url}]} or a list) — e.g. acceptance-30.json",
    )
    s.set_defaults(func=cmd_scan)

    a = sub.add_parser("accuracy", help="Strict metric + reason codes for a snapshot")
    a.add_argument("--snapshot", type=Path, required=True)
    a.add_argument("--out", type=Path, default=ROOT / "reports" / "price_watch" / "accuracy.json")
    a.set_defaults(func=cmd_accuracy)

    d = sub.add_parser("diff", help="Diff two snapshots → markdown report")
    d.add_argument("--previous", type=Path, required=True)
    d.add_argument("--current", type=Path, required=True)
    d.add_argument("--out", type=Path, default=ROOT / "reports" / "price_watch" / "sample-report.md")
    d.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
