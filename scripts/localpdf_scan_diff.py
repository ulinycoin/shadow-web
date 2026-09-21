#!/usr/bin/env python3
"""Diff two LocalPDF competitor scans → actionable digest.

Usage:
  python scripts/localpdf_scan_diff.py
  python scripts/localpdf_scan_diff.py \\
    --current reports/localpdf/latest.json \\
    --previous reports/localpdf/previous.json \\
    --out reports/localpdf/diff-20260718.md

Exit codes:
  0 — ok (may have low-signal only)
  1 — I/O / parse error
  2 — high-signal deltas present (useful for automation alerts)
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CURRENT = ROOT / "reports" / "localpdf" / "latest.json"
DEFAULT_PREVIOUS = ROOT / "reports" / "localpdf" / "previous.json"
FALLBACK_PREVIOUS = ROOT / "examples" / "localpdf" / "latest-scan.json"

_AI_RE = re.compile(r"\b(ai|chat|summar|gpt|assistant)\b", re.I)
# Format converters / utility breadth — not alert-worthy weekly, useful monthly.
_BREADTH_RE = re.compile(
    r"\b(epub|hwp|odp|ods|zip|markdown|mobi|azw|dwg|dxf|csv|rtf)\b.*\bpdf\b"
    r"|\bpdf\b.*\b(epub|hwp|odp|ods|zip|markdown|mobi|azw|dwg|dxf|csv|rtf)\b",
    re.I,
)
_JUNK = (" [page]", " [navigation]", " [footer]", " [header]")


def _norm(label: str) -> str:
    s = label.lower()
    for junk in _JUNK:
        s = s.replace(junk, "")
    return re.sub(r"\s+", " ", s).strip()[:80]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _by_name(report: dict[str, Any]) -> dict[str, dict]:
    return {
        s["name"]: s
        for s in report.get("sites") or []
        if isinstance(s, dict) and s.get("name")
    }


def _label_set(site: dict) -> set[str]:
    return {_norm(x) for x in (site.get("feature_labels") or []) if x and _norm(x)}


def _ai_labels(labels: set[str]) -> set[str]:
    return {x for x in labels if _AI_RE.search(x)}


def diff_sites(prev: dict[str, dict], curr: dict[str, dict]) -> dict[str, Any]:
    names = sorted(set(prev) | set(curr))
    site_diffs: list[dict] = []
    high: list[str] = []
    medium: list[str] = []
    low: list[str] = []

    for name in names:
        p, c = prev.get(name), curr.get(name)
        entry: dict[str, Any] = {"name": name}

        if c and c.get("error"):
            msg = f"{name}: scan error — {c['error'][:160]}"
            high.append(msg)
            entry["error"] = c["error"]
            site_diffs.append(entry)
            continue
        if p and p.get("error") and c and not c.get("error"):
            low.append(f"{name}: recovered from previous error")

        if not p:
            low.append(f"{name}: new target in current scan")
            site_diffs.append(entry)
            continue
        if not c:
            high.append(f"{name}: missing from current scan")
            site_diffs.append(entry)
            continue

        # Messaging
        for field in ("h1", "title", "meta_description"):
            pv, cv = (p.get(field) or "").strip(), (c.get(field) or "").strip()
            if pv != cv:
                entry[f"{field}_changed"] = {"from": pv[:200], "to": cv[:200]}
                if field in ("h1", "title") and name.startswith(("smallpdf", "ilovepdf")):
                    high.append(f"{name}: {field} changed")
                else:
                    low.append(f"{name}: {field} changed")

        # Breadth proxy
        pa, ca = int(p.get("action_count") or 0), int(c.get("action_count") or 0)
        if pa != ca:
            entry["action_count"] = {"from": pa, "to": ca, "delta": ca - pa}
            if abs(ca - pa) >= 20:
                low.append(f"{name}: action_count {pa} → {ca} (Δ{ca - pa})")

        pc = p.get("page_class") or ""
        cc = c.get("page_class") or ""
        if pc != cc:
            entry["page_class"] = {"from": pc, "to": cc}
            if cc in ("Anti-bot", "SparseShell", "Auth-gated"):
                high.append(f"{name}: page_class → {cc}")
            else:
                low.append(f"{name}: page_class {pc} → {cc}")

        # Features
        prev_labels, curr_labels = _label_set(p), _label_set(c)
        added = sorted(curr_labels - prev_labels)
        removed = sorted(prev_labels - curr_labels)
        if added:
            entry["features_added"] = added[:40]
        if removed:
            entry["features_removed"] = removed[:40]

        ai_new = sorted(_ai_labels(set(added)))
        breadth_new = sorted(x for x in added if _BREADTH_RE.search(x))
        if ai_new and name.startswith(("smallpdf", "ilovepdf")):
            entry["ai_features_added"] = ai_new
            high.append(f"{name}: new AI/Chat labels — {', '.join(ai_new[:8])}")
        if breadth_new:
            entry["breadth_features_added"] = breadth_new
            medium.append(
                f"{name}: new format/utility tools — {', '.join(breadth_new[:8])}"
            )
        other_added = [
            x for x in added if x not in ai_new and x not in breadth_new
        ]
        if other_added:
            low.append(f"{name}: +{len(other_added)} feature labels")
        if removed:
            low.append(f"{name}: -{len(removed)} feature labels")

        # Pricing / CTA wording (low unless money words shift hard)
        for key in ("pricing_hits", "cta_hits"):
            ps = {_norm(x) for x in (p.get(key) or [])}
            cs = {_norm(x) for x in (c.get(key) or [])}
            a, r = sorted(cs - ps), sorted(ps - cs)
            if a or r:
                entry[key] = {"added": a[:20], "removed": r[:20]}
                low.append(f"{name}: {key} +{len(a)}/-{len(r)}")

        if len(entry) > 1:
            site_diffs.append(entry)

    # Content actions for LocalPDF compare pages
    actions: list[str] = []
    for h in high:
        if "AI/Chat" in h:
            actions.append(
                "Compare PR (this week): refresh AI/Chat row on vs-smallpdf / vs-ilovepdf — "
                "angle = cloud AI vs local-first (do not copy the feature)."
            )
        if "h1 changed" in h or "title changed" in h:
            actions.append(
                "Compare PR: check positioning copy vs updated competitor H1/title."
            )
        if "page_class" in h or "scan error" in h or "missing from" in h:
            actions.append("Ops: fix scan target / anti-bot before trusting tables.")
    if medium and not any("AI/Chat" in h for h in high):
        actions.append(
            "Monthly (not weekly): bump tool-breadth row on compare pages — "
            "competitors added converters; LocalPDF angle stays focused workspace."
        )

    # Deduplicate actions preserving order
    seen: set[str] = set()
    uniq_actions = []
    for a in actions:
        if a not in seen:
            seen.add(a)
            uniq_actions.append(a)
    if not uniq_actions:
        uniq_actions.append(
            "No content PR this week — high-signal empty; keep scan on weekly schedule."
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "high_signal": high,
        "medium_signal": medium,
        "low_signal": low,
        "sites": site_diffs,
        "content_actions": uniq_actions,
        "has_high_signal": bool(high),
        "has_medium_signal": bool(medium),
    }


def render_md(diff: dict[str, Any], prev_path: Path, curr_path: Path) -> str:
    lines = [
        "# LocalPDF competitor scan — diff",
        "",
        f"- Generated: `{diff['generated_at']}`",
        f"- Previous: `{prev_path}`",
        f"- Current: `{curr_path}`",
        f"- High-signal: **{len(diff['high_signal'])}** · "
        f"Medium: **{len(diff.get('medium_signal') or [])}**",
        "",
    ]
    if diff["high_signal"]:
        lines += ["## High signal", ""]
        for item in diff["high_signal"]:
            lines.append(f"- {item}")
        lines.append("")
    else:
        lines += ["## High signal", "", "_None._", ""]

    medium = diff.get("medium_signal") or []
    if medium:
        lines += ["## Medium signal (monthly content)", ""]
        for item in medium:
            lines.append(f"- {item}")
        lines.append("")

    if diff["content_actions"]:
        lines += ["## Content actions (LocalPDF)", ""]
        for item in diff["content_actions"]:
            lines.append(f"- {item}")
        lines.append("")

    if diff["low_signal"]:
        lines += ["## Low signal", ""]
        for item in diff["low_signal"][:40]:
            lines.append(f"- {item}")
        if len(diff["low_signal"]) > 40:
            lines.append(f"- … +{len(diff['low_signal']) - 40} more")
        lines.append("")

    # Compact per-site AI / messaging
    interesting = [
        s
        for s in diff["sites"]
        if s.get("ai_features_added")
        or s.get("h1_changed")
        or s.get("title_changed")
        or s.get("error")
    ]
    if interesting:
        lines += ["## Site detail", ""]
        for s in interesting:
            lines.append(f"### {s['name']}")
            if s.get("error"):
                lines.append(f"- error: `{s['error'][:200]}`")
            if s.get("h1_changed"):
                lines.append(f"- h1: {s['h1_changed']['from'][:80]!r} → {s['h1_changed']['to'][:80]!r}")
            if s.get("ai_features_added"):
                lines.append(f"- AI labels+: {', '.join(s['ai_features_added'][:12])}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff LocalPDF competitor scans")
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--previous", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None, help="Markdown digest path")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    current_path = args.current
    if not current_path.is_file():
        print(f"ERROR: current scan not found: {current_path}")
        return 1

    previous_path = args.previous
    if previous_path is None:
        if DEFAULT_PREVIOUS.is_file():
            previous_path = DEFAULT_PREVIOUS
        elif FALLBACK_PREVIOUS.is_file():
            previous_path = FALLBACK_PREVIOUS
        else:
            print("ERROR: no previous scan (reports/localpdf/previous.json or examples/localpdf/latest-scan.json)")
            return 1
    if not previous_path.is_file():
        print(f"ERROR: previous scan not found: {previous_path}")
        return 1

    prev_report, curr_report = _load(previous_path), _load(current_path)
    diff = diff_sites(_by_name(prev_report), _by_name(curr_report))
    diff["previous_generated_at"] = prev_report.get("generated_at")
    diff["current_generated_at"] = curr_report.get("generated_at")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_dir = ROOT / "reports" / "localpdf"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.out or (out_dir / f"diff-{stamp}.md")
    json_path = args.json_out or (out_dir / f"diff-{stamp}.json")

    md = render_md(diff, previous_path, current_path)
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    # Convenience copy
    (out_dir / "diff-latest.md").write_text(md, encoding="utf-8")
    (out_dir / "diff-latest.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(md)
    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    return 2 if diff["has_high_signal"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
