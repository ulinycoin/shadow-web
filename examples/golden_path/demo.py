#!/usr/bin/env python3
"""Golden path demo — structure → data → filter (token budget at each step).

Mirrors the recommended MCP workflow without Cursor:

  navigate(minimal) → schema_session_json() → shadow_query(terse)

Run:
  python examples/golden_path/demo.py
  python examples/golden_path/demo.py --quick    # one site (smoke / CI-friendly)
  pip install tiktoken                         # optional: accurate token counts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from playwright.sync_api import sync_playwright
from shadow_web.schema_snap import export_table_json, parse_page, parse_tables
from shadow_web.wrapper import ShadowPage

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")
    HAS_TIKTOKEN = True

    def count_tokens(text: str) -> int:
        return len(_enc.encode(text))

except ImportError:
    HAS_TIKTOKEN = False

    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


SITES = [
    {
        "name": "W3Schools HTML Tables",
        "url": "https://www.w3schools.com/html/html_tables.asp",
        "query": "type:a",
    },
    {
        "name": "Wikipedia (Web Scraping)",
        "url": "https://en.wikipedia.org/wiki/Web_scraping",
        "query": "type:a",
    },
]


def _pick_table_index(clean_html: str) -> int:
    """Prefer tables with real column headers and multiple rows."""
    tables = parse_tables(clean_html)
    best_idx = 0
    best_score = -1
    for i, table in enumerate(tables):
        cols = table.get("columns") or []
        rows = table.get("rows") or []
        score = len(rows)
        if cols and not str(cols[0]).startswith("col_"):
            score += 100
        if len(cols) >= 2:
            score += 20
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def _minimal_payload(shadow, title: str) -> dict:
    """Same fields as MCP navigate(..., detail='minimal')."""
    return {
        "url": shadow.last_url,
        "title": title,
        "action_count": len(shadow.action_map),
        "page_class": getattr(shadow, "page_class", "Static"),
        "groups_summary": [
            {"name": g.get("name", "Page"), "count": len(g.get("elements", []))}
            for g in shadow.action_groups
        ],
    }


def _run_site(page, site: dict) -> dict:
    name, url, query = site["name"], site["url"], site["query"]
    print(f"\n{'─' * 72}\n  {name}\n  {url}\n{'─' * 72}")

    t0 = time.time()
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2000)
    raw_html = page.content()
    raw_tokens = count_tokens(raw_html)

    shadow = ShadowPage(page)
    clean_html, _xml = shadow.refresh()
    title = page.title()
    load_ms = (time.time() - t0) * 1000

    minimal = _minimal_payload(shadow, title)
    minimal_tokens = count_tokens(json.dumps(minimal, ensure_ascii=False))

    table_idx = _pick_table_index(clean_html)
    records = export_table_json(clean_html, table_index=table_idx, max_rows=10)
    schema_tokens = count_tokens(json.dumps(records, ensure_ascii=False))

    query_result = shadow.query(query, fmt="terse")
    terse_text = query_result.terse() if hasattr(query_result, "terse") else str(query_result)
    query_tokens = count_tokens(terse_text[:2000])

    parsed = parse_page(clean_html, max_rows=10)
    table_count = len(parsed["tables"])
    form_count = len(parsed["forms"])

    naive_tokens = raw_tokens + count_tokens(json.dumps(parse_page(raw_html, max_rows=10), ensure_ascii=False))
    shadow_tokens = minimal_tokens + schema_tokens + query_tokens

    print(f"  Load + capture     {load_ms:,.0f} ms")
    print(f"  Raw HTML           {raw_tokens:>8,} tokens")
    print(f"  MCP minimal        {minimal_tokens:>8,} tokens  (action_count={minimal['action_count']}, page_class={minimal['page_class']})")
    print(f"  schema_json(10)    {schema_tokens:>8,} tokens  (table #{table_idx}, {len(records)} records, {table_count} table(s), {form_count} form(s))")
    print(f"  shadow_query terse {query_tokens:>8,} tokens  (query={query!r})")
    print(f"  ── pipeline total  {shadow_tokens:>8,} tokens  vs naive raw+schema {naive_tokens:>8,}  (−{(1 - shadow_tokens / max(1, naive_tokens)) * 100:.0f}%)")

    if records:
        print(f"  Sample record:     {records[0]}")

    return {
        "name": name,
        "raw_tokens": raw_tokens,
        "minimal_tokens": minimal_tokens,
        "schema_tokens": schema_tokens,
        "query_tokens": query_tokens,
        "shadow_tokens": shadow_tokens,
        "naive_tokens": naive_tokens,
        "records": len(records),
        "action_count": minimal["action_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow Web golden path demo")
    parser.add_argument("--quick", action="store_true", help="Run only the first site (smoke test)")
    args = parser.parse_args()

    sites = SITES[:1] if args.quick else SITES

    print("=" * 72)
    print(" SHADOW WEB — GOLDEN PATH DEMO")
    print(" Workflow: navigate(minimal) → schema_session_json → shadow_query(terse)")
    print(f" Token counter: {'tiktoken cl100k_base' if HAS_TIKTOKEN else '~4 chars/token estimate'}")
    print("=" * 72)

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for site in sites:
            try:
                results.append(_run_site(page, site))
            except Exception as exc:
                print(f"  [ERROR] {site['name']}: {exc}")
        browser.close()

    if not results:
        print("\nNo sites completed successfully.")
        return 1

    print(f"\n{'=' * 72}")
    print(f"{'Site':<28} | {'Naive':>8} | {'Shadow':>8} | {'Saved':>7} | Records")
    print("-" * 72)
    for r in results:
        saved = (1 - r["shadow_tokens"] / max(1, r["naive_tokens"])) * 100
        print(
            f"{r['name']:<28} | {r['naive_tokens']:>8,} | {r['shadow_tokens']:>8,} | "
            f"-{saved:>5.0f}% | {r['records']}"
        )
    print("=" * 72)
    print("\nMCP equivalent (3 tool calls, no full HTML to LLM):")
    print("  1. navigate(url, detail='minimal')")
    print("  2. schema_session_json(max_rows=10)")
    print("  3. shadow_query('type:a', format='terse')")
    print("\nSee examples/golden_path/CASE.md for the full agent playbook.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
