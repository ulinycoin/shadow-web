"""Local MCP server for Shadow Web (stdio, async Playwright)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Literal

from shadow_web.compressor import generate_grouped_xml_map, process_html
from shadow_web.query import shadow_grep

# Lazy browser session for navigate/snapshot/click tools.
_session: Dict[str, Any] = {}


def _get_shadow_page():
    if "shadow_page" not in _session:
        raise RuntimeError("No browser session. Call navigate(url) first.")
    return _session["shadow_page"]


async def _ensure_browser():
    from playwright.async_api import async_playwright

    if "playwright" not in _session:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
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

        title = await page.title()
        detail_mode = detail if detail in ("minimal", "terse", "xml", "full") else "terse"
        return _format_mcp_response(shadow, clean_html, xml_map, detail=detail_mode, title=title)

    @mcp.tool()
    async def web_search(query: str) -> dict:
        """Search the web via Yahoo Search (no API keys, works out-of-the-box)."""
        import urllib.parse
        from shadow_web.browser_use import AsyncShadowPage

        await _ensure_browser()
        page = _session["page"]
        
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://search.yahoo.com/search?p={encoded_query}"
        
        await page.goto(url, wait_until="domcontentloaded")
        shadow = AsyncShadowPage(page, capture_mode="auto")
        await shadow.refresh()
        
        results = []
        for action in shadow.action_map:
            label = action.get("label", "")
            href = action.get("href", "")
            if href and not href.startswith("/") and "yahoo" not in href and len(label) > 10:
                results.append({
                    "title": label,
                    "url": href,
                    "sid": action["id"]
                })
                
        # Cache the shadow page session so subsequent snapshot/click commands work on search results
        _session["shadow_page"] = shadow
        
        return {
            "query": query,
            "results": results[:10],
            "results_count": len(results),
        }

    @mcp.tool()
    async def snapshot(diff: bool = False, detail: str = "terse") -> dict:
        """Refresh current page snapshot. Set diff=True for delta XML after first snapshot.
        
        detail: minimal | terse | xml | full
        """
        shadow = _get_shadow_page()
        clean_html, xml_map = await shadow.refresh(diff=diff)
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

    return mcp


def main() -> None:
    mcp = create_mcp_server()
    mcp.run()


if __name__ == "__main__":
    main()
