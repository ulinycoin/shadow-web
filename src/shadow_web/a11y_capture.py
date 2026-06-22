"""Accessibility tree capture via CDP (closed Shadow DOM fallback)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set

from lxml import html as lxml_html

from .compressor import get_element_label
from .dom_capture import FlattenResult
from .heal_local import fuzzy_ratio

PageLike = Any

CaptureMode = Literal["dom", "a11y", "dual", "auto"]

INTERACTIVE_AX_ROLES = {
    "button",
    "link",
    "textbox",
    "searchbox",
    "combobox",
    "checkbox",
    "radio",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "tab",
    "switch",
    "slider",
    "spinbutton",
    "listbox",
    "option",
}

_ROLE_TO_TAG = {
    "button": "button",
    "link": "a",
    "textbox": "input",
    "searchbox": "input",
    "checkbox": "input",
    "radio": "input",
    "combobox": "select",
    "menuitem": "button",
    "menuitemcheckbox": "button",
    "menuitemradio": "button",
    "tab": "button",
    "switch": "button",
    "slider": "input",
    "spinbutton": "input",
    "listbox": "select",
    "option": "option",
}

_INPUT_TYPES = {
    "checkbox": "checkbox",
    "radio": "radio",
    "searchbox": "search",
    "slider": "range",
    "spinbutton": "number",
}


@dataclass
class A11yNode:
    role: str
    name: str
    backend_node_id: int
    value: str = ""


@dataclass
class A11yCaptureResult:
    nodes: List[A11yNode] = field(default_factory=list)
    supplement_html: str = ""
    bindings: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    stats: Dict[str, int] = field(default_factory=dict)


def _role_value(node: Dict[str, Any]) -> str:
    role = node.get("role") or {}
    value = role.get("value", "")
    return str(value).lower()


def _name_value(node: Dict[str, Any]) -> str:
    name = node.get("name") or {}
    return str(name.get("value") or "").strip()


def _value_text(node: Dict[str, Any]) -> str:
    value = node.get("value") or {}
    return str(value.get("value") or "").strip()


def parse_ax_nodes(raw_nodes: List[Dict[str, Any]]) -> List[A11yNode]:
    """Extract interactive AX nodes that expose a backend DOM node id."""
    parsed: List[A11yNode] = []
    seen: Set[tuple[int, str]] = set()
    for node in raw_nodes:
        if node.get("ignored"):
            continue
        role = _role_value(node)
        if role not in INTERACTIVE_AX_ROLES:
            continue
        backend_id = node.get("backendDOMNodeId")
        if not backend_id:
            continue
        name = _name_value(node) or _value_text(node)
        key = (int(backend_id), role)
        if key in seen:
            continue
        seen.add(key)
        parsed.append(
            A11yNode(
                role=role,
                name=name,
                backend_node_id=int(backend_id),
                value=_value_text(node),
            )
        )
    return parsed


def capture_a11y_interactive(page: PageLike) -> A11yCaptureResult:
    """Capture interactive nodes from Chrome Accessibility tree."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("Accessibility.enable")
    payload = cdp.send("Accessibility.getFullAXTree")
    nodes = parse_ax_nodes(payload.get("nodes") or [])
    supplement_html, bindings = build_a11y_supplement(nodes, bind_prefix="sw-a11y")
    return A11yCaptureResult(
        nodes=nodes,
        supplement_html=supplement_html,
        bindings=bindings,
        stats={"a11y_interactive": len(nodes), "a11y_bindings": len(bindings)},
    )


def build_a11y_supplement(
    nodes: List[A11yNode],
    *,
    bind_prefix: str = "sw-a11y",
    existing_labels: Optional[List[str]] = None,
    match_threshold: float = 0.85,
) -> tuple[str, Dict[str, Dict[str, Any]]]:
    """Build pseudo-HTML + bindings for AX nodes not already covered by DOM labels."""
    existing_labels = existing_labels or []
    bindings: Dict[str, Dict[str, Any]] = {}
    chunks: List[str] = []
    counter = 0

    for node in nodes:
        if existing_labels and _is_covered(node.name, existing_labels, match_threshold):
            continue
        counter += 1
        bind_id = f"{bind_prefix}-{counter}"
        tag = _ROLE_TO_TAG.get(node.role, "button")
        attrs = [f'data-sw-bind="{bind_id}"', 'data-sw-source="a11y"']
        if node.name:
            attrs.append(f'aria-label="{_escape_attr(node.name)}"')
        input_type = _INPUT_TYPES.get(node.role)
        if tag == "input" and input_type:
            attrs.append(f'type="{input_type}"')
        if tag == "a":
            attrs.append('href="#"')
        label_text = _escape_attr(node.name or node.role)
        chunks.append(f"<{tag} {' '.join(attrs)}>{label_text}</{tag}>")
        bindings[bind_id] = {
            "source": "a11y",
            "backend_node_id": node.backend_node_id,
            "tag": tag,
            "role": node.role,
            "name": node.name,
        }

    if not chunks:
        return "", bindings

    inner = "".join(chunks)
    html = f'<section data-sw-a11y-supplement="true">{inner}</section>'
    return html, bindings


def _is_covered(name: str, labels: List[str], threshold: float) -> bool:
    if not name:
        return False
    for label in labels:
        if fuzzy_ratio(name, label) >= threshold:
            return True
    return False


def labels_from_dom_html(html_text: str) -> List[str]:
    """Collect human labels from flattened DOM HTML before merge."""
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return []
    labels: List[str] = []
    for el in tree.xpath("//*[@data-sw-bind]"):
        label = get_element_label(el)
        if label:
            labels.append(label)
    return labels


def detect_page_class(
    url: str,
    title: str,
    html_content: str,
    stats: Dict[str, Any],
    action_map_len: int
) -> tuple[str, str]:
    """Analyze page structure and classify it.
    
    Returns:
        tuple[str, str]: (page_class, reason)
    """
    html_len = len(html_content) if html_content else 0
    shadow_hosts = stats.get("shadow_hosts", 0)
    cross_origin_iframes = stats.get("cross_origin_iframes", 0)
    
    # 1. Anti-bot (Cloudflare, CAPTCHA, block pages)
    lower_html = html_content.lower() if html_content else ""
    if html_len < 15000 and any(m in lower_html for m in ["captcha", "cloudflare", "bot detection", "robot check", "just a moment"]):
        return "Anti-bot", "CAPTCHA or Cloudflare protection detected (small HTML with security markers)."
        
    # 2. Auth-gated
    lower_url = url.lower() if url else ""
    if "login" in lower_url or "signin" in lower_url or "authorize" in lower_url:
        return "Auth-gated", "Redirected to a login or sign-in page."
        
    # 3. SPA (Single Page Application)
    if action_map_len == 0 and html_len > 1000:
        return "SPA", "No interactive elements detected; page might be loading or a dynamic SPA."
        
    # 4. Shadow DOM / Closed Shadow
    if shadow_hosts > 0:
        if stats.get("a11y_supplement_nodes", 0) > 0:
            return "Closed Shadow", f"Detected {shadow_hosts} shadow hosts and closed shadow nodes in a11y tree."
        return "Shadow DOM", f"Detected {shadow_hosts} open shadow hosts."
        
    # 5. Iframe-heavy
    if cross_origin_iframes > 0:
        return "Iframe-heavy", f"Detected {cross_origin_iframes} cross-origin iframes which are inaccessible to local script."
        
    # 6. Static / Standard
    return "Static", "Standard HTML page with static layout."


def needs_a11y_supplement(
    dom: FlattenResult,
    a11y: A11yCaptureResult,
    *,
    mode: CaptureMode,
) -> bool:
    if mode == "a11y":
        return True
    if mode == "dual":
        return bool(a11y.nodes)
    if mode != "auto":
        return False
    if dom.stats.get("shadow_hosts", 0) <= 0:
        return False
    dom_labels = labels_from_dom_html(dom.html)
    uncovered = sum(
        1 for node in a11y.nodes if not _is_covered(node.name, dom_labels, 0.85)
    )
    return uncovered > 0


def merge_dom_and_a11y(
    dom: FlattenResult,
    a11y: A11yCaptureResult,
    *,
    mode: CaptureMode = "dual",
) -> FlattenResult:
    """Merge DOM flatten with supplemental a11y-only interactive nodes."""
    if mode == "dom":
        return dom
    if mode == "a11y":
        html_body = a11y.supplement_html or "<body></body>"
        if not html_body.startswith("<body"):
            html_body = f"<body>{html_body}</body>"
        return FlattenResult(
            html=html_body,
            bindings=dict(a11y.bindings),
            stats={**a11y.stats, "capture_source": "a11y"},
        )

    if not needs_a11y_supplement(dom, a11y, mode=mode):
        return dom

    dom_labels = labels_from_dom_html(dom.html)
    supplement_html, supplement_bindings = build_a11y_supplement(
        a11y.nodes,
        existing_labels=dom_labels,
    )
    if not supplement_html:
        return dom

    merged_html = dom.html
    if "</body>" in merged_html:
        merged_html = merged_html.replace("</body>", f"{supplement_html}</body>", 1)
    else:
        merged_html = f"{merged_html}{supplement_html}"

    merged_stats = dict(dom.stats)
    merged_stats.update(a11y.stats)
    merged_stats["a11y_supplement_nodes"] = len(supplement_bindings)
    merged_stats["capture_source"] = "dual" if mode == "dual" else "auto"

    return FlattenResult(
        html=merged_html,
        bindings={**dom.bindings, **supplement_bindings},
        stats=merged_stats,
    )


async def acapture_a11y_interactive(page: PageLike) -> A11yCaptureResult:
    """Async: capture interactive nodes from Chrome Accessibility tree."""
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Accessibility.enable")
    payload = await cdp.send("Accessibility.getFullAXTree")
    nodes = parse_ax_nodes(payload.get("nodes") or [])
    supplement_html, bindings = build_a11y_supplement(nodes, bind_prefix="sw-a11y")
    return A11yCaptureResult(
        nodes=nodes,
        supplement_html=supplement_html,
        bindings=bindings,
        stats={"a11y_interactive": len(nodes), "a11y_bindings": len(bindings)},
    )


async def acapture_page(
    page: PageLike,
    mode: CaptureMode = "dom",
) -> FlattenResult:
    """Async unified capture entry."""
    from .dom_capture import _FLATTEN_SCRIPT

    if mode == "a11y":
        a11y = await acapture_a11y_interactive(page)
        return merge_dom_and_a11y(FlattenResult(html="<body></body>"), a11y, mode="a11y")

    raw = await page.evaluate(_FLATTEN_SCRIPT)
    dom = FlattenResult(
        html=raw.get("html") or "<body></body>",
        bindings=raw.get("bindings") or {},
        stats=raw.get("stats") or {},
    )
    if mode == "dom":
        dom.stats["capture_source"] = "dom"
        return dom

    a11y = await acapture_a11y_interactive(page)
    return merge_dom_and_a11y(dom, a11y, mode=mode)


def capture_page(
    page: PageLike,
    mode: CaptureMode = "dom",
) -> FlattenResult:
    """Unified capture entry: DOM flatten with optional a11y supplement."""
    from .dom_capture import capture_flattened_dom

    if mode == "a11y":
        a11y = capture_a11y_interactive(page)
        return merge_dom_and_a11y(FlattenResult(html="<body></body>"), a11y, mode="a11y")

    dom = capture_flattened_dom(page)
    if mode == "dom":
        dom.stats["capture_source"] = "dom"
        return dom

    a11y = capture_a11y_interactive(page)
    return merge_dom_and_a11y(dom, a11y, mode=mode)


def interact_by_a11y_binding(
    page: PageLike,
    binding: Dict[str, Any],
    action: str,
    value: Optional[str] = None,
) -> None:
    """Click or fill via CDP backendDOMNodeId (closed shadow safe)."""
    backend_id = binding.get("backend_node_id")
    if not backend_id:
        raise ValueError("a11y binding missing backend_node_id")

    cdp = page.context.new_cdp_session(page)
    resolved = cdp.send("DOM.resolveNode", {"backendNodeId": int(backend_id)})
    object_id = resolved.get("object", {}).get("objectId")
    if not object_id:
        raise RuntimeError("CDP could not resolve a11y backend node")

    if action == "click":
        fn = "function() { this.click(); return true; }"
        args: List[Dict[str, Any]] = []
    elif action == "fill":
        fn = (
            "function(v) {"
            " this.focus();"
            " this.value = v ?? '';"
            " this.dispatchEvent(new Event('input', { bubbles: true }));"
            " this.dispatchEvent(new Event('change', { bubbles: true }));"
            " return true;"
            "}"
        )
        args = [{"value": value or ""}]
    else:
        raise ValueError(f"Unknown action: {action}")

    result = cdp.send(
        "Runtime.callFunctionOn",
        {
            "objectId": object_id,
            "functionDeclaration": fn,
            "arguments": args,
            "returnByValue": True,
        },
    )
    if result.get("exceptionDetails"):
        raise RuntimeError(f"a11y {action} failed: {result['exceptionDetails']}")


async def ainteract_by_a11y_binding(
    page: PageLike,
    binding: Dict[str, Any],
    action: str,
    value: Optional[str] = None,
) -> None:
    """Async click/fill via CDP backendDOMNodeId."""
    backend_id = binding.get("backend_node_id")
    if not backend_id:
        raise ValueError("a11y binding missing backend_node_id")

    cdp = await page.context.new_cdp_session(page)
    resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": int(backend_id)})
    object_id = resolved.get("object", {}).get("objectId")
    if not object_id:
        raise RuntimeError("CDP could not resolve a11y backend node")

    if action == "click":
        fn = "function() { this.click(); return true; }"
        args: List[Dict[str, Any]] = []
    elif action == "fill":
        fn = (
            "function(v) {"
            " this.focus();"
            " this.value = v ?? '';"
            " this.dispatchEvent(new Event('input', { bubbles: true }));"
            " this.dispatchEvent(new Event('change', { bubbles: true }));"
            " return true;"
            "}"
        )
        args = [{"value": value or ""}]
    else:
        raise ValueError(f"Unknown action: {action}")

    result = await cdp.send(
        "Runtime.callFunctionOn",
        {
            "objectId": object_id,
            "functionDeclaration": fn,
            "arguments": args,
            "returnByValue": True,
        },
    )
    if result.get("exceptionDetails"):
        raise RuntimeError(f"a11y {action} failed: {result['exceptionDetails']}")


def _escape_attr(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
