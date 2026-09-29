"""Local MCP server for Shadow Web (stdio, async Playwright)."""

from __future__ import annotations

import itertools
import json
import logging
import os
from typing import Any, Dict, Literal

from shadow_web.compressor import generate_grouped_xml_map, process_html
from shadow_web.query import shadow_grep
from shadow_web.runtime_env import (
    allow_chromium_fallback,
    camoufox_unavailable_message,
    load_dotenv_files,
    preferred_browser_engine,
)
from shadow_web.url_guard import assert_navigation_landed, validate_navigation_url

logger = logging.getLogger("shadow-web.mcp")

# Per-session browser state for navigate/snapshot/click tools.
#
# Every entry is keyed by an MCP session key (see _current_session_key) so two
# concurrent MCP clients own two Playwright Pages instead of interleaving
# goto/refresh on one shared document.
_SESSIONS: Dict[str, Dict[str, Any]] = {}
# One asyncio.Lock per session key: it serialises a single client's own
# browser actions (page.goto -> refresh must not interleave with a click),
# while different clients proceed in parallel on their own pages.
_SESSION_LOCKS: Dict[str, Any] = {}  # asyncio.Lock per key, created lazily
# Key used whenever no MCP request context is available: stdio single-client
# servers, direct calls from tests/scripts, and the mcp-less import path.
_DEFAULT_SESSION_KEY = "__default__"
# The default session's state, kept under the historical module name so
# external callers/tests that monkeypatch ``_session`` still address the
# fallback session. Module code must go through _sess() instead.
_session: Dict[str, Any] = {}
_SESSIONS[_DEFAULT_SESSION_KEY] = _session

# Identity -> stable key for every MCP session object seen so far.
#
# The key used to be ``str(id(session))``, and CPython recycles ``id()`` once an
# object is collected: a client that disconnected could hand its registry entry
# - and therefore its browser and captured page - to the next client that
# happened to allocate at the same address.  Keys are now handed out
# monotonically and can never be reused.
#
# The session object is held STRONGLY in the tuple on purpose: while the entry
# lives its ``id()`` cannot be recycled at all, and the ``is not session``
# re-check below makes even that failure mode impossible.  ``_SESSION_IDENTITIES``
# is bounded by ``SHADOW_WEB_MCP_MAX_SESSIONS`` (see ``_enforce_session_cap``).
_SESSION_IDENTITIES: Dict[int, tuple[Any, str]] = {}
_SESSION_KEY_SEQ = itertools.count(1)
# Running FastMCP app, set by create_mcp_server(); None until then.
_mcp_app: Any = None
load_dotenv_files()

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

# Hard caps for thin MCP tools. The thick agent path already bounds these;
# the thin path must not let one call allocate unbounded work or context.
def _env_int(name: str, default: int) -> int:
    """Parse an integer cap defensively: a malformed value must not break import."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed %s=%r; using %d", name, raw, default)
        return default
    return value if value > 0 else default


_MAX_HTML_CHARS = _env_int("SHADOW_WEB_MCP_MAX_HTML", 2_000_000)
_MAX_ROWS = _env_int("SHADOW_WEB_MCP_MAX_ROWS", 5_000)
_MAX_OUTLINE_TOKENS = _env_int("SHADOW_WEB_MCP_MAX_OUTLINE_TOKENS", 10_000)
_MAX_BLOCK_TOKENS = _env_int("SHADOW_WEB_MCP_MAX_BLOCK_TOKENS", 50_000)
_MAX_BLOCK_IDS = _env_int("SHADOW_WEB_MCP_MAX_BLOCK_IDS", 200)
# Live MCP clients kept at once.  Each one owns a full browser, so an unbounded
# registry is a resource-exhaustion vector.
_MCP_MAX_SESSIONS = _env_int("SHADOW_WEB_MCP_MAX_SESSIONS", 8)

# Each thick agent run owns a whole browser, so concurrency is bounded and the
# run has a wall-clock ceiling (the LLM client also has its own HTTP timeout).
_AGENT_RUN_TIMEOUT_SEC = float(_env_int("SHADOW_WEB_AGENT_TIMEOUT", 600))
_AGENT_CONCURRENCY = _env_int("SHADOW_WEB_AGENT_CONCURRENCY", 1)
_agent_semaphore: Any = None


def _get_agent_semaphore():
    global _agent_semaphore
    if _agent_semaphore is None:
        import asyncio

        _agent_semaphore = asyncio.Semaphore(_AGENT_CONCURRENCY)
    return _agent_semaphore


def _current_session_object() -> Any:
    """Return the request-scoped MCP session object, or None if unavailable.

    Deliberately import-safe and never raising: a missing request context, a
    stdio single-client transport, a direct call from a test, or the mcp package
    not being importable must all degrade to the default session.
    """
    app = _mcp_app
    if app is None:
        return None
    try:
        ctx = app.get_context()
        return getattr(ctx, "session", None)
    except Exception:  # noqa: BLE001 - key derivation must never break a tool
        return None


def _current_session_key() -> str:
    """Return a stable, never-recycled key identifying the calling MCP session.

    See ``_SESSION_IDENTITIES``: ``id(session)`` alone is unsafe because CPython
    reuses addresses of collected objects, which let a new client inherit a dead
    client's browser and page state.
    """
    session = _current_session_object()
    if session is None:
        return _DEFAULT_SESSION_KEY
    entry = _SESSION_IDENTITIES.get(id(session))
    if entry is None or entry[0] is not session:
        key = f"mcp-{next(_SESSION_KEY_SEQ)}"
        _SESSION_IDENTITIES[id(session)] = (session, key)
        return key
    return entry[1]


def _sess() -> Dict[str, Any]:
    """Return the calling session's mutable state dict, creating it lazily."""
    key = _current_session_key()
    if key == _DEFAULT_SESSION_KEY:
        # Honour a rebound ``_session`` (tests / legacy callers) by re-pointing
        # the registry at it; in normal operation the alias never changes.
        if _SESSIONS.get(key) is not _session:
            _SESSIONS[key] = _session
        return _session
    return _SESSIONS.setdefault(key, {})


def _get_shadow_page():
    session = _sess()
    if "shadow_page" not in session:
        raise RuntimeError("No browser session. Call navigate(url) first.")
    return session["shadow_page"]


def _get_content_index_blocks() -> list[dict[str, Any]]:
    """Build or reuse the Content Block Index for the current page."""
    from shadow_web.content_index import build, quality

    shadow = _get_shadow_page()
    clean_html = shadow.clean_html
    if not clean_html:
        raise RuntimeError(
            "Current page has no captured HTML. Navigate to a DOM page first."
        )
    session = _sess()
    if session.get("content_index_html") != clean_html:
        session["content_index_html"] = clean_html
        session["content_index_blocks"] = build(clean_html)
        session["content_index_quality"] = quality(
            clean_html,
            session["content_index_blocks"],
        )
    return session["content_index_blocks"]


def _get_content_index_quality() -> dict[str, Any]:
    """Return cached coverage and signal-retention diagnostics."""
    _get_content_index_blocks()
    return _sess()["content_index_quality"]


def _extract_search_results(
    action_map: list[dict[str, Any]],
    exclude_domains: tuple[str, ...],
) -> list[dict[str, str]]:
    from urllib.parse import urlparse

    seen_urls: set[str] = set()
    results: list[dict[str, str]] = []

    def _host_matches(href: str) -> bool:
        try:
            hostname = (urlparse(href).hostname or "").lower()
        except ValueError:
            return False
        if not hostname:
            return False
        return any(
            hostname == domain or hostname.endswith("." + domain)
            for domain in exclude_domains
        )

    for action in action_map:
        label = (action.get("label") or "").strip()
        href = (action.get("href") or "").strip()
        if not href.startswith("http"):
            continue
        if href.startswith("javascript:"):
            continue
        if _host_matches(href):
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


def _get_session_lock():
    """Return the calling session's asyncio.Lock, created on first use."""
    import asyncio

    key = _current_session_key()
    lock = _SESSION_LOCKS.get(key)
    if lock is None:
        # asyncio.Lock() no longer binds a loop at construction (3.10+), so
        # this is safe to call before the loop runs.
        lock = asyncio.Lock()
        _SESSION_LOCKS[key] = lock
    return lock


async def _close_session_state(session: Dict[str, Any]) -> None:
    """Best-effort close of page/context/browser/playwright for one session."""
    import logging
    logger = logging.getLogger("shadow-web.mcp")

    page = session.pop("page", None)
    context = session.pop("context", None)
    browser = session.pop("browser", None)
    pw = session.pop("playwright_driver", None)
    session.pop("playwright", None)
    session.pop("shadow_page", None)
    session.pop("clean_html", None)
    session.pop("content_index_html", None)
    session.pop("content_index_blocks", None)
    session.pop("content_index_quality", None)

    try:
        if page is not None:
            await page.close()
    except Exception as exc:
        logger.warning("Page close failed during session reset: %s", exc)
    try:
        if context is not None:
            await context.close()
    except Exception as exc:
        logger.warning("Context close failed during session reset: %s", exc)
    try:
        if browser is not None:
            await browser.close()
    except Exception as exc:
        logger.warning("Browser close failed during session reset: %s", exc)
    try:
        if pw is not None:
            await pw.stop()
    except Exception as exc:
        logger.warning("Playwright stop failed during session reset: %s", exc)


async def _close_browser_resources() -> None:
    """Best-effort close of the CURRENT session's browser resources."""
    await _close_session_state(_sess())


async def _drop_session(key: str) -> None:
    """Close one session's browser resources and forget it completely."""
    session = _SESSIONS.pop(key, None)
    if session is not None:
        await _close_session_state(session)
    _SESSION_LOCKS.pop(key, None)
    for ident, (_obj, session_key) in list(_SESSION_IDENTITIES.items()):
        if session_key == key:
            del _SESSION_IDENTITIES[ident]


async def _close_all_sessions() -> None:
    """Best-effort close of every registered session (process shutdown hook)."""
    for key in list(_SESSIONS):
        if key == _DEFAULT_SESSION_KEY:
            await _close_session_state(_SESSIONS[key])
        else:
            await _drop_session(key)


async def _enforce_session_cap() -> None:
    """Reclaim browsers from the oldest idle sessions past the cap.

    A session that is currently inside its own lock is never evicted: closing a
    page under an in-flight tool call is worse than the temporary overrun, so
    the cap is retried on the next call instead.
    """
    cap = _MCP_MAX_SESSIONS
    candidates = [key for key in _SESSIONS if key != _DEFAULT_SESSION_KEY]
    while len(candidates) > cap:
        victim = None
        for key in candidates:
            lock = _SESSION_LOCKS.get(key)
            if lock is not None and lock.locked():
                continue
            victim = key
            break
        if victim is None:
            return
        candidates.remove(victim)
        logger.info("Closing idle MCP session %s (cap %d)", victim, cap)
        await _drop_session(victim)


async def _ensure_browser():
    """Create the calling session's browser/page if it does not exist yet.

    The caller MUST already hold ``_get_session_lock()``: every browser-touching
    tool wraps its whole body in that lock, and acquiring it again here would
    deadlock (asyncio.Lock is not reentrant).
    """
    import logging
    logger = logging.getLogger("shadow-web.mcp")

    session = _sess()
    # Reclaim browsers from sessions past the cap before opening another one.
    await _enforce_session_cap()
    # Recreate if page was closed (e.g. browser crash)
    if "page" in session:
        try:
            if session["page"].is_closed():
                logger.warning("Page closed, recreating browser session")
                await _close_browser_resources()
        except Exception:
            logger.warning("Page check failed, recreating browser session")
            await _close_browser_resources()

    if "playwright" not in session:
        requested = preferred_browser_engine("camoufox")
        if requested not in ("camoufox", "chromium"):
            requested = "camoufox"
        browser = None
        browser_type = requested
        browser_fallback = False

        if requested == "camoufox":
            try:
                from camoufox import AsyncCamoufox

                browser = await AsyncCamoufox(headless=True).start()
                browser_type = "camoufox"
                logger.info("Browser: camoufox (Firefox)")
            except Exception as e:
                if not allow_chromium_fallback():
                    raise RuntimeError(camoufox_unavailable_message(e)) from e
                logger.warning(
                    "Camoufox unavailable (%s), falling back to Chromium "
                    "(SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK=1)",
                    e,
                )
                from playwright.async_api import async_playwright

                pw = await async_playwright().start()
                browser = await pw.chromium.launch(headless=True)
                browser_type = "chromium"
                browser_fallback = True
                session["playwright_driver"] = pw
        else:
            from playwright.async_api import async_playwright

            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            browser_type = "chromium"
            logger.info("Browser: chromium")
            session["playwright_driver"] = pw

        context = await browser.new_context(locale="en-US")
        page = await context.new_page()
        session["playwright"] = browser
        session["browser"] = browser
        session["context"] = context
        session["page"] = page
        session["browser_type"] = browser_type
        session["requested_browser"] = requested
        session["browser_fallback"] = browser_fallback


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
    session = _sess()
    res = {
        "url": shadow.last_url,
        "title": title,
        "interaction_mode": shadow.interaction_mode,
        "webmcp_available": shadow.webmcp.available,
        "webmcp_tools": [tool.to_dict() for tool in shadow.webmcp.tools],
        "action_count": len(shadow.action_map),
        "page_class": getattr(shadow, "page_class", "Static"),
        "page_class_reason": getattr(shadow, "page_class_reason", ""),
        "browser_type": session.get("browser_type") or "unknown",
        "requested_browser": session.get("requested_browser")
        or session.get("browser_type")
        or "unknown",
        "browser_fallback": bool(session.get("browser_fallback")),
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


def _build_lifespan():
    """FastMCP lifespan whose shutdown closes every session's browser.

    ``_close_all_sessions`` existed but was never wired to anything, so each MCP
    client that disconnected left a full browser process running for the life of
    the server.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app):
        try:
            yield {}
        finally:
            await _close_all_sessions()

    return _lifespan


def create_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError(
            "MCP support requires the 'mcp' package. Install with: pip install shadow-web[mcp]"
        ) from exc

    mcp = FastMCP("shadow-web", lifespan=_build_lifespan())
    # Lets _current_session_key() reach the request-scoped session from
    # module-level helpers that have no closure over the app.
    global _mcp_app
    _mcp_app = mcp

    @mcp.tool()
    def compress_html(html: str) -> dict:
        """Compress raw HTML into clean markup, flat action map, and semantic groups."""
        if len(html) > _MAX_HTML_CHARS:
            raise ValueError(
                f"html too large: {len(html)} chars, max {_MAX_HTML_CHARS}"
            )
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
        async with _get_session_lock():
            shadow = _get_shadow_page()
            fmt = format if format in ("json", "terse", "xml") else "terse"
            return await _run_shadow_query(shadow, query, fmt=fmt)  # type: ignore[arg-type]

    @mcp.tool()
    def shadow_grep_html(html: str, query: str, format: str = "terse") -> dict | str:
        """Run shadow_grep on raw HTML without a browser session."""
        if len(html) > _MAX_HTML_CHARS:
            raise ValueError(
                f"html too large: {len(html)} chars, max {_MAX_HTML_CHARS}"
            )
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
        async with _get_session_lock():
            shadow = _get_shadow_page()
            return await _run_shadow_query(shadow, query, fmt="json")

    @mcp.tool()
    async def navigate(url: str, capture_mode: str = "auto", detail: str = "terse") -> dict:
        """Open URL in Playwright and build grouped XML Action Map snapshot.

        Only http/https targets on the public internet are allowed (no file://,
        loopback, link-local or cloud-metadata addresses); see
        SHADOW_WEB_ALLOW_PRIVATE_URLS for the local-development opt-out.

        detail: minimal | terse | xml | full
        """
        from shadow_web.browser_use import AsyncShadowPage

        async with _get_session_lock():
            validate_navigation_url(url)
            await _ensure_browser()
            session = _sess()
            page = session["page"]
            heal_url = os.environ.get("SHADOW_WEB_HEAL_URL")
            mode = capture_mode if capture_mode in ("dom", "a11y", "dual", "auto") else "auto"
            try:
                await page.goto(url, wait_until="domcontentloaded")
                # Chromium follows redirects on its own: re-check where we landed.
                assert_navigation_landed(page.url)
                shadow = AsyncShadowPage(page, heal_api_url=heal_url, capture_mode=mode)
                clean_html, xml_map = await shadow.refresh()
            except Exception:
                # A failed navigate must not leave a stale page/action map behind:
                # click(sid)/snapshot would then act on a document the caller never
                # asked for, with sids that no longer match the DOM.
                session.pop("shadow_page", None)
                session.pop("clean_html", None)
                session.pop("content_index_html", None)
                raise
            session["shadow_page"] = shadow
            session["clean_html"] = clean_html

            title = await page.title()
            detail_mode = detail if detail in ("minimal", "terse", "xml", "full") else "terse"
            return _format_mcp_response(shadow, clean_html, xml_map, detail=detail_mode, title=title)

    @mcp.tool()
    async def web_search(query: str) -> dict:
        """Search the web via Brave Search (Yahoo fallback). No API keys required."""
        import urllib.parse
        from shadow_web.browser_use import AsyncShadowPage

        async with _get_session_lock():
            session = _sess()
            await _ensure_browser()
            page = session["page"]

            encoded_query = urllib.parse.quote_plus(query)
            heal_url = os.environ.get("SHADOW_WEB_HEAL_URL")
            tried_engines: list[str] = []

            for engine_name, url_template, exclude_domains in _SEARCH_ENGINES:
                tried_engines.append(engine_name)
                url = url_template.format(query=encoded_query)

                try:
                    validate_navigation_url(url)
                    await page.goto(url, wait_until="domcontentloaded")
                    assert_navigation_landed(page.url)
                except Exception as exc:  # noqa: BLE001 - try the next engine
                    logger.warning("web_search: %s navigation refused (%s)", engine_name, exc)
                    continue
                await page.wait_for_timeout(2000)

                shadow = AsyncShadowPage(page, heal_api_url=heal_url, capture_mode="auto")
                await shadow.refresh()
                results = _extract_search_results(shadow.action_map, exclude_domains)
                if not results:
                    continue

                session["shadow_page"] = shadow
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
        async with _get_session_lock():
            shadow = _get_shadow_page()
            clean_html, xml_map = await shadow.refresh(diff=diff)
            _sess()["clean_html"] = clean_html
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
        async with _get_session_lock():
            shadow = _get_shadow_page()
            await shadow.click(sid)
            return {"ok": True, "sid": sid, "url": shadow.last_url}

    @mcp.tool()
    async def fill(sid: str, value: str) -> dict:
        """Fill input by data-sid on the current page."""
        async with _get_session_lock():
            shadow = _get_shadow_page()
            await shadow.fill(sid, value)
            return {"ok": True, "sid": sid, "url": shadow.last_url}

    @mcp.tool()
    def compress_html_to_xml(html: str, url: str = "", title: str = "") -> str:
        """Return grouped XML action map for raw HTML (no browser)."""
        _bound_html(html)
        _, _, groups = process_html(html)
        return generate_grouped_xml_map(url or "about:blank", title or "Page", groups)

    def _bound_html(html: str) -> None:
        if len(html) > _MAX_HTML_CHARS:
            raise ValueError(
                f"html too large: {len(html)} chars, max {_MAX_HTML_CHARS}"
            )

    def _bound_rows(max_rows: int) -> int:
        # max_rows <= 0 means "no caller limit" - it must still respect the
        # process-wide hard cap, otherwise the documented cap is bypassable.
        if max_rows <= 0:
            return _MAX_ROWS
        return min(max_rows, _MAX_ROWS)

    @mcp.tool()
    def schema_table(html: str, max_rows: int = 50) -> list[dict]:
        """Extract structured table data from HTML: columns, types, and rows.

        max_rows: cap rows per table (default 50). Set 0 for no limit.
        """
        from shadow_web.schema_snap import parse_tables
        _bound_html(html)
        return parse_tables(html, max_rows=_bound_rows(max_rows))

    @mcp.tool()
    def schema_form(html: str) -> list[dict]:
        """Extract form schema from HTML: action, method, fields with types and validation."""
        from shadow_web.schema_snap import parse_forms
        _bound_html(html)
        return parse_forms(html)

    @mcp.tool()
    def schema_list(html: str) -> list[dict]:
        """Extract lists from HTML: unordered, ordered, or standalone select with items."""
        from shadow_web.schema_snap import parse_lists
        _bound_html(html)
        return parse_lists(html)

    @mcp.tool()
    def schema_page(html: str, max_rows: int = 50) -> dict:
        """Extract ALL structured data (tables, forms, lists) from HTML at once.

        max_rows: cap rows per table (default 50). Set 0 for no limit.
        """
        from shadow_web.schema_snap import parse_page
        _bound_html(html)
        return parse_page(html, max_rows=_bound_rows(max_rows))

    @mcp.tool()
    def get_page_html(max_chars: int = 50000) -> str:
        """Return clean HTML from the current browser session.

        max_chars: truncate output (default 50000). Set 0 for full HTML — may be huge.
        Requires navigate(url) or snapshot() first.
        """
        session = _sess()
        if "clean_html" not in session:
            raise RuntimeError("No page loaded. Call navigate(url) or snapshot() first.")
        html = session["clean_html"]
        if max_chars > 0 and len(html) > max_chars:
            return html[:max_chars] + f"\n<!-- truncated: {len(html)} chars total, returned {max_chars} -->"
        return html

    @mcp.tool()
    async def schema_session(max_rows: int = 50) -> dict:
        """Extract structured data from the current browser session (tables, forms, lists).

        max_rows: cap rows per table (default 50). Set 0 for no limit.
        Requires navigate(url) or snapshot() first.
        """
        from shadow_web.schema_snap import parse_page

        async with _get_session_lock():
            session = _sess()
            if "clean_html" not in session:
                raise RuntimeError("No page loaded. Call navigate(url) or snapshot() first.")
            return parse_page(session["clean_html"], max_rows=_bound_rows(max_rows))

    @mcp.tool()
    def schema_json(html: str, table_index: int = 0, max_rows: int = 50) -> list[dict]:
        """Export HTML table as JSON records: [{"Name": "Alice", "Age": 30}, ...].

        table_index: which <table> on the page (default 0). max_rows: cap rows (0 = all).
        """
        from shadow_web.schema_snap import export_table_json
        _bound_html(html)
        return export_table_json(html, table_index=table_index, max_rows=_bound_rows(max_rows))

    @mcp.tool()
    def schema_csv(html: str, table_index: int = 0, max_rows: int = 50) -> str:
        """Export HTML table as CSV: "Name,Age,Email\nAlice,30,alice@...".

        table_index: which <table> on the page (default 0). max_rows: cap rows (0 = all).
        """
        from shadow_web.schema_snap import export_table_csv
        _bound_html(html)
        return export_table_csv(html, table_index=table_index, max_rows=_bound_rows(max_rows))

    @mcp.tool()
    async def schema_session_json(table_index: int = 0, max_rows: int = 50) -> list[dict]:
        """Export table from current browser session as JSON records. Requires navigate/snapshot first."""
        from shadow_web.schema_snap import export_table_json

        async with _get_session_lock():
            session = _sess()
            if "clean_html" not in session:
                raise RuntimeError("No page loaded. Call navigate(url) or snapshot() first.")
            return export_table_json(session["clean_html"], table_index=table_index, max_rows=_bound_rows(max_rows))

    @mcp.tool()
    async def schema_session_csv(table_index: int = 0, max_rows: int = 50) -> str:
        """Export table from current browser session as CSV. Requires navigate/snapshot first."""
        from shadow_web.schema_snap import export_table_csv

        async with _get_session_lock():
            session = _sess()
            if "clean_html" not in session:
                raise RuntimeError("No page loaded. Call navigate(url) or snapshot() first.")
            return export_table_csv(session["clean_html"], table_index=table_index, max_rows=_bound_rows(max_rows))

    @mcp.tool()
    async def form_fill_plan(
        profile: str,
        url: str = "",
        auto_submit: bool = False,
        form_index: int = 0,
    ) -> dict:
        """Build AgentOps form fill plan from profile JSON. Optional url navigates first.

        profile: JSON object with email, name, company, role, phone (unknown keys warned).
        auto_submit: default False. Set True only when the caller explicitly wants
        the plan to include a submit step (submit is a side effect on a live site).
        Returns plan with auto_fill / ask / handoff steps and profile_validation.
        """
        from shadow_web.browser_use import AsyncShadowPage
        from shadow_web.form_fill import plan_from_session

        async with _get_session_lock():
            try:
                profile_dict = json.loads(profile)
            except json.JSONDecodeError as exc:
                raise ValueError(f"profile must be valid JSON: {exc}") from exc

            if not isinstance(profile_dict, dict):
                raise ValueError("profile must be a JSON object")

            if url:
                validate_navigation_url(url)
                session = _sess()
                await _ensure_browser()
                page = session["page"]
                await page.goto(url, wait_until="domcontentloaded")
                assert_navigation_landed(page.url)
                heal_url = os.environ.get("SHADOW_WEB_HEAL_URL")
                shadow = AsyncShadowPage(page, heal_api_url=heal_url, capture_mode="auto")
                clean_html, _ = await shadow.refresh()
                session["shadow_page"] = shadow
                session["clean_html"] = clean_html
            else:
                shadow = _get_shadow_page()
                if not shadow.clean_html:
                    await shadow.refresh()
                clean_html = shadow.clean_html

            plan = plan_from_session(
                url=shadow.last_url,
                clean_html=clean_html,
                action_map=shadow.action_map,
                profile=profile_dict,
                page_class=shadow.page_class,
                page_class_reason=shadow.page_class_reason,
                form_index=form_index,
                auto_submit=auto_submit,
            )
            return {
                "profile_validation": plan.profile_validation,
                "plan": plan.to_dict(),
            }

    @mcp.tool()
    async def form_fill_execute(
        plan: str = "",
        answers: str = "{}",
        profile: str = "",
        multi_step: bool = False,
        max_steps: int = 3,
        validate: bool = True,
        auto_submit: bool = False,
    ) -> dict:
        """Execute form fill plan. Use answers JSON for ask steps. Returns status + filled/handoffs/questions.

        status: completed | questions_pending | handoff | validation_error
        multi_step: wizard loop (requires profile JSON). Ignores plan when true.
        auto_submit: default False. Submitting is a side effect on a live site, so
        it must be requested explicitly. At most one submit per URL is performed,
        and a plan with unanswered questions never submits.
        """
        from shadow_web.form_fill import (
            apply_question_answers,
            execute_form_fill_plan_async,
            execute_form_fill_plan_multi_step_async,
            plan_from_dict,
        )

        async with _get_session_lock():
            try:
                answers_dict = json.loads(answers or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"answers must be valid JSON: {exc}") from exc

            shadow = _get_shadow_page()

            if multi_step:
                if not profile:
                    raise ValueError("profile JSON required when multi_step=true")
                try:
                    profile_dict = json.loads(profile)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"profile must be valid JSON: {exc}") from exc
                return await execute_form_fill_plan_multi_step_async(
                    shadow,
                    profile_dict,
                    answers=answers_dict,
                    max_steps=max_steps,
                    validate=validate,
                    auto_submit=auto_submit,
                )

            if not plan:
                raise ValueError("plan JSON required when multi_step=false")

            try:
                plan_dict = json.loads(plan)
            except json.JSONDecodeError as exc:
                raise ValueError(f"plan must be valid JSON: {exc}") from exc

            plan_obj = plan_from_dict(plan_dict.get("plan", plan_dict))
            # Guard against executing a stale or foreign plan: the plan records the
            # URL it was built for. If it names a different page than the current
            # session, filling+submitting could target the wrong site (prompt
            # injection on a visited page is the threat model).
            plan_url = (plan_obj.url or "").strip()
            current_url = (getattr(shadow, "last_url", "") or "").strip()
            if plan_url and current_url and plan_url != current_url:
                raise ValueError(
                    "Plan was built for a different page "
                    f"(plan url={plan_url!r}, current url={current_url!r}). "
                    "Refuse to execute: re-build the plan for the current page."
                )
            if answers_dict:
                apply_question_answers(plan_obj, answers_dict)

            return await execute_form_fill_plan_async(shadow, plan_obj, validate=validate)

    @mcp.tool()
    async def content_outline(max_tokens: int = 600, offset: int = 0) -> str:
        """Build a terse content outline from the current browser session.

        Returns block IDs (p0, p1, …) with heading paths and estimated token
        counts. Use content_blocks() to fetch full text of selected blocks.
        If the summary includes next=N, call again with offset=N.
        The summary reports text coverage, extraction mode, and retained price
        signals. Requires navigate() first. Excludes nav, footer, aside, form,
        and table.
        """
        from shadow_web.content_index import outline_text

        async with _get_session_lock():
            budget = max(1, min(int(max_tokens or 600), _MAX_OUTLINE_TOKENS))
            return outline_text(
                _get_content_index_blocks(),
                max_tokens=budget,
                offset=offset,
                quality_data=_get_content_index_quality(),
            )

    @mcp.tool()
    async def content_blocks(ids: str, max_tokens: int = 2000) -> dict:
        """Fetch content blocks from the current page by comma-separated IDs.

        Requires content_outline() first. Example IDs: "p1,p3,p5".
        Returns block_id → text, bounded by max_tokens total.
        """
        from shadow_web.content_index import fetch

        async with _get_session_lock():
            block_ids = [i.strip() for i in ids.split(",") if i.strip()][:_MAX_BLOCK_IDS]
            budget = max(1, min(int(max_tokens or 2000), _MAX_BLOCK_TOKENS))
            return fetch(
                _get_content_index_blocks(),
                block_ids,
                max_tokens=budget,
            )

    @mcp.tool()
    async def agent_run(
        goal: str,
        max_steps: int = 20,
        max_tokens: int = 15000,
        model: str = "",
        browser: str = "camoufox",
        session: str = "",
    ) -> dict:
        """Thick tool: run a bounded browser TASK server-side and return one JSON result.

        Use for: answer a question, find a fact, short interactive flow.
        Do NOT use for: full-site / large-catalog scrapes — those stay on thin tools
        (navigate, schema_session, content_outline with offset).

        Envelope always includes stop_reason, complete, truncated, and optional next_hint.
        Requires: pip install "shadow-web[agent]" (+ LLM API key env or .env).
        Owns its own browser; does not share the thin-tool MCP session.
        Preflight: fails closed if no API key in this process (pass via env/.env;
        Hermes often strips secrets — set DEEPSEEK_API_KEY in MCP server env).
        """
        import asyncio

        from shadow_web.runtime_env import load_dotenv_files, missing_llm_key_message

        load_dotenv_files()

        goal_text = (goal or "").strip()
        if not goal_text:
            return {
                "ok": False,
                "answer": None,
                "error": "goal is required",
                "stop_reason": "invalid",
                "complete": False,
                "truncated": False,
            }

        if not (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("SHADOW_WEB_LLM_API_KEY")
        ):
            return {
                "ok": False,
                "answer": None,
                "error": missing_llm_key_message(),
                "stop_reason": "missing_api_key",
                "complete": False,
                "truncated": False,
            }

        try:
            from shadow_web.agent.loop import DEFAULT_MAX_TOKENS, run_agent_task
        except ImportError as exc:
            return {
                "ok": False,
                "answer": None,
                "error": (
                    "agent_run requires the agent extra: "
                    'pip install "shadow-web[agent]" '
                    f"({exc})"
                ),
                "stop_reason": "missing_dependency",
                "complete": False,
                "truncated": False,
            }

        steps = max(1, min(int(max_steps or 20), 40))
        try:
            token_budget = int(max_tokens)
        except (TypeError, ValueError):
            token_budget = DEFAULT_MAX_TOKENS
        if token_budget < 0:
            token_budget = DEFAULT_MAX_TOKENS
        # Cap hard so MCP clients cannot request unbounded bulk runs.
        if token_budget == 0:
            token_budget = DEFAULT_MAX_TOKENS
        token_budget = min(token_budget, 50000)

        model_id = (model or "").strip() or os.environ.get(
            "SHADOW_WEB_LLM_MODEL", "deepseek-v4-flash"
        )
        raw_engine = (browser or "camoufox").strip().lower() or "camoufox"
        try:
            from shadow_web.agent.session import normalize_browser_engine

            engine = normalize_browser_engine(raw_engine)
        except (ImportError, ValueError) as exc:
            return {
                "ok": False,
                "answer": None,
                "error": f"invalid browser engine {raw_engine!r}: {exc}",
                "stop_reason": "invalid",
                "complete": False,
                "truncated": False,
            }
        session_name = (session or "").strip() or None

        def _run():
            return run_agent_task(
                goal_text,
                model=model_id,
                max_steps=steps,
                max_tokens=token_budget,
                browser_engine=engine,
                session=session_name,
            )

        # Only one agent run at a time per process: each owns a full browser, so
        # unbounded concurrency is a resource-exhaustion vector.
        async with _get_agent_semaphore():
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(_run), timeout=_AGENT_RUN_TIMEOUT_SEC
                )
            except asyncio.TimeoutError:
                return {
                    "ok": False,
                    "answer": None,
                    "error": (
                        f"agent run exceeded the wall-clock limit "
                        f"({_AGENT_RUN_TIMEOUT_SEC:.0f}s)"
                    ),
                    "stop_reason": "timeout",
                    "complete": False,
                    "truncated": True,
                }
            except Exception as exc:  # noqa: BLE001 - the envelope is the contract
                logger.warning("agent_run failed: %s", exc, exc_info=True)
                return {
                    "ok": False,
                    "answer": None,
                    "error": str(exc) or exc.__class__.__name__,
                    "stop_reason": "error",
                    "complete": False,
                    "truncated": False,
                }
        return result.to_dict()

    return mcp


def main() -> None:
    mcp = create_mcp_server()
    mcp.run()


if __name__ == "__main__":
    main()
