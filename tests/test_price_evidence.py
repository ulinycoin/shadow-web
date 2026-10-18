"""W1 — evidence pack: plane provenance, capture block, name match, statuses.

These tests are the acceptance contract from docs/FAMILY_PLAN.md §4 W1:
every priced row records where the price came from, a change without evidence
can never be `high`, and a product-name mismatch is never high confidence.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

import price_change_report as pcr  # noqa: E402
from price_change_report import (  # noqa: E402
    diff_snapshots,
    evidence_coverage,
    name_match_of,
    normalize_result,
    render_markdown,
)

FIXTURES = ROOT / "tests" / "fixtures" / "price_watch"


@pytest.fixture(autouse=True)
def _isolate_jev(monkeypatch):
    monkeypatch.setattr(pcr, "_JEV_PRICE_JUDGE", None, raising=False)
    monkeypatch.setattr(pcr, "_JEV_PRICE_LAST", None, raising=False)


CAPTURE = {
    "captured_at": "2026-10-18T09:00:00+00:00",
    "html_sha256": "f" * 64,
    "screenshot": "reports/price_watch/evidence/2026-10-18/x.png",
    "final_url": "https://shop.example/products/a",
    "http_status": 200,
    "page_class": "Closed Shadow",
}


def _probe(**overrides):
    base = {
        "id": "p1",
        "kind": "shopify_pdp",
        "url": "https://shop.example/products/classic-percale-sheet-set",
        "requested_url": "https://shop.example/products/classic-percale-sheet-set",
        "ok": True,
        "success": True,
        "fail_reason": "",
        "http_status": 200,
        "page_class": "Closed Shadow",
        "title": "Classic Percale Sheet Set | Shop",
        "product_name": {"name": "Classic Percale Sheet Set", "source": "jsonld"},
        "jsonld_sample": None,
        "meta_offer": None,
        "schema_sample": None,
        "prices": [],
        "stock_hits": ["Add to cart"],
        "planes": [],
        "capture": dict(CAPTURE),
    }
    base.update(overrides)
    return base


def _candidate(plane, raw, value=None, currency=None, path="Product.offers.price", **kw):
    return {
        "plane": plane,
        "raw": raw,
        "value": value if value is not None else float(raw.strip("$€£ ").replace(",", ".")),
        "currency": currency,
        "path": path,
        "snippet": None,
        **kw,
    }


# ---------------------------------------------------------------------------
# Plane provenance
# ---------------------------------------------------------------------------

def test_jsonld_wins_and_rejected_candidates_keep_a_reason():
    snap = normalize_result(
        _probe(
            price_candidates=[
                _candidate("regex", "$189.00", 189.0, path="content_outline#4"),
                _candidate("schema", "135.15", 135.15, path="table:Price"),
                _candidate("og_meta", "135.15", 135.15, currency="USD", path="og:price:amount"),
                _candidate("jsonld", "135.15", 135.15, currency="USD"),
            ]
        )
    )
    assert snap["price_plane"] == "jsonld"
    assert snap["price_norm"] == 135.15
    assert snap["price_evidence"]["path"] == "Product.offers.price"
    assert snap["price_evidence"]["name_match"] == "match"
    winner = snap["price_candidates"][0]
    assert winner["plane"] == "jsonld"
    rejected = snap["price_candidates"][1:]
    assert rejected and all(c.get("reason") for c in rejected)
    assert {c["plane"] for c in rejected} == {"og_meta", "schema", "regex"}
    assert len(snap["price_candidates"]) <= 4, "winner + top 3 rejected"


def test_og_plane_is_used_when_jsonld_has_no_price():
    snap = normalize_result(
        _probe(
            price_candidates=[
                _candidate("og_meta", "135.15", 135.15, "USD", path="product:price:amount")
            ]
        )
    )
    assert snap["price_plane"] == "og_meta"
    assert snap["confidence"] == "high"
    assert snap["price_status"] == "verified"
    assert snap["price_evidence"]["path"] == "product:price:amount"


def test_schema_plane_without_currency_is_review_not_verified():
    snap = normalize_result(
        _probe(price_candidates=[_candidate("schema", "135.15", 135.15, path="table:Price")])
    )
    assert snap["price_plane"] == "schema"
    assert snap["confidence"] == "low"
    assert snap["price_status"] == "review"
    assert snap["needs_review"] is True


def test_regex_plane_records_path_and_stays_low():
    snap = normalize_result(
        _probe(
            price_candidates=[
                _candidate("regex", "$14", 14.0, path="content_outline#1"),
                _candidate("regex", "$120.00", 120.0, path="content_outline#3"),
            ]
        )
    )
    assert snap["price_plane"] == "regex"
    assert snap["price_norm"] == 120.0
    assert snap["confidence"] == "low"
    assert snap["price_evidence"]["path"].startswith(("content_outline#", "html_regex#", "regex#"))


def test_regex_noise_filter_is_applied_before_median():
    snap = normalize_result(
        _probe(price_candidates=[_candidate("regex", "$100.00", 100.0, path="html_regex#1")])
    )
    assert snap["price_norm"] is None
    assert snap["price_plane"] is None


def test_schema_table_fixture_becomes_a_schema_candidate():
    from shadow_web.schema_snap import export_table_json, parse_page

    from price_monitor_probe import _price_candidates

    html = (FIXTURES / "schema_table.html").read_text(encoding="utf-8")
    parsed = parse_page(html, max_rows=10)
    records = export_table_json(html, table_index=0, max_rows=10)
    assert records, "fixture must expose at least one record"
    cands = _price_candidates(
        jsonld=[],
        meta_offer=None,
        records=records,
        outline_prices=[],
        html_prices=[],
        outline="",
        clean_html=html,
    )
    assert cands, f"no candidate from parsed table {parsed.get('tables')[:1]}"
    assert cands[0]["plane"] == "schema"
    assert cands[0]["path"].startswith("table:")
    assert cands[0]["value"] == 135.15


def test_jsonld_fixture_matches_expected_shape():
    node = json.loads((FIXTURES / "jsonld_product.json").read_text(encoding="utf-8"))
    assert node["offers"]["price"] == "135.15"
    assert node["offers"]["priceCurrency"] == "USD"


# ---------------------------------------------------------------------------
# Product identity
# ---------------------------------------------------------------------------

def test_name_match_cases():
    assert name_match_of("Classic Percale Sheet Set", "Classic Percale Sheet Set | Brooklinen",
                         "https://x.com/products/classic-percale-sheet-set")["name_match"] == "match"
    partial = name_match_of(
        "The Jodhpur Boot - ReVelvet Black",
        "The Jodhpur Boot | Rothy's",
        "https://www.rothys.com/products/the-jodhpur-boot",
    )
    assert partial["name_match"] == "partial"
    mismatch = name_match_of(
        "Wool Runner", "Classic Percale Sheet Set", "https://x.com/products/classic-core-sheet-set"
    )
    assert mismatch["name_match"] == "mismatch"
    assert name_match_of(None, "Anything", "https://x.com/products/a")["name_match"] == "unknown"


def test_slug_alone_can_confirm_a_name():
    result = name_match_of("Classic Percale Sheet Set", "", "https://x.com/products/classic-percale-sheet-set")
    assert result["name_match"] == "match"


def test_name_mismatch_caps_high_confidence_and_flags_review():
    snap = normalize_result(
        _probe(
            title="Wool Runner | Shop",
            url="https://shop.example/products/wool-runner",
            jsonld_sample={"price": "135.15", "currency": "USD", "name": "Classic Percale Sheet Set"},
            product_name={"name": "Classic Percale Sheet Set", "source": "jsonld"},
            stock_hits=["Add to cart"],
        )
    )
    assert snap["name_match"] == "mismatch"
    assert snap["confidence"] == "medium", "a wrong-product row must never read as high"
    assert snap["confidence_capped"] == "name_mismatch"
    assert snap["needs_review"] is True
    assert snap["price_status"] == "review"


def test_missing_product_name_is_unknown_and_reviewed():
    snap = normalize_result(
        _probe(
            product_name={"name": None, "source": "unknown"},
            jsonld_sample={"price": "135.15", "currency": "USD"},
        )
    )
    assert snap["name_match"] == "unknown"
    assert snap["price_status"] == "review"
    assert snap["needs_review"] is True


# ---------------------------------------------------------------------------
# Statuses and the high-severity gate
# ---------------------------------------------------------------------------

def test_verified_price_needs_structured_plane_currency_and_name():
    snap = normalize_result(
        _probe(jsonld_sample={"price": "135.15", "currency": "USD"})
    )
    assert snap["price_status"] == "verified"
    assert snap["stock_status"] == "verified"
    assert snap["needs_review"] is False


def _row(price=50.0, **kw):
    row = {
        "id": "a", "identity": "https://x.com/products/a", "kind": "shopify_pdp",
        "url": "https://x.com/products/a", "success": True, "confidence": "high",
        "price_norm": price, "price_raw": f"USD {price}", "currency": "USD",
        "stock": "in_stock", "promo": False, "variant": None,
        "product_name": "Test Product", "name_match": "match",
        "price_plane": "jsonld", "price_status": "verified", "stock_status": "verified",
        "price_evidence": {"plane": "jsonld", "raw": str(price), "path": "Product.offers.price", "name_match": "match"},
        "capture": dict(CAPTURE),
    }
    row.update(kw)
    return row


def test_evidenced_two_sided_price_move_is_high():
    changes = diff_snapshots({"products": [_row(50.0)]}, {"products": [_row(45.0)]})
    assert [c["type"] for c in changes] == ["price"]
    assert changes[0]["severity"] == "high"
    assert changes[0]["to_status"]["price"] == "verified"
    assert changes[0]["to_capture"]["html_sha256"] == "f" * 64


def test_change_without_capture_is_capped_to_medium():
    curr = _row(45.0)
    curr["capture"] = {}
    changes = diff_snapshots({"products": [_row(50.0)]}, {"products": [curr]})
    price = next(c for c in changes if c["type"] == "price")
    assert price["severity"] == "medium"
    assert "no price evidence/capture" in price["note"]


def test_change_with_review_price_is_capped_to_medium():
    prev = _row(50.0)
    curr = _row(45.0, price_status="review", price_plane="regex")
    changes = diff_snapshots({"products": [prev]}, {"products": [curr]})
    price = next(c for c in changes if c["type"] == "price")
    assert price["severity"] == "medium"
    assert "price review" in price["note"]


def test_stock_flip_is_high_only_with_both_fields_verified():
    prev = _row(stock="in_stock")
    curr = _row(stock="out_of_stock")
    high = next(c for c in diff_snapshots({"products": [prev]}, {"products": [curr]}) if c["type"] == "stock")
    assert high["severity"] == "high"

    curr_unknown_price = _row(stock="out_of_stock", price_status="review")
    capped = next(
        c
        for c in diff_snapshots({"products": [prev]}, {"products": [curr_unknown_price]})
        if c["type"] == "stock"
    )
    assert capped["severity"] == "medium"
    assert "price review" in capped["note"]


def test_evidence_coverage_counts_priced_rows():
    cov = evidence_coverage(
        [
            normalize_result(_probe(jsonld_sample={"price": "135.15", "currency": "USD"})),
            normalize_result(_probe(id="np", product_name={"name": "X", "source": "h1"},
                                   jsonld_sample=None, prices=[], stock_hits=["In stock"])),
        ]
    )
    assert cov["rows"] == 2
    assert cov["priced"] == 1
    assert cov["price_evidence"] == 1
    assert cov["capture"] == 2
    assert cov["price_evidence_pct"] == 100.0


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def test_render_prints_evidence_and_marks_unverified_changes():
    b = dict(id="b", identity="https://x.com/products/b", url="https://x.com/products/b")
    prev = {
        "scanned_at": "2026-10-18T08:00:00+00:00",
        "products": [_row(50.0), _row(30.0, **b)],
    }
    evidenced = _row(45.0)
    no_evidence = _row(25.0, **b)
    no_evidence["price_evidence"] = {}
    no_evidence["capture"] = {}
    curr = {"scanned_at": "2026-10-18T09:00:00+00:00", "products": [evidenced, no_evidence]}

    changes = diff_snapshots(prev, curr)
    assert sorted(c["type"] for c in changes) == ["price", "price"]
    md = render_markdown(prev, curr, changes)
    assert "## Evidence" in md
    assert "Product.offers.price" in md
    assert "sha256 `ffffffffffff…`" in md
    assert "UNVERIFIED" in md
    assert "**Evidenced price/stock claims:** 1/2" in md


def test_render_of_snapshot_fixture_keeps_the_audit_trail():
    snap = json.loads((FIXTURES / "snapshot_with_evidence.json").read_text(encoding="utf-8"))
    row = snap["products"][0]
    assert row["price_evidence"]["plane"] == "jsonld"
    assert row["capture"]["html_sha256"]
    prev = {"scanned_at": "2026-10-17T09:00:00+00:00", "products": [dict(row, price_norm=140.0, price_raw="USD 140.00")]}
    md = render_markdown(prev, snap, diff_snapshots(prev, snap))
    assert "brooklinen_pdp" in md
    assert "raw `135.15`" in md


# ---------------------------------------------------------------------------
# Capture helpers (no browser)
# ---------------------------------------------------------------------------

class _NoShotPage:
    def screenshot(self, **_kw):  # pragma: no cover - must never be reached
        raise AssertionError("screenshot must not be taken when disabled")


class _WritingPage:
    def __init__(self, size):
        self.size = size

    def screenshot(self, path=None, **_kw):
        Path(path).write_bytes(b"x" * self.size)


def test_screenshot_disabled_by_env(monkeypatch):
    from price_monitor_probe import capture_screenshot

    monkeypatch.setenv("SHADOW_WEB_EVIDENCE_SCREENSHOTS", "0")
    assert capture_screenshot(_NoShotPage(), "pdp") == {
        "screenshot": None,
        "screenshot_skipped": "disabled_by_env",
    }


def test_screenshot_over_cap_is_dropped(tmp_path, monkeypatch):
    from price_monitor_probe import MAX_SCREENSHOT_BYTES, capture_screenshot

    monkeypatch.delenv("SHADOW_WEB_EVIDENCE_SCREENSHOTS", raising=False)
    monkeypatch.setenv("SHADOW_WEB_EVIDENCE_DIR", str(tmp_path))
    result = capture_screenshot(_WritingPage(MAX_SCREENSHOT_BYTES + 1), "pdp")
    assert result["screenshot"] is None
    assert result["screenshot_skipped"].startswith("too_large:")
    assert not list(tmp_path.glob("*.png")), "an oversized screenshot must not linger"


def test_screenshot_within_cap_is_recorded(tmp_path, monkeypatch):
    from price_monitor_probe import capture_screenshot

    monkeypatch.delenv("SHADOW_WEB_EVIDENCE_SCREENSHOTS", raising=False)
    monkeypatch.setenv("SHADOW_WEB_EVIDENCE_DIR", str(tmp_path))
    result = capture_screenshot(_WritingPage(1024), "pdp")
    assert result["screenshot"] is not None
    assert Path(result["screenshot"]).exists()
    assert result["screenshot_bytes"] == 1024


def test_snippet_window_is_capped_and_centered():
    from price_monitor_probe import snippet_around

    text = "a" * 500 + " USD 45.00 " + "b" * 500
    out = snippet_around(text, "USD 45.00", 120)
    assert "USD 45.00" in out
    assert len(out) <= 122  # 120 + two ellipses


def test_snippet_chars_env_is_clamped(monkeypatch):
    from price_monitor_probe import _snippet_chars

    monkeypatch.setenv("SHADOW_WEB_EVIDENCE_SNIPPET_CHARS", "5")
    assert _snippet_chars() == 80
    monkeypatch.setenv("SHADOW_WEB_EVIDENCE_SNIPPET_CHARS", "999999")
    assert _snippet_chars() == 2000
    monkeypatch.setenv("SHADOW_WEB_EVIDENCE_SNIPPET_CHARS", "not-a-number")
    assert _snippet_chars() == 400


def test_single_token_name_is_partial_not_match():
    """One token appears in too many places to prove a product identity."""
    result = name_match_of("Toast", "Toast Warm Brown | ColourPop", "https://colourpop.com/products/toast")
    assert result["name_match"] == "partial"
    assert result["single_token"] is True


def test_identity_failure_cannot_verify_a_price():
    """A redirect to the homepage matches its own title but sells nothing."""
    snap = normalize_result(
        _probe(
            url="https://www.koio.co/",
            kind="shopify_pdp",
            jsonld_sample={"price": "135.15", "currency": "USD"},
        )
    )
    assert snap["success"] is False
    assert snap["fail_reason"] == "identity_not_pdp"
    assert snap["price_status"] == "review"


def test_currency_is_derived_from_a_regex_amount():
    from price_monitor_probe import _currency_from_text

    assert _currency_from_text("80cad") == "CAD"
    assert _currency_from_text("CAD 80") == "CAD"
    assert _currency_from_text("$59.99") == "USD"
    assert _currency_from_text("€1.234,56") == "EUR"
    assert _currency_from_text("80 items") is None
    assert _currency_from_text("1500 руб") is None


def test_regex_candidate_keeps_currency_but_stays_low():
    from price_monitor_probe import _price_candidates

    cands = _price_candidates(
        jsonld=[],
        meta_offer=None,
        records=[],
        outline_prices=["80 CAD"],
        html_prices=[],
        outline="80 CAD",
        clean_html="80 CAD",
    )
    assert cands and cands[0]["currency"] == "CAD"
    snap = normalize_result(_probe(price_candidates=cands))
    assert snap["currency"] == "CAD"
    assert snap["confidence"] == "low"
    assert snap["price_status"] == "review"
    assert snap["needs_review"] is True


def test_priced_but_unverified_row_is_always_review():
    snap = normalize_result(
        _probe(price_candidates=[_candidate("regex", "$59.99", 59.99, currency="USD")])
    )
    assert snap["price_status"] == "review"
    assert snap["needs_review"] is True
    # The most specific reason wins ("regex only"), but the row must never be
    # presented as clean just because a price exists.
    assert snap["needs_review_reason"]


def test_multiple_jsonld_products_pick_the_amount_og_corroborates():
    """A PDP with recommendation nodes must not price the first Product blindly."""
    snap = normalize_result(
        _probe(
            price_candidates=[
                _candidate("jsonld", "14.00", 14.0, "USD", path="Product.offers.price"),
                _candidate("jsonld", "120.00", 120.0, "USD", path="Product.offers.price"),
                _candidate("og_meta", "120.00", 120.0, "USD", path="og:price:amount"),
            ]
        )
    )
    assert snap["price_norm"] == 120.0
    assert snap["price_plane"] == "jsonld"
    rejected = {c["value"]: c.get("reason") for c in snap["price_candidates"][1:]}
    assert rejected.get(14.0) == "disagrees_with_cross_plane"
