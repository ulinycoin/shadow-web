"""W2 — strict metric, gates, reason codes, acceptance summary, target loading."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

import price_change_report as pcr  # noqa: E402
from price_change_report import (  # noqa: E402
    acceptance_summary,
    accuracy_report,
    evaluate_gates,
    load_targets,
    render_accuracy_markdown,
)

CAPTURE = {"captured_at": "2026-10-18T09:00:00+00:00", "html_sha256": "a" * 64}


def _row(i: int, **kw) -> dict:
    row = {
        "id": f"p{i}",
        "url": f"https://shop.example/products/p{i}",
        "identity": f"https://shop.example/products/p{i}",
        "kind": "shopify_pdp",
        "success": True,
        "confidence": "high",
        "price_norm": 100.0 + i,
        "price_raw": f"USD {100 + i}",
        "currency": "USD",
        "price_plane": "jsonld",
        "name_match": "match",
        "price_status": "verified",
        "stock": "in_stock",
        "stock_status": "verified",
        "price_evidence": {"plane": "jsonld", "raw": str(100.0 + i), "path": "Product.offers.price"},
        "capture": dict(CAPTURE),
    }
    row.update(kw)
    return row


def _rows():
    """One row per reason code, plus two good planes."""
    return [
        _row(0),  # jsonld_ok → trusted
        _row(1, price_plane="og_meta", price_evidence={"plane": "og_meta", "raw": "101.0"}),
        _row(2, price_plane="schema", price_evidence={"plane": "schema", "raw": "102.0"}),
        _row(3, price_plane="regex", confidence="low", price_status="review"),  # regex_only
        _row(4, price_norm=None, price_raw=None, price_evidence={}, confidence="none"),  # no_price
        _row(5, currency=None, price_status="review"),  # no_currency
        _row(6, price_evidence={}),  # no_evidence
        _row(7, name_match="mismatch", price_status="review", confidence="medium"),  # name_mismatch
        _row(8, name_match="unknown", price_status="review", confidence="medium"),  # name_unknown
        _row(9, stock="unknown", stock_status="unknown"),  # stock_unknown
        _row(10, success=False, fail_reason="no_price", price_norm=None, price_evidence={},
             confidence="none", stock="unknown", stock_status="unknown"),  # scan_failed
    ]


def test_reason_codes_cover_every_documented_case():
    report = accuracy_report(_rows())
    counts = report["reason_counts"]
    for code in (
        "jsonld_ok",
        "og_ok",
        "schema_ok",
        "regex_only",
        "no_price",
        "no_currency",
        "no_evidence",
        "name_mismatch",
        "name_unknown",
        "stock_unknown",
        "scan_failed",
    ):
        assert code in counts, f"missing reason code {code}: {sorted(counts)}"
    assert counts["jsonld_ok"] >= 1


def test_strict_and_legacy_trusted_disagree_on_purpose():
    report = accuracy_report(_rows())
    assert report["trusted_strict"] == 3, "only the three structured rows are trusted"
    assert report["trusted_legacy"] > report["trusted_strict"], (
        "the legacy metric counted rows the strict one rejects — that gap is the point"
    )


def test_accuracy_report_states_its_definition_and_denominator():
    report = accuracy_report(_rows(), snapshot="reports/price_watch/x.json")
    assert "price_status=verified" in report["definition"]
    assert report["n"] == 11
    md = render_accuracy_markdown(report)
    assert "**Trusted strict:** 3/11" in md
    assert "No percentage in this report is a marketing claim" in md


def test_gates_do_not_include_acceptance_for_a_ten_row_set():
    gates = evaluate_gates([_row(i) for i in range(10)])
    assert "acceptance_30" not in gates
    assert gates["trusted_80_strict"] is True
    assert gates["evidence_100"] is True


def test_gates_include_acceptance_for_a_thirty_row_set():
    gates = evaluate_gates([_row(i) for i in range(30)])
    assert "acceptance_30" in gates
    assert gates["acceptance_30"] is True


def test_acceptance_gate_fails_below_27_verifiable():
    rows = [_row(i) for i in range(26)]
    rows += [_row(i, price_norm=None, price_evidence={}) for i in range(26, 30)]
    summary = acceptance_summary(rows)
    assert summary["n"] == 30
    assert summary["verifiable"] == 26
    assert summary["false_verified"] == 0
    assert summary["passes"] is False
    assert evaluate_gates(rows)["acceptance_30"] is False


def test_false_verified_is_detected_even_if_the_label_is_forced():
    rows = [_row(i) for i in range(30)]
    rows[0]["price_plane"] = "regex"  # verified label on a non-structured plane
    summary = acceptance_summary(rows)
    assert summary["false_verified"] == 1
    assert summary["false_verified_ids"] == ["p0"]
    assert summary["passes"] is False
    assert evaluate_gates(rows)["no_false_verified"] is False


def test_name_mismatch_gate_fails_on_a_single_row():
    rows = [_row(i) for i in range(10)]
    rows[3]["name_match"] = "mismatch"
    assert evaluate_gates(rows)["no_name_mismatch"] is False


def test_evidence_gate_fails_when_a_high_row_lacks_a_capture():
    rows = [_row(i) for i in range(10)]
    rows[2]["capture"] = {}
    assert evaluate_gates(rows)["evidence_100"] is False


def test_load_targets_accepts_the_acceptance_file():
    targets = load_targets(ROOT / "reports" / "price_watch" / "acceptance-30.json")
    assert len(targets) == 30
    assert len({t["id"] for t in targets}) == 30
    assert all(t["url"].startswith("https://") for t in targets)
    assert all(t["kind"] for t in targets)


def test_load_targets_fails_closed(tmp_path):
    bad_http = tmp_path / "http.json"
    bad_http.write_text(json.dumps([{"id": "a", "url": "http://x.com/products/a"}]), encoding="utf-8")
    with pytest.raises(SystemExit):
        load_targets(bad_http)

    dupes = tmp_path / "dupes.json"
    dupes.write_text(
        json.dumps([{"id": "a", "url": "https://x.com/products/a"},
                    {"id": "a", "url": "https://x.com/products/b"}]),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        load_targets(dupes)

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"targets": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        load_targets(empty)


def test_cmd_diff_refuses_a_snapshot_without_evidence(tmp_path, capsys):
    """A pre-W1 baseline can no longer back a delivered report."""
    def old_row(i):
        return {
            "id": f"p{i}", "identity": f"https://shop.example/products/p{i}",
            "kind": "shopify_pdp", "url": f"https://shop.example/products/p{i}",
            "success": True, "confidence": "high", "price_norm": 20.0 + i,
            "price_raw": f"USD {20 + i}", "currency": "USD", "stock": "in_stock",
            "promo": False, "variant": None,
        }

    prev = tmp_path / "prev.json"
    curr = tmp_path / "curr.json"
    prev.write_text(json.dumps({"scanned_at": "2026-10-17T09:00:00+00:00",
                                "products": [old_row(i) for i in range(10)]}), encoding="utf-8")
    curr.write_text(json.dumps({"scanned_at": "2026-10-18T09:00:00+00:00",
                                "products": [old_row(i) for i in range(10)]}), encoding="utf-8")
    args = pcr.argparse.Namespace(previous=prev, current=curr, out=tmp_path / "report.md")
    assert pcr.cmd_diff(args) == 2
    err = capsys.readouterr().err
    assert "trusted_80_strict" in err and "evidence_100" in err
    assert not (tmp_path / "report.md").exists()
