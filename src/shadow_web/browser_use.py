"""
Async ShadowPage wrapper for asynchronous frameworks like browser-use.
Provides the same DOM capture, semantic grouping, and self-healing logic but fully async.
"""

from __future__ import annotations

import logging
import requests
import aiohttp
from typing import Any, Dict, List, Optional, Tuple

from shadow_web.compressor import process_html, generate_grouped_xml_map
from shadow_web.dom_capture import _INTERACT_SCRIPT
from shadow_web.a11y_capture import CaptureMode, acapture_page, ainteract_by_a11y_binding, detect_page_class
from shadow_web.heal_local import HealCache, score_candidate, rank_candidates, HEAL_THRESHOLD, _COLLECT_CANDIDATES_SCRIPT
from shadow_web.query import QueryResult, shadow_grep
from shadow_web.verified_heal import averify_selector_on_page
from shadow_web.webmcp import (
    WebMcpSnapshot,
    adetect_webmcp,
    aexecute_webmcp_tool,
    generate_webmcp_xml_map,
    webmcp_tools_to_action_map,
)
from shadow_web.diff import PageDiff, PageSnapshot, build_snapshot, compute_page_diff, diff_terse, generate_diff_xml

logger = logging.getLogger(__name__)

class AsyncShadowPage:
    def __init__(
        self,
        page: Any,
        heal_api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        prefer_webmcp: bool = True,
        capture_mode: CaptureMode = "auto",
        verify_heal: bool = True,
    ):
        """
        Wraps an async Playwright Page instance.

        Args:
            page: Async Playwright Page object.
            heal_api_url: Endpoint for the self-healing service.
            api_key: Shadow Web API key.
            prefer_webmcp: Use WebMCP tools when the page exposes them.
            capture_mode: ``dom`` | ``a11y`` | ``dual`` | ``auto`` (a11y supplement for closed shadow).
            verify_heal: Verify selectors before caching healed results.
        """
        self.page = page
        self.heal_api_url = heal_api_url
        self.api_key = api_key
        self.prefer_webmcp = prefer_webmcp
        self.capture_mode: CaptureMode = capture_mode
        self.verify_heal = verify_heal

        # State
        self.clean_html: str = ""
        self.action_map: List[Dict[str, Any]] = []
        self.action_groups: List[Dict[str, Any]] = []
        self.xml_map: str = ""
        self.last_url: str = ""
        self.bindings: Dict[str, Dict[str, Any]] = {}
        self.capture_stats: Dict[str, int] = {}
        self.interaction_mode: str = "action_map"
        self.webmcp: WebMcpSnapshot = WebMcpSnapshot(available=False)
        self._baseline: Optional[PageSnapshot] = None
        self.last_diff: Optional[PageDiff] = None
        self.full_xml_map: str = ""

        self.heal_cache = HealCache()
        self.healed_selectors: Dict[str, str] = {}
        
        self.page_class: str = "Static"
        self.page_class_reason: str = ""

    async def refresh(self, diff: bool = False) -> Tuple[str, str]:
        """Captures flattened DOM asynchronously, processes HTML and builds Action Map."""
        current_url = self.page.url
        if self._baseline and self._baseline.url != current_url:
            self._baseline = None
        if self.last_url and self.last_url != current_url:
            self.healed_selectors.clear()

        self.last_url = current_url
        title = await self.page.title()

        self.webmcp = await adetect_webmcp(self.page) if self.prefer_webmcp else WebMcpSnapshot(available=False)
        if self.webmcp.available:
            self.interaction_mode = "webmcp"
            self.action_map = webmcp_tools_to_action_map(self.webmcp.tools)
            self.action_groups = [{"name": "WebMCP Tools", "elements": self.action_map}]
            self.full_xml_map = generate_webmcp_xml_map(self.last_url, title, self.webmcp.tools)
            self.bindings = {}
            self.capture_stats = {"webmcp_tools": self.webmcp.count}
            self.clean_html = ""
            self.page_class = "WebMCP"
            self.page_class_reason = "Page exposes WebMCP tools natively."
        else:
            self.interaction_mode = "action_map"
            flattened = await acapture_page(self.page, mode=self.capture_mode)
            self.bindings = flattened.bindings
            self.capture_stats = flattened.stats
            self.clean_html, self.action_map, self.action_groups = process_html(flattened.html)
            
            # SPA Auto-retry logic
            if len(self.action_map) == 0 and len(flattened.html) > 1000 and self.capture_mode == "auto":
                logger.info("[Shadow Web Async] SPA page suspected (0 actions). Waiting for networkidle/loading...")
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                flattened = await acapture_page(self.page, mode=self.capture_mode)
                self.bindings = flattened.bindings
                self.capture_stats = flattened.stats
                self.clean_html, self.action_map, self.action_groups = process_html(flattened.html)
                
            self.full_xml_map = generate_grouped_xml_map(self.last_url, title, self.action_groups)
            
            # Classify page layout and capture details
            self.page_class, self.page_class_reason = detect_page_class(
                self.last_url,
                title,
                flattened.html,
                self.capture_stats,
                len(self.action_map)
            )

        current_snapshot = build_snapshot(
            self.last_url,
            title,
            self.interaction_mode,
            self.action_map,
            self.action_groups,
        )
        page_diff = compute_page_diff(self._baseline, current_snapshot)
        self.last_diff = page_diff

        if diff and self._baseline and self._baseline.url == current_url:
            self.xml_map = generate_diff_xml(page_diff, full_xml=self.full_xml_map)
        else:
            self.xml_map = self.full_xml_map

        self._baseline = current_snapshot
        return self.clean_html, self.xml_map

    async def execute_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        result = await aexecute_webmcp_tool(self.page, name, arguments)
        await self.refresh()
        return result

    async def execute_tool_by_sid(self, sid: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        action = self.get_action_by_sid(sid)
        if not action or action.get("type") != "webmcp_tool":
            raise ValueError(f"Shadow ID {sid} is not a WebMCP tool")
        tool_name = action.get("tool_name")
        if not tool_name:
            raise ValueError(f"Shadow ID {sid} has no tool_name")
        return await self.execute_tool(tool_name, arguments)

    async def query(self, q: str, *, fmt: str = "result") -> QueryResult | str | List[Dict[str, Any]]:
        """Filter the current Action Map with shadow_grep."""
        if not self.action_map:
            await self.refresh()
        title = await self.page.title()
        result = shadow_grep(
            self.action_map,
            q,
            groups=self.action_groups,
            url=self.last_url,
            title=title,
        )
        if fmt == "list":
            return result.matches
        if fmt == "terse":
            return result.terse()
        if fmt == "xml":
            return result.xml(url=self.last_url, title=title)
        return result

    def diff_terse(self) -> str:
        """Return the latest page diff as compact text."""
        if not self.last_diff:
            return "# diff: no snapshot yet"
        return diff_terse(self.last_diff)

    async def list_webmcp_tools(self) -> WebMcpSnapshot:
        """Refresh WebMCP detection without rebuilding the full Action Map."""
        self.webmcp = await adetect_webmcp(self.page)
        return self.webmcp

    def get_action_by_sid(self, sid: str) -> Optional[Dict[str, Any]]:
        """Finds action metadata from the current Action Map."""
        for action in self.action_map:
            if action.get("id") == sid:
                return action
        return None

    def _get_binding_for_sid(self, sid: str) -> Optional[Dict[str, Any]]:
        action = self.get_action_by_sid(sid)
        if not action:
            return None
        bind_id = action.get("bind_id")
        if not bind_id:
            return None
        return self.bindings.get(bind_id)

    def _invalidate_heal_for_sid(self, sid: str) -> None:
        self.healed_selectors.pop(sid, None)
        action = self.get_action_by_sid(sid)
        if action:
            self.heal_cache.invalidate(
                self.last_url,
                action.get("label", ""),
                action.get("type", ""),
            )

    async def _wait_after_click(self, timeout_ms: int) -> None:
        """Best-effort post-click wait. Failures are non-fatal."""
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            logger.debug("[Shadow Web Async] domcontentloaded wait skipped: %s", exc)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 3000))
        except Exception as exc:
            logger.debug("[Shadow Web Async] networkidle wait skipped (non-fatal): %s", exc)

    async def _get_element_context_html(self, sid: str) -> str:
        """Extracts surrounding HTML context for the Self-Healing API."""
        try:
            action = self.get_action_by_sid(sid)
            if not action:
                return self.clean_html[:5000]

            label = action.get("label", "")
            tag = action.get("type", "button").split("[")[0]

            context = await self.page.evaluate(
                """({ label, tag }) => {
                    const candidates = Array.from(document.querySelectorAll(tag));
                    const match = candidates.find(el =>
                        (el.textContent && el.textContent.includes(label)) ||
                        (el.placeholder && el.placeholder.includes(label)) ||
                        (el.value && el.value.includes(label)) ||
                        (el.getAttribute("aria-label") && el.getAttribute("aria-label").includes(label))
                    );
                    if (match) {
                        return match.parentElement
                            ? match.parentElement.outerHTML
                            : match.outerHTML;
                    }
                    return document.body
                        ? document.body.innerHTML.substring(0, 5000)
                        : "";
                }""",
                {"label": label, "tag": tag},
            )
            return context or self.clean_html[:5000]
        except Exception as exc:
            logger.warning("[Shadow Web Async] Failed to extract heal context: %s", exc)
            return self.clean_html[:3000]

    async def _attempt_local_heal(self, sid: str) -> Optional[str]:
        action = self.get_action_by_sid(sid)
        if not action:
            return None

        cached = self.heal_cache.get(self.last_url, action.get("label", ""), action.get("type", ""))
        if cached:
            if self.verify_heal and not await averify_selector_on_page(
                self.page,
                cached,
                action.get("label", ""),
                action.get("type", ""),
            ):
                self.heal_cache.invalidate(
                    self.last_url,
                    action.get("label", ""),
                    action.get("type", ""),
                )
            else:
                logger.info("[Shadow Web Async] Local heal (cache) for ID %s: '%s'", sid, cached)
                self.healed_selectors[sid] = cached
                return cached

        base_tag = action.get("type", "button").split("[")[0].lower()
        raw_candidates = await self.page.evaluate(
            _COLLECT_CANDIDATES_SCRIPT,
            {"tag": base_tag, "label": action.get("label", "")},
        )
        candidates = rank_candidates(action.get("label", ""), raw_candidates)

        for candidate in candidates:
            confidence = float(candidate.get("score", 0))
            selector = candidate.get("selector")
            if not selector or confidence < HEAL_THRESHOLD:
                continue
            if self.verify_heal and not await averify_selector_on_page(
                self.page,
                selector,
                action.get("label", ""),
                action.get("type", ""),
            ):
                continue
            logger.info(
                "[Shadow Web Async] Local heal (local, %.2f) for ID %s: '%s'",
                confidence,
                sid,
                selector,
            )
            self.healed_selectors[sid] = selector
            self.heal_cache.set(self.last_url, action.get("label", ""), action.get("type", ""), selector)
            return selector

        return None

    async def _attempt_api_heal(self, sid: str, original_selector: str, error_msg: str) -> str:
        """Queries the Self-Healing API asynchronously to find a recovered CSS selector."""
        if not self.heal_api_url:
            raise RuntimeError(
                f"Element {original_selector} not found and no self-healing API URL configured. "
                f"Error: {error_msg}"
            )

        action = self.get_action_by_sid(sid)
        if not action:
            raise RuntimeError(f"Shadow ID {sid} not found in current Action Map. Cannot heal.")

        logger.info("[Shadow Web Async] LLM heal for ID %s (local heal below threshold)", sid)

        context_html = await self._get_element_context_html(sid)

        payload = {
            "broken_selector": original_selector,
            "context_html": context_html,
            "action_label": action.get("label", ""),
            "action_type": action.get("type", ""),
            "verify": self.verify_heal,
        }

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.heal_api_url, json=payload, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        new_selector = data.get("selector")
                        verified = data.get("verified", True)
                        if new_selector and (verified or not self.verify_heal):
                            if self.verify_heal and not await averify_selector_on_page(
                                self.page,
                                new_selector,
                                action.get("label", ""),
                                action.get("type", ""),
                            ):
                                raise RuntimeError(
                                    f"API heal selector failed client verification for ID {sid}: {new_selector}"
                                )
                            logger.info("[Shadow Web Async] LLM heal success for ID %s: '%s'", sid, new_selector)
                            self.healed_selectors[sid] = new_selector
                            self.heal_cache.set(
                                self.last_url,
                                action.get("label", ""),
                                action.get("type", ""),
                                new_selector,
                            )
                            return new_selector
                    
                    response_text = await response.text()
                    logger.error(
                        "[Shadow Web Async] LLM heal bad response for ID %s: status=%s body=%s",
                        sid,
                        response.status,
                        response_text[:500],
                    )
                    raise RuntimeError(f"API returned status {response.status}: {response_text}")
        except Exception as exc:
            logger.error("[Shadow Web Async] LLM heal request failed for ID %s: %s", sid, exc)
            raise RuntimeError(f"LLM heal request failed for ID {sid}: {exc}") from exc

    async def _attempt_heal(self, sid: str, original_selector: str, error_msg: str) -> str:
        local_selector = await self._attempt_local_heal(sid)
        if local_selector:
            return local_selector
        return await self._attempt_api_heal(sid, original_selector, error_msg)

    async def _execute_selector_action(
        self,
        selector: str,
        action: str,
        value: Optional[str],
        timeout_ms: int,
    ) -> None:
        if action == "click":
            await self.page.click(selector, timeout=timeout_ms)
        elif action == "fill":
            await self.page.fill(selector, value or "", timeout=timeout_ms)
        else:
            raise ValueError(f"Unknown action: {action}")

    async def _perform_action(
        self,
        sid: str,
        action: str,
        value: Optional[str] = None,
        timeout_ms: int = 5000,
    ) -> None:
        if not self.action_map:
            await self.refresh()

        action_meta = self.get_action_by_sid(sid)
        if action_meta and action_meta.get("type") == "webmcp_tool":
            if action != "click":
                raise ValueError("WebMCP tools support execute via click(sid) or execute_tool_by_sid()")
            args = {"value": value} if value else {}
            await self.execute_tool_by_sid(sid, args)
            if action == "click":
                await self._wait_after_click(timeout_ms)
            return

        binding = self._get_binding_for_sid(sid)
        if binding:
            try:
                if binding.get("source") == "a11y":
                    await ainteract_by_a11y_binding(self.page, binding, action, value=value)
                else:
                    result = await self.page.evaluate(
                        _INTERACT_SCRIPT,
                        {"path": binding.get("path"), "action": action, "value": value},
                    )
                    if not result or not result.get("ok"):
                        error = (result or {}).get("error", "unknown")
                        raise RuntimeError(f"Interaction script failed: {error}")
            except Exception as binding_error:
                self._invalidate_heal_for_sid(sid)
                try:
                    healed = await self._attempt_heal(
                        sid,
                        f"binding:{binding.get('tag')}",
                        str(binding_error),
                    )
                    await self._execute_selector_action(healed, action, value, timeout_ms)
                except Exception as heal_error:
                    logger.error(
                        "[Shadow Web Async] Heal after binding failure for ID %s: %s",
                        sid,
                        heal_error,
                    )
                    raise binding_error from heal_error
            else:
                if action == "click":
                    await self._wait_after_click(timeout_ms)
                await self.refresh()
                return

        selector = self.healed_selectors.get(sid, f'*[data-sid="{sid}"]')
        try:
            await self._execute_selector_action(selector, action, value, timeout_ms)
        except Exception as click_error:
            self._invalidate_heal_for_sid(sid)
            healed_selector = await self._attempt_heal(sid, selector, str(click_error))
            await self._execute_selector_action(healed_selector, action, value, timeout_ms)

        if action == "click":
            await self._wait_after_click(timeout_ms)
        await self.refresh()

    async def click(self, sid: str, timeout_ms: int = 5000):
        """Clicks an element asynchronously by shadow ID (data-sid)."""
        await self._perform_action(sid, "click", timeout_ms=timeout_ms)

    async def fill(self, sid: str, value: str, timeout_ms: int = 5000):
        """Fills an input asynchronously by shadow ID (data-sid)."""
        await self._perform_action(sid, "fill", value=value, timeout_ms=timeout_ms)
