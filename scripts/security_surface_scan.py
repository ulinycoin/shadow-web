#!/usr/bin/env python3
"""Attack surface security scan via Shadow Web.

Usage:
  python scripts/security_surface_scan.py https://example.com
  python scripts/security_surface_scan.py https://a.com https://a.com/login --crawl-depth 1
  python scripts/security_surface_scan.py --url-file targets.txt --json report.json --markdown report.md

Requires: pip install -e ".[mcp]" && playwright install chromium
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from shadow_web.browser_use import AsyncShadowPage
from shadow_web.security_scan import (
    analyze_surface,
    extract_same_domain_links,
    render_markdown_report,
    summarize_report,
)


async def scan_page(url: str, *, timeout_ms: int = 45000, capture_mode: str = "auto") -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            title = await page.title()

            shadow = AsyncShadowPage(page, capture_mode=capture_mode)
            clean_html, _ = await shadow.refresh()

            stats = getattr(shadow, "capture_stats", {}) or {}
            result = analyze_surface(
                url,
                title=title,
                page_class=shadow.page_class,
                page_class_reason=shadow.page_class_reason,
                action_count=len(shadow.action_map),
                action_map=shadow.action_map,
                clean_html=clean_html,
                capture_stats=stats,
            )
            result["scanned_at"] = datetime.now(timezone.utc).isoformat()
            result["outbound_same_domain_links"] = extract_same_domain_links(url, shadow.action_map)
            return result
        except Exception as exc:
            return {"url": url, "error": str(exc)}
        finally:
            await browser.close()


def _load_urls(url_file: Path | None, positional: list[str]) -> list[str]:
    urls: list[str] = list(positional)
    if url_file:
        for line in url_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    # dedupe preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


async def crawl_and_scan(
    seed_urls: list[str],
    *,
    crawl_depth: int,
    max_pages: int,
    same_domain: bool,
    timeout_ms: int,
) -> list[dict]:
    if not seed_urls:
        raise ValueError("No URLs provided")

    queue: deque[tuple[str, int]] = deque((url, 0) for url in seed_urls)
    visited: set[str] = set()
    pages: list[dict] = []

    seed_hosts = {urlparse(u).netloc.lower() for u in seed_urls}

    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        print(f"Scanning [{depth}] {url}...", flush=True)
        page_result = await scan_page(url, timeout_ms=timeout_ms)
        pages.append(page_result)

        if "error" in page_result:
            continue
        if depth >= crawl_depth:
            continue

        for link in page_result.get("outbound_same_domain_links", []):
            if link in visited:
                continue
            if same_domain and urlparse(link).netloc.lower() not in seed_hosts:
                continue
            queue.append((link, depth + 1))

    return pages


async def main() -> None:
    parser = argparse.ArgumentParser(description="Attack surface security scan (Shadow Web)")
    parser.add_argument("urls", nargs="*", help="Seed URL(s) to scan")
    parser.add_argument("--url-file", type=Path, help="Text file with one URL per line")
    parser.add_argument("--json", type=Path, help="Write JSON report to path")
    parser.add_argument("--markdown", type=Path, help="Write Markdown report to path")
    parser.add_argument("--crawl-depth", type=int, default=0, help="Follow same-domain links (0 = seeds only)")
    parser.add_argument("--max-pages", type=int, default=15, help="Max pages per run")
    parser.add_argument(
        "--same-domain",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Restrict crawl to seed domain(s)",
    )
    parser.add_argument("--timeout", type=int, default=45, help="Page load timeout (seconds)")
    args = parser.parse_args()

    urls = _load_urls(args.url_file, args.urls)
    if not urls:
        parser.error("Provide at least one URL positional arg or --url-file")

    pages = await crawl_and_scan(
        urls,
        crawl_depth=max(0, args.crawl_depth),
        max_pages=max(1, args.max_pages),
        same_domain=args.same_domain,
        timeout_ms=args.timeout * 1000,
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Automated attack surface mapping only. Not a penetration test.",
        "seeds": urls,
        "crawl_depth": args.crawl_depth,
        "max_pages": args.max_pages,
        "pages": pages,
        "summary": summarize_report(pages),
    }

    markdown = render_markdown_report(report)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote JSON: {args.json}", flush=True)

    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown, encoding="utf-8")
        print(f"Wrote Markdown: {args.markdown}", flush=True)

    if not args.json and not args.markdown:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        totals = report["summary"]["finding_totals"]
        print(
            f"Done. critical={totals.get('critical', 0)} high={totals.get('high', 0)} "
            f"medium={totals.get('medium', 0)} pages={report['summary']['pages_scanned']}",
            flush=True,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
