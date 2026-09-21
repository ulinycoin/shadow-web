#!/usr/bin/env python3
"""LocalPDF competitor scan via Shadow Web.

Usage:
  python scripts/localpdf_competitor_scan.py
  python scripts/localpdf_competitor_scan.py --json reports/localpdf/custom.json

Default write path:
  reports/localpdf/YYYYMMDD.json
  reports/localpdf/latest.json
  reports/localpdf/previous.json  ← rotated from previous latest

Requires: pip install -e ".[mcp]" && playwright install chromium
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from shadow_web.browser_use import AsyncShadowPage
from shadow_web.query import shadow_grep

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "localpdf"

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

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _labels_from_terse(terse: str) -> list[str]:
    labels = []
    for line in terse.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = re.search(r"@\d+\s+\S+\s+(.+?)(?:\s+\[|$)", line)
        if m:
            labels.append(m.group(1).strip())
    return labels


async def scan_site(context, name: str, url: str) -> dict:
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1000)
        title = await page.title()
        meta_desc = (
            await page.locator('meta[name="description"]').get_attribute("content") or ""
        )
        h1 = ""
        try:
            h1 = (await page.locator("h1").first.inner_text(timeout=3000)).strip()
        except Exception:
            pass

        shadow = AsyncShadowPage(page, capture_mode="auto")
        await shadow.refresh()

        features = shadow_grep(
            shadow.action_map, FEATURE_QUERY, groups=shadow.action_groups
        )
        pricing = shadow_grep(
            shadow.action_map, PRICING_QUERY, groups=shadow.action_groups
        )
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
        return {
            "name": name,
            "url": url,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        }
    finally:
        await page.close()


def build_gap_report(results: list[dict]) -> dict:
    by_name = {r["name"]: r for r in results if "error" not in r}
    local = by_name.get("localpdf_home", {})
    local_features = {x.lower() for x in local.get("feature_labels", [])}

    competitors = [
        k for k in by_name if k != "localpdf_home" and not k.startswith("localpdf_")
    ]
    all_competitor_features: set[str] = set()
    for c in competitors:
        for label in by_name[c].get("feature_labels", []):
            all_competitor_features.add(label.lower())

    def norm(s: str) -> str:
        s = s.lower()
        for junk in (" [page]", " [navigation]", " [footer]", " [header]"):
            s = s.replace(junk, "")
        return re.sub(r"\s+", " ", s).strip()[:80]

    local_norm = {norm(x) for x in local_features}
    comp_norm = {norm(x) for x in all_competitor_features}

    missing_on_localpdf = sorted(
        x
        for x in comp_norm
        if x and not any(x in ln or ln in x for ln in local_norm)
        if any(
            k in x
            for k in (
                "ai",
                "chat",
                "summar",
                "translate",
                "redact",
                "annotate",
                "repair",
                "unlock",
            )
        )
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


def _rotate_and_write(report: dict, explicit: Path | None) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dated = explicit or (REPORT_DIR / f"{stamp}.json")
    latest = REPORT_DIR / "latest.json"
    previous = REPORT_DIR / "previous.json"

    # Rotate: current latest becomes previous (for next diff)
    if latest.is_file():
        shutil.copy2(latest, previous)
    elif not previous.is_file():
        # Seed previous from committed June snapshot so first live run still diffs
        seed = ROOT / "examples" / "localpdf" / "latest-scan.json"
        if seed.is_file():
            shutil.copy2(seed, previous)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    dated.parent.mkdir(parents=True, exist_ok=True)
    dated.write_text(text, encoding="utf-8")
    if dated.resolve() != latest.resolve():
        latest.write_text(text, encoding="utf-8")
    # Keep examples/ mirror for CASE readers
    examples_latest = ROOT / "examples" / "localpdf" / "latest-scan.json"
    examples_latest.write_text(text, encoding="utf-8")
    return dated


async def main() -> None:
    parser = argparse.ArgumentParser(description="Scan LocalPDF competitors with Shadow Web")
    parser.add_argument(
        "--json",
        type=Path,
        help="Override dated JSON path (still updates latest/previous)",
    )
    args = parser.parse_args()

    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        for name, url in TARGETS.items():
            print(f"Scanning {name}...", flush=True)
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1400, "height": 900},
                locale="en-US",
            )
            try:
                results.append(await scan_site(context, name, url))
            finally:
                await context.close()
        await browser.close()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sites": results,
        "analysis": build_gap_report(results),
    }

    path = _rotate_and_write(report, args.json)
    ok = sum(1 for r in results if "error" not in r)
    print(f"Wrote {path} ({ok}/{len(results)} sites ok)")
    print(f"Updated {REPORT_DIR / 'latest.json'}")
    if (REPORT_DIR / "previous.json").is_file():
        print(f"Previous kept at {REPORT_DIR / 'previous.json'}")


if __name__ == "__main__":
    asyncio.run(main())
