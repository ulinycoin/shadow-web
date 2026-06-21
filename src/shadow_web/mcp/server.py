"""Local MCP server for Shadow Web (stdio, sync Playwright)."""

from __future__ import annotations

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


def _ensure_browser():
    from playwright.sync_api import sync_playwright

    if "playwright" not in _session:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        _session["playwright"] = pw
        _session["browser"] = browser
        _session["context"] = context
        _session["page"] = page


def _run_shadow_query(shadow, query: str, fmt: Literal["json", "terse", "xml"] = "json") -> dict | str:
    result = shadow.query(query, fmt="result")
    if fmt == "terse":
        return {"query": query, "format": "terse", "text": result.terse()}
    if fmt == "xml":
        return {
            "query": query,
            "format": "xml",
            "xml": result.xml(url=shadow.last_url, title=shadow.page.title()),
        }
    return {
        "query": query,
        "format": "json",
        **result.to_dict(),
    }


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
    def shadow_query(query: str, format: str = "terse") -> dict | str:
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
        return _run_shadow_query(shadow, query, fmt=fmt)  # type: ignore[arg-type]

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
    def query_page(query: str) -> dict:
        """Alias for shadow_query(format=json). Requires navigate first."""
        shadow = _get_shadow_page()
        return _run_shadow_query(shadow, query, fmt="json")

    @mcp.tool()
    def navigate(url: str, capture_mode: str = "auto") -> dict:
        """Open URL in Playwright and build grouped Action Map snapshot."""
        from shadow_web.wrapper import ShadowPage

        _ensure_browser()
        page = _session["page"]
        page.goto(url, wait_until="domcontentloaded")
        heal_url = os.environ.get("SHADOW_WEB_HEAL_URL")
        mode = capture_mode if capture_mode in ("dom", "a11y", "dual", "auto") else "auto"
        shadow = ShadowPage(page, heal_api_url=heal_url, capture_mode=mode)
        clean_html, xml_map = shadow.refresh()
        _session["shadow_page"] = shadow

        return {
            "url": shadow.last_url,
            "title": page.title(),
            "interaction_mode": shadow.interaction_mode,
            "capture_mode": shadow.capture_mode,
            "webmcp_available": shadow.webmcp.available,
            "webmcp_tools": [tool.to_dict() for tool in shadow.webmcp.tools],
            "action_count": len(shadow.action_map),
            "groups": shadow.action_groups,
            "xml_map": xml_map,
            "clean_html_preview": clean_html[:2000],
            "capture_stats": shadow.capture_stats,
            "capture_mode": shadow.capture_mode,
        }

    @mcp.tool()
    def snapshot(diff: bool = False) -> dict:
        """Refresh current page snapshot. Set diff=True for delta XML after first snapshot."""
        shadow = _get_shadow_page()
        clean_html, xml_map = shadow.refresh(diff=diff)
        payload = {
            "url": shadow.last_url,
            "interaction_mode": shadow.interaction_mode,
            "webmcp_available": shadow.webmcp.available,
            "webmcp_tools": [tool.to_dict() for tool in shadow.webmcp.tools],
            "action_count": len(shadow.action_map),
            "groups": shadow.action_groups,
            "xml_map": xml_map,
            "clean_html_preview": clean_html[:2000],
            "capture_stats": shadow.capture_stats,
            "capture_mode": shadow.capture_mode,
            "diff_mode": diff,
        }
        if shadow.last_diff:
            payload["diff"] = shadow.last_diff.to_dict()
            payload["diff_terse"] = shadow.diff_terse()
        return payload

    @mcp.tool()
    def webmcp_list_tools() -> dict:
        """Detect WebMCP tools on the current page (Chrome 145+ preview)."""
        shadow = _get_shadow_page()
        snapshot = shadow.list_webmcp_tools()
        return {
            "available": snapshot.available,
            "api": snapshot.api,
            "error": snapshot.error,
            "tools": [tool.to_dict() for tool in snapshot.tools],
            "interaction_mode": shadow.interaction_mode,
        }

    @mcp.tool()
    def webmcp_execute_tool(name: str, arguments: str = "{}") -> dict:
        """Execute a WebMCP tool by name. arguments: JSON object string."""
        import json

        shadow = _get_shadow_page()
        args = json.loads(arguments or "{}")
        result = shadow.execute_tool(name, args)
        return {
            "ok": True,
            "tool": name,
            "result": result,
            "interaction_mode": shadow.interaction_mode,
            "url": shadow.last_url,
        }

    @mcp.tool()
    def click(sid: str) -> dict:
        """Click element by data-sid on the current page."""
        shadow = _get_shadow_page()
        shadow.click(sid)
        return {"ok": True, "sid": sid, "url": shadow.last_url}

    @mcp.tool()
    def fill(sid: str, value: str) -> dict:
        """Fill input by data-sid on the current page."""
        shadow = _get_shadow_page()
        shadow.fill(sid, value)
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
