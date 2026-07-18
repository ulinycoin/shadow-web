import logging
import requests
from typing import List, Dict, Any, Tuple, Optional, Union
from .compressor import process_html, generate_grouped_xml_map
from .dom_capture import capture_flattened_dom, interact_by_binding
from .a11y_capture import CaptureMode, capture_page, detect_page_class
from .capture_ready import CaptureReadyResult, prepare_page_for_capture
from .heal_local import HealCache, local_heal
from .query import QueryResult, shadow_grep
from .webmcp import (
    WebMcpSnapshot,
    detect_webmcp,
    execute_webmcp_tool,
    generate_webmcp_xml_map,
    webmcp_tools_to_action_map,
)
from .diff import PageDiff, PageSnapshot, build_snapshot, compute_page_diff, diff_terse, generate_diff_xml
from .verified_heal import verify_selector_on_page

logger = logging.getLogger(__name__)


class ShadowPage:
    def __init__(
        self,
        page,
        heal_api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        prefer_webmcp: bool = True,
        capture_mode: CaptureMode = "auto",
        verify_heal: bool = True,
    ):
        """
        Wraps a Playwright Page instance to enable data-sid interactions and self-healing selectors.

        Args:
            page: Playwright Page object (sync API).
            heal_api_url: Endpoint for the self-healing service (e.g. 'http://localhost:8000/v1/heal').
            api_key: Shadow Web API key for authentication.
            prefer_webmcp: When True, use WebMCP tools if the page exposes them (Chrome 145+).
            capture_mode: ``dom`` | ``a11y`` | ``dual`` | ``auto`` (a11y supplement for closed shadow).
            verify_heal: Verify selectors resolve before caching (local + API heal).
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
        self.interaction_mode: str = "action_map"  # action_map | webmcp
        self.webmcp: WebMcpSnapshot = WebMcpSnapshot(available=False)
        self._baseline: Optional[PageSnapshot] = None
        self.last_diff: Optional[PageDiff] = None
        self.full_xml_map: str = ""

        self.heal_cache = HealCache()
        self.healed_selectors: Dict[str, str] = {}
        
        self.page_class: str = "Static"
        self.page_class_reason: str = ""
        self._capture_prepared_url: str = ""
        self.capture_readiness: Optional[CaptureReadyResult] = None

    def refresh(self, diff: bool = False) -> Tuple[str, str]:
        """Captures flattened DOM (read-only), builds clean HTML and Action Map.

        Args:
            diff: When True and baseline exists for same URL, return delta XML
                  with skeleton + breadcrumbs instead of full Action Map.
        """
        current_url = self.page.url
        if self._baseline and self._baseline.url != current_url:
            self._baseline = None
        if self.last_url and self.last_url != current_url:
            self.healed_selectors.clear()

        self.last_url = current_url
        title = self.page.title()

        self.webmcp = detect_webmcp(self.page) if self.prefer_webmcp else WebMcpSnapshot(available=False)
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
            # Universal consent + content wait once per URL (skip on post-click refresh).
            if self._capture_prepared_url != current_url:
                self.capture_readiness = prepare_page_for_capture(self.page)
                self._capture_prepared_url = current_url
                if self.capture_readiness.consent_dismissed:
                    logger.info(
                        "[Shadow Web] Dismissed consent control: %s",
                        self.capture_readiness.consent_label,
                    )
                if not self.capture_readiness.ready:
                    logger.info(
                        "[Shadow Web] Capture readiness: %s (text=%s cards=%s)",
                        self.capture_readiness.reason,
                        self.capture_readiness.text_chars,
                        self.capture_readiness.card_candidates,
                    )

            flattened = capture_page(self.page, mode=self.capture_mode)
            self.bindings = flattened.bindings
            self.capture_stats = dict(flattened.stats or {})
            if self.capture_readiness is not None:
                self.capture_stats["readiness"] = self.capture_readiness.as_stats()
            self.clean_html, self.action_map, self.action_groups = process_html(flattened.html)

            # SPA Auto-retry — skip when readiness already classified a content shell.
            shell = bool(self.capture_readiness and self.capture_readiness.shell)
            if (
                not shell
                and len(self.action_map) == 0
                and len(flattened.html) > 1000
                and self.capture_mode == "auto"
            ):
                logger.info("[Shadow Web] SPA page suspected (0 actions). Waiting for networkidle/loading...")
                try:
                    self.page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                flattened = capture_page(self.page, mode=self.capture_mode)
                self.bindings = flattened.bindings
                self.capture_stats = dict(flattened.stats or {})
                if self.capture_readiness is not None:
                    self.capture_stats["readiness"] = self.capture_readiness.as_stats()
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

    def list_webmcp_tools(self) -> WebMcpSnapshot:
        """Refresh WebMCP detection without rebuilding the full Action Map."""
        self.webmcp = detect_webmcp(self.page)
        return self.webmcp

    def execute_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Execute a WebMCP tool by name."""
        result = execute_webmcp_tool(self.page, name, arguments)
        self.refresh()
        return result

    def execute_tool_by_sid(self, sid: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Execute a WebMCP tool referenced by Action Map id."""
        action = self.get_action_by_sid(sid)
        if not action or action.get("type") != "webmcp_tool":
            raise ValueError(f"Shadow ID {sid} is not a WebMCP tool")
        tool_name = action.get("tool_name")
        if not tool_name:
            raise ValueError(f"Shadow ID {sid} has no tool_name")
        return self.execute_tool(tool_name, arguments)

    def query(self, q: str, *, fmt: str = "result") -> Union[QueryResult, str, List[Dict[str, Any]]]:
        """
        Filter the current Action Map with shadow_grep.

        Args:
            q: Query string (``type:button``, ``intent:login``, ``label~/pay/i``, etc.).
            fmt: ``result`` | ``list`` | ``terse`` | ``xml``

        Returns:
            QueryResult, list of actions, terse text, or XML string depending on ``fmt``.
        """
        if not self.action_map:
            self.refresh()

        result = shadow_grep(
            self.action_map,
            q,
            groups=self.action_groups,
            url=self.last_url,
            title=self.page.title() if self.page else "",
        )
        if fmt == "list":
            return result.matches
        if fmt == "terse":
            return result.terse()
        if fmt == "xml":
            return result.xml(url=self.last_url, title=self.page.title() if self.page else "")
        return result

    def diff_terse(self) -> str:
        """Return the latest page diff as compact text."""
        if not self.last_diff:
            return "# diff: no snapshot yet"
        return diff_terse(self.last_diff)

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

    def _wait_after_click(self, timeout_ms: int) -> None:
        """Best-effort post-click wait. Failures are non-fatal (no heal/re-click)."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            logger.debug("[Shadow Web] domcontentloaded wait skipped: %s", exc)
        try:
            self.page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 3000))
        except Exception as exc:
            logger.debug("[Shadow Web] networkidle wait skipped (non-fatal): %s", exc)

    def _get_element_context_html(self, sid: str) -> str:
        """Extracts surrounding HTML context for the Self-Healing API."""
        try:
            action = self.get_action_by_sid(sid)
            if not action:
                return self.clean_html[:5000]

            label = action.get("label", "")
            tag = action.get("type", "button").split("[")[0]

            context = self.page.evaluate(
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
            logger.warning("[Shadow Web] Failed to extract heal context: %s", exc)
            return self.clean_html[:3000]

    def _attempt_local_heal(self, sid: str) -> Optional[str]:
        action = self.get_action_by_sid(sid)
        if not action:
            return None

        result = local_heal(
            self.page,
            self.last_url,
            action.get("label", ""),
            action.get("type", ""),
            cache=self.heal_cache,
            verify=self.verify_heal,
        )
        if not result:
            return None

        logger.info(
            "[Shadow Web] Local heal (%s, %.2f) for ID %s: '%s'",
            result.source,
            result.confidence,
            sid,
            result.selector,
        )
        self.healed_selectors[sid] = result.selector
        return result.selector

    def _attempt_api_heal(self, sid: str, original_selector: str, error_msg: str) -> str:
        """Queries the Self-Healing API to find a recovered CSS selector."""
        if not self.heal_api_url:
            raise RuntimeError(
                f"Element {original_selector} not found and no self-healing API URL configured. "
                f"Error: {error_msg}"
            )

        action = self.get_action_by_sid(sid)
        if not action:
            raise RuntimeError(f"Shadow ID {sid} not found in current Action Map. Cannot heal.")

        logger.info("[Shadow Web] LLM heal for ID %s (local heal below threshold)", sid)

        context_html = self._get_element_context_html(sid)

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
            response = requests.post(self.heal_api_url, json=payload, headers=headers, timeout=10)
        except requests.RequestException as exc:
            logger.error("[Shadow Web] LLM heal request failed for ID %s: %s", sid, exc)
            raise RuntimeError(f"LLM heal request failed for ID {sid}: {exc}") from exc

        if response.status_code == 200:
            data = response.json()
            new_selector = data.get("selector")
            verified = data.get("verified", True)
            if new_selector and (verified or not self.verify_heal):
                if self.verify_heal and not verify_selector_on_page(
                    self.page,
                    new_selector,
                    action.get("label", ""),
                    action.get("type", ""),
                ):
                    raise RuntimeError(
                        f"API heal selector failed client verification for ID {sid}: {new_selector}"
                    )
                logger.info("[Shadow Web] LLM heal success for ID %s: '%s'", sid, new_selector)
                self.healed_selectors[sid] = new_selector
                self.heal_cache.set(
                    self.last_url,
                    action.get("label", ""),
                    action.get("type", ""),
                    new_selector,
                )
                return new_selector

        logger.error(
            "[Shadow Web] LLM heal bad response for ID %s: status=%s body=%s",
            sid,
            response.status_code,
            response.text[:500],
        )
        raise RuntimeError(f"API returned status {response.status_code}: {response.text}")

    def _attempt_heal(self, sid: str, original_selector: str, error_msg: str) -> str:
        local_selector = self._attempt_local_heal(sid)
        if local_selector:
            return local_selector
        return self._attempt_api_heal(sid, original_selector, error_msg)

    def _execute_selector_action(
        self,
        selector: str,
        action: str,
        value: Optional[str],
        timeout_ms: int,
    ) -> None:
        if action == "click":
            self.page.click(selector, timeout=timeout_ms)
        elif action == "fill":
            self.page.fill(selector, value or "", timeout=timeout_ms)
        else:
            raise ValueError(f"Unknown action: {action}")

    def _perform_action(
        self,
        sid: str,
        action: str,
        value: Optional[str] = None,
        timeout_ms: int = 5000,
    ) -> None:
        if not self.action_map:
            self.refresh()

        action_meta = self.get_action_by_sid(sid)
        if action_meta and action_meta.get("type") == "webmcp_tool":
            if action != "click":
                raise ValueError("WebMCP tools support execute via click(sid) or execute_tool_by_sid()")
            args = {"value": value} if value else {}
            self.execute_tool_by_sid(sid, args)
            if action == "click":
                self._wait_after_click(timeout_ms)
            return

        binding = self._get_binding_for_sid(sid)
        if binding:
            try:
                interact_by_binding(self.page, binding, action, value=value)
            except Exception as binding_error:
                self._invalidate_heal_for_sid(sid)
                try:
                    healed = self._attempt_heal(
                        sid,
                        f"binding:{binding.get('tag')}",
                        str(binding_error),
                    )
                    self._execute_selector_action(healed, action, value, timeout_ms)
                except Exception as heal_error:
                    logger.error(
                        "[Shadow Web] Heal after binding failure for ID %s: %s",
                        sid,
                        heal_error,
                    )
                    raise binding_error from heal_error
                if action == "click":
                    self._wait_after_click(timeout_ms)
                self.refresh()
                return
            else:
                if action == "click":
                    self._wait_after_click(timeout_ms)
                self.refresh()
                return

        selector = self.healed_selectors.get(sid, f'*[data-sid="{sid}"]')
        try:
            self._execute_selector_action(selector, action, value, timeout_ms)
        except Exception as click_error:
            self._invalidate_heal_for_sid(sid)
            healed_selector = self._attempt_heal(sid, selector, str(click_error))
            self._execute_selector_action(healed_selector, action, value, timeout_ms)

        if action == "click":
            self._wait_after_click(timeout_ms)
        self.refresh()

    def click(self, sid: str, timeout_ms: int = 5000):
        """Clicks an element by shadow ID (data-sid), resolving via live DOM binding."""
        self._perform_action(sid, "click", timeout_ms=timeout_ms)

    def fill(self, sid: str, value: str, timeout_ms: int = 5000):
        """Fills an input by shadow ID (data-sid), resolving via live DOM binding."""
        self._perform_action(sid, "fill", value=value, timeout_ms=timeout_ms)
