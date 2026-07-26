"""Agent tools over ShadowPage (observe / act / verify / done)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from shadow_web.schema_snap import parse_page
from shadow_web.wrapper import ShadowPage

# Dense pages (catalogs, megamenus): keep observations MCP-terse sized.
DENSE_ACTION_THRESHOLD = 40
DENSE_ACTION_LIMIT = 10
DEFAULT_ACTION_LIMIT = 20
DEFAULT_OUTLINE_TOKENS = 400
DEFAULT_BLOCKS_TOKENS = 800
MAX_CONTENT_BLOCK_IDS = 5

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": (
                "Open a URL and return a bounded Action Map observation. "
                "Dense pages auto-downgrade to minimal (groups + top actions)."
            ),
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
                "detail=terse|minimal for Action Map; detail=diff for post-action deltas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "detail": {
                        "type": "string",
                        "enum": ["terse", "minimal", "diff"],
                        "description": "Observation mode (default terse; auto-minimal when dense)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shadow_query",
            "description": (
                "Filter the current Action Map before acting. "
                "Examples: type:button, intent:search, label~/nike/i, group:Nav. "
                "Use this on dense pages instead of browsing the full map."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "shadow_grep query string",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max matches to return (default 20)",
                    },
                },
                "required": ["query"],
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
            "description": (
                "Extract tables, forms, and lists from the current page as JSON. "
                "Good for static catalogs. On SPA product grids with empty schema, "
                "use content_outline instead."
            ),
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
            "name": "content_outline",
            "description": (
                "Build a terse Rendered Text Index outline (block ids p0, p1, …) "
                "from the current page HTML. Best for SPA/catalogs where products "
                "are not in HTML tables. Then ONE content_blocks(ids) or done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_tokens": {
                        "type": "integer",
                        "description": f"Outline token budget (default {DEFAULT_OUTLINE_TOKENS})",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Continue from next=N if previous outline said so",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "content_blocks",
            "description": (
                "Fetch full text for selected content_outline block ids "
                f"(comma-separated, max {MAX_CONTENT_BLOCK_IDS} ids, e.g. 'p1,p3,p5'). "
                "Call at most once per page, then done(finalAnswer)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "string",
                        "description": "Comma-separated block ids from content_outline",
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": f"Total text token budget (default {DEFAULT_BLOCKS_TOKENS})",
                    },
                },
                "required": ["ids"],
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
    # Soft loop hint injected as a user message (e.g. after rich schema_page).
    nudge: Optional[str] = None


def _groups_summary(shadow: ShadowPage) -> List[Dict[str, Any]]:
    return [
        {"name": g.get("name", "Page"), "count": len(g.get("elements", []))}
        for g in (shadow.action_groups or [])
    ]


def _action_terse(shadow: ShadowPage, limit: int = DEFAULT_ACTION_LIMIT) -> str:
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
        lines.append(f"... +{len(shadow.action_map) - limit} more — use shadow_query or schema_page")
    return "\n".join(lines)


def _observe(
    shadow: ShadowPage,
    *,
    diff: bool = False,
    detail: str = "terse",
) -> Dict[str, Any]:
    title = ""
    try:
        title = shadow.page.title() if shadow.page else ""
    except Exception:
        title = ""
    action_count = len(shadow.action_map)
    payload: Dict[str, Any] = {
        "url": shadow.last_url,
        "title": title,
        "action_count": action_count,
        "page_class": getattr(shadow, "page_class", "Static"),
    }
    if diff and shadow.last_diff:
        payload["detail"] = "diff"
        payload["observation"] = shadow.diff_terse()
        payload["has_changes"] = shadow.last_diff.has_changes
        return payload

    dense = action_count > DENSE_ACTION_THRESHOLD
    mode = detail if detail in ("terse", "minimal") else "terse"
    if dense and mode == "terse":
        mode = "minimal"

    payload["detail"] = mode
    payload["groups_summary"] = _groups_summary(shadow)
    limit = DENSE_ACTION_LIMIT if mode == "minimal" else DEFAULT_ACTION_LIMIT
    payload["observation"] = _action_terse(shadow, limit=limit)
    if dense or mode == "minimal":
        payload["hint"] = (
            "Dense page. Prefer shadow_query(...), schema_page(), or content_outline(), "
            "then done(finalAnswer). Do not click/fill randomly through the Action Map."
        )
    return payload


def schema_has_payload(parsed: Dict[str, Any]) -> bool:
    """True when SchemaSnap found at least one table, form, or non-empty list."""
    if not isinstance(parsed, dict):
        return False
    if parsed.get("tables"):
        return True
    if parsed.get("forms"):
        return True
    for item in parsed.get("lists") or []:
        if isinstance(item, dict) and item.get("items"):
            return True
        if isinstance(item, list) and item:
            return True
    return bool(parsed.get("lists"))


def outline_has_payload(outline: str) -> bool:
    """True when outline looks usable (block lines or ranking signals)."""
    text = (outline or "").strip()
    if not text:
        return False
    if "cards=" in text or "feeds=" in text or "signals=" in text:
        return True
    return any(line.startswith("p") and "|" in line for line in text.splitlines())


def _clip_text(value: Any, limit: int = 80) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    # Drop embedded widget/JSON blobs that explode agent context.
    if "{&amp;quot;" in text or '{"widgets"' in text or text.count("{") > 2:
        return text[:40] + "…"
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def compact_schema_for_agent(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Bound SchemaSnap payloads so mega-menus cannot blow the exec context."""
    if not isinstance(parsed, dict):
        return {"tables": [], "forms": [], "lists": []}

    tables_out: List[Dict[str, Any]] = []
    for table in (parsed.get("tables") or [])[:3]:
        if not isinstance(table, dict):
            continue
        rows = table.get("rows") or []
        tables_out.append(
            {
                **{k: table.get(k) for k in ("columns", "types", "total_rows") if k in table},
                "rows": rows[:10],
            }
        )

    forms_out: List[Dict[str, Any]] = []
    for form in (parsed.get("forms") or [])[:2]:
        if not isinstance(form, dict):
            continue
        fields = []
        for field in (form.get("fields") or [])[:8]:
            if not isinstance(field, dict):
                continue
            fields.append(
                {
                    k: _clip_text(field.get(k), 60)
                    for k in ("name", "type", "label", "placeholder", "required", "tag")
                    if k in field
                }
            )
        forms_out.append(
            {
                "action": _clip_text(form.get("action"), 80),
                "method": form.get("method"),
                "field_count": form.get("field_count", len(fields)),
                "fields": fields,
            }
        )

    lists_out: List[Dict[str, Any]] = []
    for lst in (parsed.get("lists") or [])[:5]:
        if not isinstance(lst, dict):
            continue
        items = [_clip_text(i, 70) for i in (lst.get("items") or [])[:8]]
        # Skip empty/nav noise after clipping
        items = [i for i in items if i]
        if not items:
            continue
        lists_out.append(
            {
                "type": lst.get("type"),
                "total": lst.get("total", len(items)),
                "items": items,
            }
        )

    return {"tables": tables_out, "forms": forms_out, "lists": lists_out}


SCHEMA_DONE_NUDGE = (
    "schema_page returned structured data. If it answers the task, call done(finalAnswer) now. "
    "Do not keep clicking or navigating unless a required fact is still missing."
)

SCHEMA_EMPTY_NUDGE = (
    "schema_page found little/no tables or lists. On SPA/catalog pages call content_outline(), "
    "then at most ONE content_blocks(ids), then done(finalAnswer)."
)

CONTENT_OUTLINE_NUDGE = (
    "content_outline is ready. If it answers the task, call done(finalAnswer) now. "
    "Otherwise call content_blocks ONCE with a few priced-card ids, then done — "
    "do not fetch blocks repeatedly."
)

CONTENT_BLOCKS_NUDGE = (
    "STOP. content_blocks already returned text. Your ONLY next tool must be "
    "done(finalAnswer). Do NOT call content_blocks, content_outline, or navigate again."
)

CONTENT_BLOCKS_REPEAT_NUDGE = (
    "content_blocks was already used on this page. Call done(finalAnswer) now using prior text. "
    "Further content_blocks calls are blocked."
)


class ToolRouter:
    """Dispatch model tool calls onto a ShadowPage."""

    def __init__(self, shadow: ShadowPage):
        self.shadow = shadow
        self._content_index_html: Optional[str] = None
        self._content_index_blocks: List[Dict[str, Any]] = []
        self._content_index_quality: Dict[str, Any] = {}
        self._content_blocks_fetches: int = 0
        self._last_content_blocks: Dict[str, Any] = {}

    def _reset_content_fetch_budget(self) -> None:
        self._content_blocks_fetches = 0
        self._last_content_blocks = {}

    def _ensure_content_index(self) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        from shadow_web.content_index import build, quality

        if not self.shadow.clean_html:
            self.shadow.refresh(diff=False)
        html = self.shadow.clean_html or ""
        if not html:
            raise RuntimeError("No captured HTML. Call navigate(url) first.")
        if self._content_index_html != html:
            self._content_index_html = html
            self._content_index_blocks = build(html)
            self._content_index_quality = quality(html, self._content_index_blocks)
            self._reset_content_fetch_budget()
        return self._content_index_blocks, self._content_index_quality

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
        self._content_index_html = None
        self._reset_content_fetch_budget()
        data = _observe(self.shadow, diff=False)
        return ToolResult(name="navigate", ok=True, content=json.dumps(data), data=data)

    def _tool_snapshot(self, args: Dict[str, Any]) -> ToolResult:
        detail = str(args.get("detail") or "terse")
        use_diff = detail == "diff"
        self.shadow.refresh(diff=use_diff)
        data = _observe(self.shadow, diff=use_diff, detail=detail)
        return ToolResult(name="snapshot", ok=True, content=json.dumps(data), data=data)

    def _tool_shadow_query(self, args: Dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        try:
            limit = int(args.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 50))
        if not self.shadow.action_map:
            self.shadow.refresh(diff=False)
        result = self.shadow.query(query, fmt="result")
        matches = list(result.matches)[:limit]
        lines = [f"# shadow_query: {query} ({len(matches)}/{result.total} shown)"]
        for action in matches:
            sid = action.get("id", "?")
            kind = action.get("type", "?")
            label = action.get("label") or action.get("placeholder") or action.get("text") or ""
            group = action.get("group", "")
            suffix = f" [{group}]" if group else ""
            lines.append(f"@{sid} {kind} {label}{suffix}".strip())
        data = {
            "query": query,
            "count": len(matches),
            "total": result.total,
            "observation": "\n".join(lines),
            "url": self.shadow.last_url,
        }
        return ToolResult(name="shadow_query", ok=True, content=json.dumps(data), data=data)

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
        # Exec always bounds schema — uncapped menus (Rimi/Market) explode context.
        if max_rows_i <= 0:
            max_rows_i = 10
        max_rows_i = min(max_rows_i, 15)
        parsed = parse_page(self.shadow.clean_html, max_rows=max_rows_i)
        compact = compact_schema_for_agent(parsed)
        data = {
            "url": self.shadow.last_url,
            "schema": compact,
        }
        if schema_has_payload(compact):
            nudge = SCHEMA_DONE_NUDGE
        else:
            nudge = SCHEMA_EMPTY_NUDGE
        return ToolResult(
            name="schema_page",
            ok=True,
            content=json.dumps(data, ensure_ascii=False),
            data=data,
            nudge=nudge,
        )

    def _tool_content_outline(self, args: Dict[str, Any]) -> ToolResult:
        from shadow_web.content_index import outline_text

        try:
            max_tokens = int(args.get("max_tokens", DEFAULT_OUTLINE_TOKENS))
        except (TypeError, ValueError):
            max_tokens = DEFAULT_OUTLINE_TOKENS
        try:
            offset = int(args.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        blocks, quality_data = self._ensure_content_index()
        outline = outline_text(
            blocks,
            max_tokens=max(50, min(max_tokens, 800)),
            offset=max(0, offset),
            quality_data=quality_data,
        )
        data = {
            "url": self.shadow.last_url,
            "block_count": len(blocks),
            "outline": outline,
            "quality": quality_data,
        }
        nudge = CONTENT_OUTLINE_NUDGE if outline_has_payload(outline) else None
        return ToolResult(
            name="content_outline",
            ok=True,
            content=json.dumps(data, ensure_ascii=False),
            data=data,
            nudge=nudge,
        )

    def _tool_content_blocks(self, args: Dict[str, Any]) -> ToolResult:
        from shadow_web.content_index import fetch

        if self._content_blocks_fetches >= 1:
            data = {
                "url": self.shadow.last_url,
                "blocked": True,
                "reason": "content_blocks already used once on this page",
                "previous": self._last_content_blocks,
            }
            return ToolResult(
                name="content_blocks",
                ok=False,
                content=json.dumps(data, ensure_ascii=False),
                data=data,
                nudge=CONTENT_BLOCKS_REPEAT_NUDGE,
            )

        ids_raw = str(args.get("ids") or "").strip()
        if not ids_raw:
            raise ValueError("ids is required")
        block_ids = [i.strip() for i in ids_raw.split(",") if i.strip()]
        if not block_ids:
            raise ValueError("ids is required")
        if len(block_ids) > MAX_CONTENT_BLOCK_IDS:
            block_ids = block_ids[:MAX_CONTENT_BLOCK_IDS]
        try:
            max_tokens = int(args.get("max_tokens", DEFAULT_BLOCKS_TOKENS))
        except (TypeError, ValueError):
            max_tokens = DEFAULT_BLOCKS_TOKENS
        # Cap hard so SPA listings cannot blow the exec context window.
        max_tokens = max(50, min(max_tokens, 1200))
        blocks, _quality = self._ensure_content_index()
        fetched = fetch(blocks, block_ids, max_tokens=max_tokens)
        data = {
            "url": self.shadow.last_url,
            "ids": block_ids,
            "blocks": fetched,
        }
        self._content_blocks_fetches += 1
        self._last_content_blocks = data
        nudge = CONTENT_BLOCKS_NUDGE if fetched else None
        return ToolResult(
            name="content_blocks",
            ok=True,
            content=json.dumps(data, ensure_ascii=False),
            data=data,
            nudge=nudge,
        )

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
                evidence["schema"] = compact_schema_for_agent(
                    parse_page(self.shadow.clean_html, max_rows=5)
                )
            except Exception:
                pass
        return ToolResult(
            name="done",
            ok=True,
            content=json.dumps({"finalAnswer": str(answer), "evidence": evidence}, ensure_ascii=False),
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
