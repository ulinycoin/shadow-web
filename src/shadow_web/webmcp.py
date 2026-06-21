"""WebMCP bridge — discover and execute browser-registered tools (Chrome 145+ preview)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from lxml import etree

PageLike = Any

_DETECT_TOOLS_SCRIPT = """
async () => {
  const ctx = document.modelContext ?? navigator.modelContext;
  if (!ctx || typeof ctx.getTools !== "function") {
    return { available: false, tools: [], api: null, error: null };
  }
  try {
    const tools = await ctx.getTools();
    const normalized = (tools || []).map((tool) => ({
      name: tool.name || "",
      description: tool.description || "",
      inputSchema: tool.inputSchema || tool.input_schema || null,
    })).filter((tool) => tool.name);
    return {
      available: normalized.length > 0,
      tools: normalized,
      api: document.modelContext ? "document" : "navigator",
      error: null,
    };
  } catch (error) {
    return {
      available: false,
      tools: [],
      api: document.modelContext ? "document" : "navigator",
      error: String(error),
    };
  }
}
"""

_EXECUTE_TOOL_SCRIPT = """
async ({ name, args }) => {
  const ctx = document.modelContext ?? navigator.modelContext;
  if (!ctx || typeof ctx.executeTool !== "function") {
    return { ok: false, error: "webmcp_unavailable" };
  }
  try {
    const payload = JSON.stringify(args ?? {});
    const result = await ctx.executeTool(name, payload);
    return { ok: true, result };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}
"""


@dataclass
class WebMcpTool:
    name: str
    description: str = ""
    input_schema: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.input_schema is not None:
            data["inputSchema"] = self.input_schema
        return data


@dataclass
class WebMcpSnapshot:
    available: bool
    tools: List[WebMcpTool] = field(default_factory=list)
    api: Optional[str] = None
    error: Optional[str] = None

    @property
    def count(self) -> int:
        return len(self.tools)


def _parse_webmcp_raw(raw: Optional[Dict[str, Any]]) -> WebMcpSnapshot:
    if not raw:
        return WebMcpSnapshot(available=False)

    tools = [
        WebMcpTool(
            name=item.get("name", ""),
            description=item.get("description", ""),
            input_schema=item.get("inputSchema"),
        )
        for item in raw.get("tools") or []
        if item.get("name")
    ]
    return WebMcpSnapshot(
        available=bool(raw.get("available")) and len(tools) > 0,
        tools=tools,
        api=raw.get("api"),
        error=raw.get("error"),
    )


def detect_webmcp(page: PageLike) -> WebMcpSnapshot:
    """Detect WebMCP tools exposed by the current page."""
    return _parse_webmcp_raw(page.evaluate(_DETECT_TOOLS_SCRIPT))


def execute_webmcp_tool(page: PageLike, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    """Execute a WebMCP tool on the live page."""
    if not name:
        raise ValueError("Tool name is required")

    result = page.evaluate(_EXECUTE_TOOL_SCRIPT, {"name": name, "args": arguments or {}})
    if not result or not result.get("ok"):
        error = (result or {}).get("error", "unknown")
        raise RuntimeError(f"WebMCP executeTool('{name}') failed: {error}")
    return result.get("result")


def webmcp_tools_to_action_map(tools: List[WebMcpTool]) -> List[Dict[str, Any]]:
    """Convert WebMCP tools into Action Map entries for agents that expect data-sid."""
    action_map: List[Dict[str, Any]] = []
    for index, tool in enumerate(tools, start=1):
        entry: Dict[str, Any] = {
            "id": str(index),
            "type": "webmcp_tool",
            "label": tool.description or tool.name,
            "tool_name": tool.name,
            "group": "WebMCP Tools",
        }
        if tool.input_schema is not None:
            entry["input_schema"] = json.dumps(tool.input_schema, separators=(",", ":"))
        action_map.append(entry)
    return action_map


def generate_webmcp_xml_map(url: str, title: str, tools: List[WebMcpTool]) -> str:
    """Build grouped XML for WebMCP tools (no DOM snapshot needed)."""
    root = etree.Element("page", url=url, title=title, mode="webmcp")
    group_el = etree.SubElement(root, "group", name="WebMCP Tools")
    for index, tool in enumerate(tools, start=1):
        tool_el = etree.SubElement(
            group_el,
            "action",
            id=str(index),
            type="webmcp_tool",
            tool_name=tool.name,
            label=(tool.description or tool.name)[:200],
        )
        if tool.input_schema is not None:
            tool_el.set(
                "input_schema",
                json.dumps(tool.input_schema, separators=(",", ":"))[:500],
            )
    return etree.tostring(root, encoding="unicode", pretty_print=True)


def webmcp_tools_terse(tools: List[WebMcpTool]) -> str:
    """Compact tool list for LLM context."""
    if not tools:
        return "# webmcp: (no tools)"
    lines = [f"# webmcp tools ({len(tools)})"]
    for index, tool in enumerate(tools, start=1):
        lines.append(f"@{index} tool:{tool.name} {tool.description}".strip())
    return "\n".join(lines)


async def adetect_webmcp(page: PageLike) -> WebMcpSnapshot:
    """Async variant of :func:`detect_webmcp`."""
    return _parse_webmcp_raw(await page.evaluate(_DETECT_TOOLS_SCRIPT))


async def aexecute_webmcp_tool(
    page: PageLike, name: str, arguments: Optional[Dict[str, Any]] = None
) -> Any:
    """Async variant of :func:`execute_webmcp_tool`."""
    if not name:
        raise ValueError("Tool name is required")

    result = await page.evaluate(_EXECUTE_TOOL_SCRIPT, {"name": name, "args": arguments or {}})
    if not result or not result.get("ok"):
        error = (result or {}).get("error", "unknown")
        raise RuntimeError(f"WebMCP executeTool('{name}') failed: {error}")
    return result.get("result")
