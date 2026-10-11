"""Guard the published MCP tool surface against documentation drift.

Nothing used to construct the MCP server in the test suite, so the README said
"26 tools", the LobeHub manifest advertised 19, and the code exposed 27 without
anyone noticing.  These tests make that class of drift fail loudly.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _actual_tools() -> list[str]:
    from shadow_web.mcp.server import create_mcp_server

    server = create_mcp_server()
    tools = asyncio.run(server.list_tools())
    return sorted(tool.name for tool in tools)


def test_server_exposes_a_non_trivial_tool_surface():
    tools = _actual_tools()
    assert len(tools) >= 27
    assert "agent_run" in tools
    assert len(tools) == len(set(tools)), "duplicate tool names"


def test_lobehub_manifest_lists_exactly_the_exposed_tools():
    manifest = json.loads((ROOT / "lhm.plugin.json").read_text(encoding="utf-8"))
    declared = {tool["name"] for tool in manifest["tools"]}
    actual = set(_actual_tools())
    missing = sorted(actual - declared)
    unknown = sorted(declared - actual)
    assert not missing, f"tools missing from lhm.plugin.json: {missing}"
    assert not unknown, f"lhm.plugin.json declares tools that do not exist: {unknown}"


def test_readme_tool_table_lists_every_tool():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^### All \d+ tools\s*$(.*?)^###", readme, re.MULTILINE | re.DOTALL)
    assert match, "README tool table section not found"
    section = match.group(1)
    missing = [name for name in _actual_tools() if f"`{name}`" not in section]
    assert not missing, f"tools missing from the README tool table: {missing}"


def test_readme_tool_count_matches_the_code():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    actual = len(_actual_tools())
    counts = {int(value) for value in re.findall(r"\*\*(\d+) tools\*\*", readme)}
    counts |= {int(value) for value in re.findall(r"^### All (\d+) tools", readme, re.MULTILINE)}
    assert counts, "README does not state a tool count"
    assert counts == {actual}, f"README claims {sorted(counts)} tools, code exposes {actual}"


def test_mcp_dependency_is_capped_below_2():
    """mcp 2.x renamed FastMCP to MCPServer; shadow_web.mcp.server imports the 1.x path.

    Unpinned, a fresh `pip install "shadow-web[mcp]"` / `uvx shadow-web-mcp` (the
    README's one-command path) resolves to mcp 2.x and dies on import.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    specs = re.findall(r'"(mcp>=[^"]*)"', text)  # not the "mcp" keyword
    assert specs, "no mcp requirement found in pyproject.toml"
    for spec in specs:
        assert "<2" in spec, f"{spec} is not capped below mcp 2.x (FastMCP -> MCPServer)"


def test_readme_does_not_advertise_an_unreproducible_compression_ratio():
    """The old table claimed -99% by comparing against a retrieval budget."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "−99%" not in readme and "-99%" not in readme
    assert "up to 99%" not in readme.lower()


# ── Form submission must stay opt-in on every surface ────────────────


def _tool_schema(name: str) -> dict:
    from shadow_web.mcp.server import create_mcp_server

    server = create_mcp_server()
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        if tool.name == name:
            return tool.inputSchema or {}
    raise AssertionError(f"tool {name} not found")


def test_form_fill_tools_expose_auto_submit_and_default_to_not_submitting():
    plan_schema = _tool_schema("form_fill_plan")
    exec_schema = _tool_schema("form_fill_execute")

    for schema in (plan_schema, exec_schema):
        props = schema.get("properties") or {}
        assert "auto_submit" in props, "auto_submit must be an explicit opt-in"
        assert props["auto_submit"].get("default") in (False, None)

    # Submitting must never be the default on the plan builder either.
    from shadow_web.form_fill import build_form_fill_plan

    import inspect

    default = inspect.signature(build_form_fill_plan).parameters["auto_submit"].default
    assert default is False


def test_multi_step_executor_receives_the_callers_auto_submit(monkeypatch):
    """The MCP plumbing must pass auto_submit through, not drop it."""
    import shadow_web.form_fill as form_fill
    import shadow_web.mcp.server as server_mod

    captured: dict = {}

    async def fake_multi_step(shadow, profile, **kwargs):
        captured.update(kwargs)
        return {"status": "completed", "auto_submit": kwargs.get("auto_submit")}

    monkeypatch.setattr(form_fill, "execute_form_fill_plan_multi_step_async", fake_multi_step)
    monkeypatch.setattr(server_mod, "_get_shadow_page", lambda: object())

    server = server_mod.create_mcp_server()
    result = asyncio.run(
        server._tool_manager.call_tool(
            "form_fill_execute",
            {
                "profile": '{"email": "a@b.co"}',
                "multi_step": True,
                "auto_submit": True,
            },
        )
    )

    assert captured.get("auto_submit") is True
    assert result is not None


# ── The navigation policy must be enforced on the MCP surface ────────


def test_navigate_refuses_a_file_url_without_starting_a_browser(monkeypatch):
    import shadow_web.mcp.server as server_mod

    async def must_not_start(*args, **kwargs):
        raise AssertionError("the browser must not be started for a blocked URL")

    monkeypatch.setattr(server_mod, "_ensure_browser", must_not_start)
    server = server_mod.create_mcp_server()

    with pytest.raises(Exception) as excinfo:
        asyncio.run(
            server._tool_manager.call_tool("navigate", {"url": "file:///etc/passwd"})
        )
    assert "not allowed" in str(excinfo.value).lower()


def test_navigate_refuses_a_metadata_address(monkeypatch):
    import shadow_web.mcp.server as server_mod

    async def must_not_start(*args, **kwargs):
        raise AssertionError("the browser must not be started for a blocked URL")

    monkeypatch.setattr(server_mod, "_ensure_browser", must_not_start)
    server = server_mod.create_mcp_server()

    with pytest.raises(Exception) as excinfo:
        asyncio.run(
            server._tool_manager.call_tool(
                "navigate",
                {"url": "http://169.254.169.254/latest/meta-data/"},
            )
        )
    assert "not allowed" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Release version: the hot cache says pyproject == server.py == lhm.plugin.json,
# but nothing enforced it, and 0.4.3 was very nearly shipped with the FastAPI app
# still advertising 0.4.2 while the manifest and package said 0.4.3.
# ---------------------------------------------------------------------------


def test_version_is_synced_across_package_manifest_and_server():
    from shadow_web import __version__

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    assert match, "pyproject.toml has no version"
    pyproject_version = match.group(1)

    manifest_version = json.loads(
        (ROOT / "lhm.plugin.json").read_text(encoding="utf-8")
    )["version"]

    server_source = (ROOT / "src/shadow_web/server.py").read_text(encoding="utf-8")
    server_match = re.search(r'version="([^"]+)"', server_source)
    assert server_match, "server.py FastAPI app has no version"
    server_version = server_match.group(1)

    assert pyproject_version == manifest_version == server_version == __version__, (
        "release version drift: "
        f"pyproject={pyproject_version} manifest={manifest_version} "
        f"server={server_version} package={__version__}"
    )


def test_readme_states_the_current_version():
    from shadow_web import __version__

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"**v{__version__}**" in readme, "README banner does not state the current version"
