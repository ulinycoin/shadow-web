#!/usr/bin/env python3
"""LocalPDF competitor scan via Shadow Web.

Usage:
  python scripts/localpdf_competitor_scan.py
  python scripts/localpdf_competitor_scan.py --json reports/localpdf-scan.json

Requires: pip install -e ".[mcp]" && playwright install chromium
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from shadow_web.browser_use import AsyncShadowPage
from shadow_web.query import shadow_grep

TARGETS = {
  "localpdf_home": "https://localpdf.online/",
  "localpdf_pricing": "https://localpdf.online/pricing",
  "smallpdf_tools": "https://smallpdf.com/pdf-tools",
  "smallpdf_pricing": "https://smallpdf.com/pricing",
  "ilovepdf": "https://www.ilovepdf.com/",
  "sejda": "https://www.sejda.com/",
  "pdf24": "https://tools.pdf24.org/en/",
}

FEATURE_QUERY = "type:a label~/pdf|merge|ocr|compress|sign|edit|convert|ai|chat/i"
PRICING_QUERY = "label~/price|pricing|plan|pro|free|trial|\\$/i"
CTA_QUERY = "label~/try|start|free|upload|open|download/i"


def _labels_from_terse(terse: str) -> list[str]:
    labels = []
    for line in terse.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = re.search(r"@\d+\s+\S+\s+(.+?)(?:\s+\[|$)", line)
        if m:
            labels.append(m.group(1).strip())
    return labels


async def scan_site(name: str, url: str) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            title = await page.title()
            meta_desc = await page.locator('meta[name="description"]').get_attribute("content") or ""
            h1 = ""
            try:
                h1 = (await page.locator("h1").first.inner_text(timeout=3000)).strip()
            except Exception:
                pass

            shadow = AsyncShadowPage(page, capture_mode="auto")
            await shadow.refresh()

            features = shadow_grep(shadow.action_map, FEATURE_QUERY, groups=shadow.action_groups)
            pricing = shadow_grep(shadow.action_map, PRICING_QUERY, groups=shadow.action_groups)
            ctas = shadow_grep(shadow.action_map, CTA_QUERY, groups=shadow.action_groups)

            feature_labels = _labels_from_terse(features.terse())
            return {
                "name": name,
                "url": url,
                "scanned_at": datetime.now(timezone.utc).isoformat(),
                "title": title,
                "meta_description": meta_desc,
                "h1": h1,
                "page_class": shadow.page_class,
                "page_class_reason": shadow.page_class_reason,
                "action_count": len(shadow.action_map),
                "feature_labels": feature_labels,
                "pricing_hits": _labels_from_terse(pricing.terse()),
                "cta_hits": _labels_from_terse(ctas.terse()),
            }
        except Exception as exc:
            return {"name": name, "url": url, "error": str(exc)}
        finally:
            await browser.close()


def build_gap_report(results: list[dict]) -> dict:
    by_name = {r["name"]: r for r in results if "error" not in r}
    local = by_name.get("localpdf_home", {})
    local_features = {x.lower() for x in local.get("feature_labels", [])}

    competitors = [k for k in by_name if k != "localpdf_home" and not k.startswith("localpdf_")]
    all_competitor_features: set[str] = set()
    for c in competitors:
        for label in by_name[c].get("feature_labels", []):
            all_competitor_features.add(label.lower())

    # Normalize tool names for gap detection
    def norm(s: str) -> str:
        s = s.lower()
        for junk in (" [page]", " [navigation]", " [footer]", " [header]"):
            s = s.replace(junk, "")
        return re.sub(r"\s+", " ", s).strip()[:80]

    local_norm = {norm(x) for x in local_features}
    comp_norm = {norm(x) for x in all_competitor_features}

    missing_on_localpdf = sorted(
        x for x in comp_norm
        if x and not any(x in ln or ln in x for ln in local_norm)
        if any(k in x for k in ("ai", "chat", "summar", "translate", "redact", "annotate", "repair", "unlock"))
    )[:15]

    localpdf_edges = [
        "local-first / zero upload positioning",
        "private PDF editor narrative",
        "offline-capable browser workflow",
        "use-case pages (lawyers, HR, accountants)",
    ]

    return {
        "localpdf_feature_count": len(local.get("feature_labels", [])),
        "competitor_feature_union": len(comp_norm),
        "feature_gaps_to_cover_in_content": missing_on_localpdf,
        "localpdf_differentiators": localpdf_edges,
        "seo_actions": [
            "Refresh compare/* pages monthly from this scan (pricing + feature deltas)",
            "Add programmatic pages: 'edit pdf without uploading', 'offline ocr pdf'",
            "Counter AI-PDF angle: 'private pdf workflow without cloud document upload'",
            "Fix SEO audit P0: www redirect, pricing canonical, sitemap gaps",
            "Update blog titles 2025→2026 + dateModified schema",
        ],
        "conversion_actions": [
            "Home CTA is strong ('Open LocalPDF — free'); add same CTA on thin landing pages",
            "Pricing page has Use Free + Pro buttons — A/B test 'Start Pro' copy vs competitor trial language",
            "Comparison pages need pricing row + upload-vs-local table from scan data",
        ],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Scan LocalPDF competitors with Shadow Web")
    parser.add_argument("--json", type=Path, help="Write full report JSON to path")
    args = parser.parse_args()

    results = []
    for name, url in TARGETS.items():
        print(f"Scanning {name}...", flush=True)
        results.append(await scan_site(name, url))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sites": results,
        "analysis": build_gap_report(results),
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text, encoding="utf-8")
        print(f"Wrote {args.json}")
    else:
        print(text)


if __name__ == "__main__":
    asyncio.run(main())
