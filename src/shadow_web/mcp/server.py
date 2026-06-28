"""Local MCP server for Shadow Web (stdio, async Playwright)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Literal

from shadow_web.compressor import generate_grouped_xml_map, process_html
from shadow_web.query import shadow_grep

# Lazy browser session for navigate/snapshot/click tools.
_session: Dict[str, Any] = {}

_STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_SEARCH_NAV_JUNK = (
    "settings",
    "sign in",
    "privacy",
    "terms",
    "advertise",
    "help",
    "report a concern",
    "suggestions",
    "cookie",
    "search again",
    "see full list",
    "skip to content",
    "accessibility",
    "feedback",
    "rewards",
)

_SEARCH_ENGINES = (
    ("brave", "https://search.brave.com/search?q={query}", ("brave.com", "search.brave.com")),
    ("yahoo", "https://search.yahoo.com/search?p={query}", ("yahoo.com", "yimg.com")),
)


def _get_shadow_page():
    if "shadow_page" not in _session:
        raise RuntimeError("No browser session. Call navigate(url) first.")
    return _session["shadow_page"]


def _extract_search_results(
    action_map: list[dict[str, Any]],
    exclude_domains: tuple[str, ...],
) -> list[dict[str, str]]:
    seen_urls: set[str] = set()
    results: list[dict[str, str]] = []

    for action in action_map:
        label = (action.get("label") or "").strip()
        href = (action.get("href") or "").strip()
        if not href.startswith("http"):
            continue
        if href.startswith("javascript:"):
            continue
        lowered_href = href.lower()
        if any(domain in lowered_href for domain in exclude_domains):
            continue
        if len(label) < 12:
            continue
        lowered_label = label.lower()
        if any(junk in lowered_label for junk in _SEARCH_NAV_JUNK):
            continue
        if href in seen_urls:
            continue

        seen_urls.add(href)
        results.append({"title": label, "url": href, "sid": str(action["id"])})

    return results


async def _ensure_browser():
    from playwright.async_api import async_playwright

    if "playwright" not in _session:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=_STEALTH_USER_AGENT,
            locale="en-US",
        )
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        _session["playwright"] = pw
        _session["browser"] = browser
        _session["context"] = context
        _session["page"] = page


async def _run_shadow_query(shadow, query: str, fmt: Literal["json", "terse", "xml"] = "json") -> dict | str:
    result = await shadow.query(query, fmt="result")
    if fmt == "terse":
        return {"query": query, "format": "terse", "text": result.terse()}
    if fmt == "xml":
        title = await shadow.page.title()
        return {
            "query": query,
            "format": "xml",
            "xml": result.xml(url=shadow.last_url, title=title),
        }
    return {
        "query": query,
        "format": "json",
        **result.to_dict(),
    }


def _format_mcp_response(
    shadow,
    clean_html: str,
    xml_map: str,
    detail: str = "terse",
    title: str = "Page",
    diff: bool = False
) -> dict:
    # Base response properties
    res = {
        "url": shadow.last_url,
        "title": title,
        "interaction_mode": shadow.interaction_mode,
        "webmcp_available": shadow.webmcp.available,
        "webmcp_tools": [tool.to_dict() for tool in shadow.webmcp.tools],
        "action_count": len(shadow.action_map),
        "page_class": getattr(shadow, "page_class", "Static"),
        "page_class_reason": getattr(shadow, "page_class_reason", ""),
    }
    
    # Include diff context if diff mode is requested
    if shadow.last_diff and diff:
        res["diff_mode"] = True
        res["diff_terse"] = shadow.diff_terse()
        if detail == "full":
            res["diff"] = shadow.last_diff.to_dict()

    if detail == "minimal":
        # Minimal: url, title, action_count, groups_summary (name + count)
        groups_summary = [
            {"name": g.get("name", "Page"), "count": len(g.get("elements", []))}
            for g in shadow.action_groups
        ]
        res["groups_summary"] = groups_summary
        return res
        
    if detail == "terse":
        # Terse: top 15 elements or diff delta list
        if shadow.last_diff and diff:
            res["appeared"] = shadow.last_diff.appeared[:15]
            res["changed"] = shadow.last_diff.changed[:15]
            res["disappeared"] = shadow.last_diff.disappeared[:15]
        else:
            res.update({
                "action_map": shadow.action_map[:15],
                "groups_summary": [
                    {"name": g.get("name", "Page"), "count": len(g.get("elements", []))}
                    for g in shadow.action_groups
                ]
            })
        return res
        
    if detail == "xml":
        # XML: grouped XML skeleton
        res["xml_map"] = xml_map
        return res
        
    # Full: debugging layout
    res.update({
        "groups": shadow.action_groups,
        "xml_map": xml_map,
        "clean_html_preview": clean_html[:500],  # HTML preview limit (500 chars)
        "capture_stats": shadow.capture_stats,
        "capture_mode": shadow.capture_mode,
    })
    return res


def create_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError(
            "MCP support requires the 'mcp' package. Install with: pip install shadow-web[mcp]"
        ) from exc

    mcp = FastMCP("shadow-web")

    @mcp.tool()
    def compress_html(html: str) -> dict:
        """Compress raw HTML into clean markup, flat action map, and semantic groups."""
        clean_html, action_map, groups = process_html(html)
        return {
            "clean_html": clean_html,
            "action_map": action_map,
            "groups": groups,
            "action_count": len(action_map),
        }

    @mcp.tool()
    async def shadow_query(query: str, format: str = "terse") -> dict | str:
        """
        shadow_grep — filter current page actions before sending to LLM.

        Query examples:
          type:button
          intent:login
          group:Login Form
          label~/checkout/i
          type:button intent:login
          id:1,3,5

        format: json | terse | xml
        """
        shadow = _get_shadow_page()
        fmt = format if format in ("json", "terse", "xml") else "terse"
        return await _run_shadow_query(shadow, query, fmt=fmt)  # type: ignore[arg-type]

    @mcp.tool()
    def shadow_grep_html(html: str, query: str, format: str = "terse") -> dict | str:
        """Run shadow_grep on raw HTML without a browser session."""
        _, action_map, groups = process_html(html)
        result = shadow_grep(action_map, query, groups=groups)
        fmt = format if format in ("json", "terse", "xml") else "terse"
        if fmt == "terse":
            return {"query": query, "format": "terse", "text": result.terse()}
        if fmt == "xml":
            return {"query": query, "format": "xml", "xml": result.xml()}
        return {"query": query, "format": "json", **result.to_dict()}

    @mcp.tool()
    async def query_page(query: str) -> dict:
        """Alias for shadow_query(format=json). Requires navigate first."""
        shadow = _get_shadow_page()
        return await _run_shadow_query(shadow, query, fmt="json")

    @mcp.tool()
    async def navigate(url: str, capture_mode: str = "auto", detail: str = "terse") -> dict:
        """Open URL in Playwright and build grouped XML Action Map snapshot.
        
        detail: minimal | terse | xml | full
        """
        from shadow_web.browser_use import AsyncShadowPage

        await _ensure_browser()
        page = _session["page"]
        await page.goto(url, wait_until="domcontentloaded")
        heal_url = os.environ.get("SHADOW_WEB_HEAL_URL")
        mode = capture_mode if capture_mode in ("dom", "a11y", "dual", "auto") else "auto"
        shadow = AsyncShadowPage(page, heal_api_url=heal_url, capture_mode=mode)
        clean_html, xml_map = await shadow.refresh()
        _session["shadow_page"] = shadow
        _session["clean_html"] = clean_html

        title = await page.title()
        detail_mode = detail if detail in ("minimal", "terse", "xml", "full") else "terse"
        return _format_mcp_response(shadow, clean_html, xml_map, detail=detail_mode, title=title)

    @mcp.tool()
    async def web_search(query: str) -> dict:
        """Search the web via Brave Search (Yahoo fallback). No API keys required."""
        import urllib.parse
        from shadow_web.browser_use import AsyncShadowPage

        await _ensure_browser()
        page = _session["page"]

        encoded_query = urllib.parse.quote_plus(query)
        heal_url = os.environ.get("SHADOW_WEB_HEAL_URL")
        tried_engines: list[str] = []

        for engine_name, url_template, exclude_domains in _SEARCH_ENGINES:
            tried_engines.append(engine_name)
            url = url_template.format(query=encoded_query)

            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            shadow = AsyncShadowPage(page, heal_api_url=heal_url, capture_mode="auto")
            await shadow.refresh()
            results = _extract_search_results(shadow.action_map, exclude_domains)
            if not results:
                continue

            _session["shadow_page"] = shadow
            return {
                "query": query,
                "engine": engine_name,
                "results": results[:10],
                "results_count": len(results),
            }

        return {
            "query": query,
            "engine": None,
            "engines_tried": tried_engines,
            "results": [],
            "results_count": 0,
            "error": "No organic search results captured. Search engines may be blocking headless access.",
        }

    @mcp.tool()
    async def snapshot(diff: bool = False, detail: str = "terse") -> dict:
        """Refresh current page snapshot. Set diff=True for delta XML after first snapshot.
        
        detail: minimal | terse | xml | full
        """
        shadow = _get_shadow_page()
        clean_html, xml_map = await shadow.refresh(diff=diff)
        _session["clean_html"] = clean_html
        title = await shadow.page.title()
        detail_mode = detail if detail in ("minimal", "terse", "xml", "full") else "terse"
        return _format_mcp_response(shadow, clean_html, xml_map, detail=detail_mode, title=title, diff=diff)

    @mcp.tool()
    async def webmcp_list_tools() -> dict:
        """Detect WebMCP tools on the current page (Chrome 145+ preview)."""
        shadow = _get_shadow_page()
        snapshot = await shadow.list_webmcp_tools()
        return {
            "available": snapshot.available,
            "api": snapshot.api,
            "error": snapshot.error,
            "tools": [tool.to_dict() for tool in snapshot.tools],
            "interaction_mode": shadow.interaction_mode,
        }

    @mcp.tool()
    async def webmcp_execute_tool(name: str, arguments: str = "{}") -> dict:
        """Execute a WebMCP tool by name. arguments: JSON object string."""
        shadow = _get_shadow_page()
        args = json.loads(arguments or "{}")
        result = await shadow.execute_tool(name, args)
        return {
            "ok": True,
            "tool": name,
            "result": result,
            "interaction_mode": shadow.interaction_mode,
            "url": shadow.last_url,
        }

    @mcp.tool()
    async def click(sid: str) -> dict:
        """Click element by data-sid on the current page."""
        shadow = _get_shadow_page()
        await shadow.click(sid)
        return {"ok": True, "sid": sid, "url": shadow.last_url}

    @mcp.tool()
    async def fill(sid: str, value: str) -> dict:
        """Fill input by data-sid on the current page."""
        shadow = _get_shadow_page()
        await shadow.fill(sid, value)
        return {"ok": True, "sid": sid, "url": shadow.last_url}

    @mcp.tool()
    def compress_html_to_xml(html: str, url: str = "", title: str = "") -> str:
        """Return grouped XML action map for raw HTML (no browser)."""
        _, _, groups = process_html(html)
        return generate_grouped_xml_map(url or "about:blank", title or "Page", groups)

    @mcp.tool()
    def schema_table(html: str, max_rows: int = 50) -> list[dict]:
        """Extract structured table data from HTML: columns, types, and rows.

        max_rows: cap rows per table (default 50). Set 0 for no limit.
        """
        from shadow_web.schema_snap import parse_tables
        limit = None if max_rows <= 0 else max_rows
        return parse_tables(html, max_rows=limit)

    @mcp.tool()
    def schema_form(html: str) -> list[dict]:
        """Extract form schema from HTML: action, method, fields with types and validation."""
        from shadow_web.schema_snap import parse_forms
        return parse_forms(html)

    @mcp.tool()
    def schema_list(html: str) -> list[dict]:
        """Extract lists from HTML: unordered, ordered, or standalone select with items."""
        from shadow_web.schema_snap import parse_lists
        return parse_lists(html)

    @mcp.tool()
    def schema_page(html: str, max_rows: int = 50) -> dict:
        """Extract ALL structured data (tables, forms, lists) from HTML at once.

        max_rows: cap rows per table (default 50). Set 0 for no limit.
        """
        from shadow_web.schema_snap import parse_page
        limit = None if max_rows <= 0 else max_rows
        return parse_page(html, max_rows=limit)

    @mcp.tool()
    def get_page_html(max_chars: int = 50000) -> str:
        """Return clean HTML from the current browser session.

        max_chars: truncate output (default 50000). Set 0 for full HTML — may be huge.
        Requires navigate(url) or snapshot() first.
        """
        if "clean_html" not in _session:
            raise RuntimeError("No page loaded. Call navigate(url) or snapshot() first.")
        html = _session["clean_html"]
        if max_chars > 0 and len(html) > max_chars:
            return html[:max_chars] + f"\n<!-- truncated: {len(html)} chars total, returned {max_chars} -->"
        return html

    @mcp.tool()
    def schema_session(max_rows: int = 50) -> dict:
        """Extract structured data from the current browser session (tables, forms, lists).

        max_rows: cap rows per table (default 50). Set 0 for no limit.
        Requires navigate(url) or snapshot() first.
        """
        if "clean_html" not in _session:
            raise RuntimeError("No page loaded. Call navigate(url) or snapshot() first.")
        from shadow_web.schema_snap import parse_page
        limit = None if max_rows <= 0 else max_rows
        return parse_page(_session["clean_html"], max_rows=limit)

    @mcp.tool()
    def schema_json(html: str, table_index: int = 0, max_rows: int = 50) -> list[dict]:
        """Export HTML table as JSON records: [{Name: \"Alice\", Age: 30}, ...].

        table_index: which <table> on the page (default 0). max_rows: cap rows (0 = all).
        """
        from shadow_web.schema_snap import export_table_json
        limit = None if max_rows <= 0 else max_rows
        return export_table_json(html, table_index=table_index, max_rows=limit)

    @mcp.tool()
    def schema_csv(html: str, table_index: int = 0, max_rows: int = 50) -> str:
        """Export HTML table as CSV: \"Name,Age,Email\\nAlice,30,alice@...\".

        table_index: which <table> on the page (default 0). max_rows: cap rows (0 = all).
        """
        from shadow_web.schema_snap import export_table_csv
        limit = None if max_rows <= 0 else max_rows
        return export_table_csv(html, table_index=table_index, max_rows=limit)

    @mcp.tool()
    def schema_session_json(table_index: int = 0, max_rows: int = 50) -> list[dict]:
        """Export table from current browser session as JSON records. Requires navigate/snapshot first."""
        if "clean_html" not in _session:
            raise RuntimeError("No page loaded. Call navigate(url) or snapshot() first.")
        from shadow_web.schema_snap import export_table_json
        limit = None if max_rows <= 0 else max_rows
        return export_table_json(_session["clean_html"], table_index=table_index, max_rows=limit)

    @mcp.tool()
    def schema_session_csv(table_index: int = 0, max_rows: int = 50) -> str:
        """Export table from current browser session as CSV. Requires navigate/snapshot first."""
        if "clean_html" not in _session:
            raise RuntimeError("No page loaded. Call navigate(url) or snapshot() first.")
        from shadow_web.schema_snap import export_table_csv
        limit = None if max_rows <= 0 else max_rows
        return export_table_csv(_session["clean_html"], table_index=table_index, max_rows=limit)

    return mcp


def main() -> None:
    mcp = create_mcp_server()
    mcp.run()


if __name__ == "__main__":
    main()
