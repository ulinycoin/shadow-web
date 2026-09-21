"""Price Watch Phase A — normalize, identity, quality gates (no browser)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

import price_change_report as _pcr  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_jev_price(monkeypatch):
    """Keep the suite env-independent: Jev price gate is off unless a test turns it on."""
    monkeypatch.setattr(_pcr, "_JEV_PRICE_JUDGE", None, raising=False)
    monkeypatch.setattr(_pcr, "_JEV_PRICE_LAST", None, raising=False)

from price_change_report import (  # noqa: E402
    diff_snapshots,
    evaluate_gates,
    identity_of,
    normalize_result,
)
from price_monitor_probe import _loads_jsonld, _offer_price_currency  # noqa: E402


def _probe(**overrides):
    base = {
        "id": "brooklinen_pdp",
        "kind": "shopify_pdp",
        "url": "https://www.brooklinen.com/products/classic-core-sheet-set",
        "requested_url": "https://www.brooklinen.com/products/classic-core-sheet-set",
        "ok": True,
        "success": True,
        "fail_reason": "",
        "http_status": 200,
        "page_class": "Closed Shadow",
        "title": "Classic Percale Sheet Set",
        "jsonld_sample": None,
        "meta_offer": None,
        "schema_sample": None,
        "prices": [],
        "stock_hits": ["Add to cart"],
        "planes": [],
    }
    base.update(overrides)
    return base


def test_identity_strips_query():
    assert identity_of("https://x.com/products/a?variant=1") == "https://x.com/products/a"


def test_jsonld_with_iso_currency_is_high_trusted():
    snap = normalize_result(
        _probe(
            jsonld_sample={"price": "135.15", "currency": "USD", "availability": "https://schema.org/InStock"},
            planes=["jsonld"],
        )
    )
    assert snap["success"] is True
    assert snap["confidence"] == "high"
    assert snap["currency"] == "USD"
    assert snap["price_norm"] == 135.15
    assert snap["needs_review"] is False
    assert snap["stock"] == "in_stock"


def test_og_meta_with_iso_currency_is_high():
    snap = normalize_result(
        _probe(
            meta_offer={"price": "89.00", "currency": "USD", "source": "og"},
            planes=["og_meta"],
        )
    )
    assert snap["confidence"] == "high"
    assert snap["currency"] == "USD"
    assert snap["price_norm"] == 89.0


def test_regex_only_is_low_and_needs_review():
    snap = normalize_result(_probe(prices=["$50", "$120", "$50"], planes=["content_outline"]))
    assert snap["confidence"] == "low"
    assert snap["needs_review"] is True
    assert "regex" in snap["needs_review_reason"]


def test_404_title_cannot_count_as_success():
    snap = normalize_result(
        _probe(
            success=True,
            title="404 Not Found",
            prices=["$20"],
            jsonld_sample={"price": "20", "currency": "USD"},
        )
    )
    assert snap["success"] is False
    assert snap["fail_reason"] == "title_404"
    assert snap["needs_review"] is True


def test_http_404_cannot_count_as_success():
    snap = normalize_result(_probe(success=True, http_status=404, title="Shoes"))
    assert snap["success"] is False
    assert snap["fail_reason"] == "http_404"


def test_pdp_redirected_to_collection_fails_identity():
    snap = normalize_result(
        _probe(
            url="https://www.allbirds.com/collections/mens",
            success=True,
            prices=["$50"],
        )
    )
    assert snap["success"] is False
    assert snap["fail_reason"] == "identity_not_pdp"


def test_gates_reject_404_leak_and_untrusted_majority():
    leaked = {
        "id": "dead",
        "success": True,
        "title": "404 Not Found",
        "identity": "https://x.com/products/dead",
        "confidence": "low",
        "currency": None,
    }
    weak = [
        {
            "id": f"w{i}",
            "success": True,
            "identity": f"https://x.com/products/w{i}",
            "confidence": "low",
            "currency": None,
        }
        for i in range(9)
    ]
    gates = evaluate_gates([leaked, *weak])
    assert gates["no_404_leak"] is False
    assert gates["trusted_70"] is False


def test_gates_pass_on_eight_trusted_pdps():
    rows = []
    for i in range(8):
        rows.append(
            normalize_result(
                _probe(
                    id=f"p{i}",
                    url=f"https://shop.example/products/p{i}",
                    jsonld_sample={"price": "20", "currency": "USD"},
                    planes=["jsonld"],
                )
            )
        )
    rows.append(normalize_result(_probe(id="weak", url="https://shop.example/products/weak", prices=["$12"])))
    rows.append(
        normalize_result(
            _probe(
                id="fail",
                url="https://shop.example/products/fail",
                success=False,
                fail_reason="no_price",
                http_status=200,
            )
        )
    )
    gates = evaluate_gates(rows)
    assert gates == {
        "success_80": True,
        "identity_100": True,
        "trusted_70": True,
        "no_404_leak": True,
    }


def test_diff_flags_price_change():
    prev = {
        "products": [
            {"identity": "https://x.com/products/a", "id": "a", "price_norm": 50.0, "price_raw": "USD 50", "confidence": "high", "stock": "in_stock", "promo": False, "success": True}
        ]
    }
    curr = {
        "products": [
            {"identity": "https://x.com/products/a", "id": "a", "price_norm": 45.0, "price_raw": "USD 45", "confidence": "high", "stock": "in_stock", "promo": False, "success": True}
        ]
    }
    changes = diff_snapshots(prev, curr)
    assert len(changes) == 1
    assert changes[0]["type"] == "price"
    assert changes[0]["severity"] == "high"


def test_jsonld_trailing_comma_and_price_specification():
    data = _loads_jsonld('{"@type": "Product", "offers": {"price": "89.00", "priceCurrency": "USD",},}')
    assert data["offers"]["price"] == "89.00"
    price, currency = _offer_price_currency(
        {"priceSpecification": {"price": "12.00", "priceCurrency": "EUR"}}
    )
    assert price == "12.00"
    assert currency == "EUR"


# ---------------------------------------------------------------------------
# Jev price plane (regex rows only, opt-in via SHADOW_WEB_JEV_PRICE)
# ---------------------------------------------------------------------------

def _judge(payload):
    import price_change_report as pcr
    from shadow_web.agent.jev import JevPriceJudge

    judge = JevPriceJudge(api_key="test", transport=lambda _p: payload)
    pcr._JEV_PRICE_JUDGE = judge
    return judge


def _reset_judge():
    import price_change_report as pcr

    pcr._JEV_PRICE_JUDGE = pcr._MISSING


def _price_payload(choice, *, confidence=0.8, is_offer=0.9, probability=0.8):
    return {
        "answers": {
            "is_offer": {"noul": is_offer},
            "price_amount": {
                "choice": choice,
                "probabilities": {choice: probability},
                "confidence": confidence,
            },
        },
        "usage": {"input_tokens": 120},
    }


def test_jev_price_leaves_jsonld_rows_alone():
    def boom(_payload):
        raise AssertionError("Jev must not be called for a JSON-LD row")

    import price_change_report as pcr
    from shadow_web.agent.jev import JevPriceJudge

    pcr._JEV_PRICE_JUDGE = JevPriceJudge(api_key="test", transport=boom)
    try:
        snap = normalize_result(
            _probe(
                jsonld_sample={"price": "89.00", "currency": "USD", "sku": "S1"},
                prices=["USD 89.00", "USD 12.00"],
            )
        )
    finally:
        _reset_judge()
    assert snap["price_norm"] == 89.0
    assert snap["confidence"] == "high"
    assert snap["needs_review"] is False
    assert "price_plane" not in snap


def test_jev_price_accepts_confident_pick_on_regex_row():
    _judge(_price_payload("USD 45.00", confidence=0.77))
    try:
        snap = normalize_result(
            _probe(title="Tentree Tee", prices=["USD 45.00", "USD 120.00"], jsonld_sample=None)
        )
    finally:
        _reset_judge()
    assert snap["price_norm"] == 45.0
    assert snap["price_raw"] == "USD 45.00"
    assert snap["confidence"] == "medium"
    assert snap["price_plane"] == "jev_regex"
    assert snap["jev_price"]["confidence"] == 0.77
    # currency / needs_review rules are untouched by this feature
    assert snap["currency"] is None
    assert snap["needs_review"] is True


def test_jev_price_keeps_low_row_when_unconfident():
    _judge(_price_payload("USD 45.00", confidence=0.41))
    try:
        snap = normalize_result(
            _probe(title="Tentree Tee", prices=["USD 45.00", "USD 120.00"], jsonld_sample=None)
        )
    finally:
        _reset_judge()
    assert snap["confidence"] == "low"
    assert snap["needs_review"] is True
    assert snap["needs_review_reason"] == "price from regex only (low confidence)"
    assert "jev_price" not in snap
    assert "price_plane" not in snap
    # the refusal stays auditable instead of silently vanishing
    assert snap["jev_price_note"]["confidence"] == 0.41


def test_jev_price_disabled_by_default_is_a_noop():
    # The autouse fixture pins the gate off — no env, no key, no call.
    result = _probe(title="Tentree Tee", prices=["USD 45.00", "USD 120.00"], jsonld_sample=None)
    first = normalize_result(result)
    assert first == normalize_result(result)
    assert first["confidence"] == "low"
    # median-of-two picks the upper candidate — existing regex behaviour, unchanged
    assert first["price_norm"] == 120.0
    assert "jev_price" not in first
    assert "jev_price_note" not in first
    assert "price_plane" not in first


def test_jev_price_refuses_when_noul_disagrees_on_a_real_choice():
    """Two candidates, confident choice (0.9) but is_offer 0.45 → refusal."""
    _judge(_price_payload("USD 45.00", confidence=0.9, is_offer=0.45))
    try:
        snap = normalize_result(
            _probe(title="Tentree Tee", prices=["USD 45.00", "USD 20.00"], jsonld_sample=None)
        )
    finally:
        _reset_judge()
    assert snap["confidence"] == "low"
    assert snap["needs_review"] is True
    assert "jev_price" not in snap
    assert snap["jev_price_note"]["is_offer"] == 0.45
    assert snap["jev_price_note"]["confidence"] == 0.9


def test_jev_price_refuses_single_candidate_without_noul_backing():
    """One candidate: confidence is arithmetically 1.0 — is_offer 0.5 decides."""
    _judge(_price_payload("USD 59.99", confidence=1.0, is_offer=0.5))
    try:
        snap = normalize_result(
            _probe(title="Outdoor Voices Dress", prices=["USD 59.99"], jsonld_sample=None)
        )
    finally:
        _reset_judge()
    assert snap["confidence"] == "low"
    assert "jev_price" not in snap
    assert snap["jev_price_note"]["confidence"] == 1.0
    assert snap["jev_price_note"]["is_offer"] == 0.5


def test_jev_price_accepts_single_candidate_when_noul_confirms():
    _judge(_price_payload("USD 59.99", confidence=1.0, is_offer=0.9))
    try:
        snap = normalize_result(
            _probe(title="Outdoor Voices Dress", prices=["USD 59.99"], jsonld_sample=None)
        )
    finally:
        _reset_judge()
    assert snap["confidence"] == "medium"
    assert snap["price_plane"] == "jev_regex"
    assert snap["price_norm"] == 59.99


def test_price_judgement_gate_matrix():
    from shadow_web.agent.jev import PriceJudgement

    two = lambda conf, offer: PriceJudgement(raw="x", amount=1.0, confidence=conf, is_offer=offer, candidates=2)
    one = lambda conf, offer: PriceJudgement(raw="x", amount=1.0, confidence=conf, is_offer=offer, candidates=1)
    assert two(0.9, 0.9).accepted() is True
    assert two(0.9, 0.45).accepted() is False       # Noul disagrees
    assert two(0.5, 0.9).accepted() is False        # weak choice on a real choice
    assert one(1.0, 0.5).accepted() is False        # degenerate confidence, Noul decides
    assert one(0.2, 0.9).accepted() is True         # n=1 does not need confidence


def test_price_judgement_holds_the_live_boundary_samples_low():
    """Real live samples (tentree PDP, URL present): the gate must not flip.

    Two consecutive calls on the same page gave is_offer 0.56 and 0.61 with
    confidence 0.59 and 0.64 while the picked amount stayed $20. At a 0.6 floor
    this row would alternate between "$20 accepted" and "stay low" every scan.
    """
    from shadow_web.agent.jev import PriceJudgement, OFFER_FLOOR, PRICE_THRESHOLD

    assert OFFER_FLOOR == 0.7 and PRICE_THRESHOLD == 0.7
    for confidence, is_offer in ((0.59, 0.56), (0.64, 0.61)):
        judgement = PriceJudgement(
            raw="$20", amount=20.0, confidence=confidence, is_offer=is_offer, candidates=2
        )
        assert judgement.accepted() is False
        assert judgement.gate()["is_offer_floor"] == 0.7


def _snap_row(**kw):
    row = {
        "id": "x_pdp", "identity": "https://x.com/products/a", "kind": "shopify_pdp",
        "url": "https://x.com/products/a", "success": True, "confidence": "high",
        "price_norm": 50.0, "price_raw": "USD 50", "currency": "USD", "stock": "in_stock",
        "promo": False, "variant": None,
    }
    row.update(kw)
    return row


def test_diff_currency_switch_is_not_a_price_change():
    prev = {"products": [_snap_row(price_norm=27.0, price_raw="EUR 27", currency="EUR")]}
    curr = {"products": [_snap_row(price_norm=29.0, price_raw="USD 29", currency="USD")]}
    changes = diff_snapshots(prev, curr)
    types = [c["type"] for c in changes]
    assert "currency" in types
    assert "price" not in types, "a localized offer must not raise a price alert"
    cur = next(c for c in changes if c["type"] == "currency")
    assert cur["severity"] == "medium" and cur["from"] == "EUR" and cur["to"] == "USD"


def test_diff_variant_switch_is_not_a_price_change():
    prev = {"products": [_snap_row(price_norm=70.95, variant="42048974160041")]}
    curr = {"products": [_snap_row(price_norm=74.0, variant="42048974160099")]}
    changes = diff_snapshots(prev, curr)
    types = [c["type"] for c in changes]
    assert "variant" in types
    assert "price" not in types, "another variant's price is not a price change"


def test_diff_real_price_move_still_alerts():
    prev = {"products": [_snap_row(price_norm=50.0)]}
    curr = {"products": [_snap_row(price_norm=45.0)]}
    changes = diff_snapshots(prev, curr)
    assert [c["type"] for c in changes] == ["price"]
    assert changes[0]["severity"] == "high"


def test_variant_is_captured_from_pdp_query():
    snap = normalize_result(
        _probe(
            id="koio_pdp",
            url="https://koio.com/products/mens-sneaker?variant=42048974160041",
            jsonld_sample=None,
            meta_offer=None,
            prices=["USD 70.95"],
            stock_hits=[],
        )
    )
    assert snap["variant"] == "42048974160041"
    assert snap["identity"] == "https://koio.com/products/mens-sneaker"


def test_unknown_stock_flags_needs_review():
    """A reason without the flag is a silent hole (the Koio row read as clean)."""
    snap = normalize_result(
        _probe(
            id="koio_pdp",
            jsonld_sample={"price": "70.95", "currency": "USD", "sku": "K1"},
            prices=[],
            stock_hits=[],
        )
    )
    assert snap["confidence"] == "high"
    assert snap["stock"] == "unknown"
    assert snap["needs_review"] is True
    assert snap["needs_review_reason"] == "no stock signal"
