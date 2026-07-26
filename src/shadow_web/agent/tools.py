"""Agent tools over ShadowPage (observe / act / verify / done)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from shadow_web.schema_snap import parse_page
from shadow_web.wrapper import ShadowPage

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Open a URL and return a terse Action Map observation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute URL to open"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snapshot",
            "description": (
                "Refresh the page observation. "
                "detail=terse for Action Map; detail=diff for post-action deltas only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "detail": {
                        "type": "string",
                        "enum": ["terse", "diff"],
                        "description": "Observation mode (default terse)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click an element by data-sid from the Action Map. Returns a page diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": {"type": "string", "description": "Action Map id (data-sid)"},
                },
                "required": ["sid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fill",
            "description": "Fill an input by data-sid. Returns a page diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": {"type": "string", "description": "Action Map id (data-sid)"},
                    "value": {"type": "string", "description": "Text to type"},
                },
                "required": ["sid", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schema_page",
            "description": "Extract tables, forms, and lists from the current page as JSON.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_rows": {
                        "type": "integer",
                        "description": "Max rows per table (default 50; 0 = unlimited)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": (
                "Finish the task with a final answer. "
                "Call this as soon as you have enough information (single-call finish for read-only)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finalAnswer": {
                        "type": "string",
                        "description": "Concise final answer for the user",
                    },
                },
                "required": ["finalAnswer"],
            },
        },
    },
]


@dataclass
class ToolResult:
    name: str
    ok: bool
    content: str
    data: Dict[str, Any] = field(default_factory=dict)
    terminal: bool = False
    final_answer: Optional[str] = None


def _action_terse(shadow: ShadowPage, limit: int = 20) -> str:
    actions = shadow.action_map[:limit]
    lines = [
        f"# page {shadow.last_url} ({len(shadow.action_map)} actions, class={getattr(shadow, 'page_class', 'Static')})",
    ]
    for action in actions:
        sid = action.get("id", "?")
        kind = action.get("type", "?")
        label = action.get("label") or action.get("placeholder") or action.get("text") or ""
        group = action.get("group", "")
        suffix = f" [{group}]" if group else ""
        lines.append(f"@{sid} {kind} {label}{suffix}".strip())
    if len(shadow.action_map) > limit:
        lines.append(f"... +{len(shadow.action_map) - limit} more")
    return "\n".join(lines)


def _observe(shadow: ShadowPage, *, diff: bool = False) -> Dict[str, Any]:
    title = ""
    try:
        title = shadow.page.title() if shadow.page else ""
    except Exception:
        title = ""
    payload: Dict[str, Any] = {
        "url": shadow.last_url,
        "title": title,
        "action_count": len(shadow.action_map),
        "page_class": getattr(shadow, "page_class", "Static"),
    }
    if diff and shadow.last_diff:
        payload["detail"] = "diff"
        payload["observation"] = shadow.diff_terse()
        payload["has_changes"] = shadow.last_diff.has_changes
    else:
        payload["detail"] = "terse"
        payload["observation"] = _action_terse(shadow)
    return payload


class ToolRouter:
    """Dispatch model tool calls onto a ShadowPage."""

    def __init__(self, shadow: ShadowPage):
        self.shadow = shadow

    def dispatch(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return ToolResult(
                name=name,
                ok=False,
                content=json.dumps({"error": f"Unknown tool: {name}"}),
            )
        try:
            return handler(arguments or {})
        except Exception as exc:
            return ToolResult(
                name=name,
                ok=False,
                content=json.dumps({"error": str(exc), "tool": name}),
            )

    def _tool_navigate(self, args: Dict[str, Any]) -> ToolResult:
        url = str(args.get("url") or "").strip()
        if not url:
            raise ValueError("url is required")
        self.shadow.page.goto(url, wait_until="domcontentloaded")
        self.shadow.refresh(diff=False)
        data = _observe(self.shadow, diff=False)
        return ToolResult(name="navigate", ok=True, content=json.dumps(data), data=data)

    def _tool_snapshot(self, args: Dict[str, Any]) -> ToolResult:
        detail = str(args.get("detail") or "terse")
        use_diff = detail == "diff"
        self.shadow.refresh(diff=use_diff)
        data = _observe(self.shadow, diff=use_diff)
        return ToolResult(name="snapshot", ok=True, content=json.dumps(data), data=data)

    def _tool_click(self, args: Dict[str, Any]) -> ToolResult:
        sid = str(args.get("sid") or "").strip()
        if not sid:
            raise ValueError("sid is required")
        self.shadow.click(sid)
        # click() already refresh()'d; last_diff is pre→post act
        data = _observe(self.shadow, diff=True)
        data["acted"] = {"tool": "click", "sid": sid}
        return ToolResult(name="click", ok=True, content=json.dumps(data), data=data)

    def _tool_fill(self, args: Dict[str, Any]) -> ToolResult:
        sid = str(args.get("sid") or "").strip()
        if not sid:
            raise ValueError("sid is required")
        if "value" not in args:
            raise ValueError("value is required")
        value = str(args["value"])
        self.shadow.fill(sid, value)
        data = _observe(self.shadow, diff=True)
        data["acted"] = {"tool": "fill", "sid": sid}
        return ToolResult(name="fill", ok=True, content=json.dumps(data), data=data)

    def _tool_schema_page(self, args: Dict[str, Any]) -> ToolResult:
        if not self.shadow.clean_html:
            self.shadow.refresh(diff=False)
        max_rows = args.get("max_rows", 50)
        try:
            max_rows_i = int(max_rows)
        except (TypeError, ValueError):
            max_rows_i = 50
        limit = None if max_rows_i <= 0 else max_rows_i
        parsed = parse_page(self.shadow.clean_html, max_rows=limit)
        data = {
            "url": self.shadow.last_url,
            "schema": parsed,
        }
        return ToolResult(name="schema_page", ok=True, content=json.dumps(data), data=data)

    def _tool_done(self, args: Dict[str, Any]) -> ToolResult:
        answer = args.get("finalAnswer")
        if answer is None:
            raise ValueError("finalAnswer is required")
        evidence: Dict[str, Any] = {
            "url": self.shadow.last_url,
            "action_count": len(self.shadow.action_map),
        }
        if self.shadow.clean_html:
            try:
                evidence["schema"] = parse_page(self.shadow.clean_html, max_rows=10)
            except Exception:
                pass
        return ToolResult(
            name="done",
            ok=True,
            content=json.dumps({"finalAnswer": str(answer), "evidence": evidence}),
            data=evidence,
            terminal=True,
            final_answer=str(answer),
        )


def parse_tool_arguments(raw: str | Dict[str, Any] | None) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid tool arguments JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return parsed
