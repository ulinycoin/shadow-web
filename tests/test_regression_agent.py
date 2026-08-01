"""Regression tests for P0/P1 bugs found in two-phase review (2026-08-01)."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from shadow_web.agent.loop import AgentLoop
from shadow_web.agent.models import ChatMessage, ModelResponse, resolve_api_credentials
from shadow_web.agent.tools import ToolResult, ToolRouter


# ---------------------------------------------------------------------------
# helpers (same signature as test_agent_loop.py)
# ---------------------------------------------------------------------------

class FakeShadow:
    def __init__(self):
        self.last_url = "https://example.com"
        self.action_map: List[Dict[str, Any]] = [
            {"id": "1", "type": "link", "label": "More information...", "group": "Main"}
        ]
        self.action_groups = [{"name": "Main", "elements": self.action_map}]
        self.clean_html = "<html><body><a>More information...</a></body></html>"
        self.last_diff = None
        self.page_class = "Static"
        self.page = type("P", (), {"title": lambda self: "Example Domain"})()

    def refresh(self, diff: bool = False):
        return self.clean_html, "<page/>"


class ScriptedLLM:
    def __init__(self, responses: List[ModelResponse]):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools):
        if self.calls >= len(self.responses):
            raise AssertionError("LLM called more times than scripted")
        resp = self.responses[self.calls]
        self.calls += 1
        return resp


def _tool_call(name, args, call_id="c1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


# ---------------------------------------------------------------------------
# P0-1: credential resolution -- atomic (key, url) pairs
# ---------------------------------------------------------------------------

def test_credential_deepseek_key_goes_to_deepseek_url(monkeypatch):
    """DeepSeek key MUST select api.deepseek.com, not default OpenAI."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SHADOW_WEB_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SHADOW_WEB_LLM_BASE_URL", raising=False)

    # Reload to bypass dotenv cache
    import importlib
    import shadow_web.runtime_env as re
    monkeypatch.setattr(re, "load_dotenv_files", lambda *a, **kw: [])
    import shadow_web.agent.models as m
    re._DOTENV_LOADED = False
    importlib.reload(m)

    key, url = m.resolve_api_credentials()
    assert key == "sk-deep", f"expected DeepSeek key, got {key}"
    assert url == "https://api.deepseek.com", f"expected DeepSeek URL, got {url}"


def test_credential_openai_key_goes_to_openai_default(monkeypatch):
    """OpenAI key alone selects default OpenAI endpoint (None = SDK default)."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SHADOW_WEB_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SHADOW_WEB_LLM_BASE_URL", raising=False)

    import importlib
    import shadow_web.runtime_env as re
    monkeypatch.setattr(re, "load_dotenv_files", lambda *a, **kw: [])
    import shadow_web.agent.models as m
    re._DOTENV_LOADED = False
    importlib.reload(m)

    key, url = m.resolve_api_credentials()
    assert key == "sk-openai"
    assert url is None, f"OpenAI should get None/default URL, got {url}"


def test_credential_openrouter_key_goes_to_openrouter_url(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.delenv("SHADOW_WEB_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SHADOW_WEB_LLM_BASE_URL", raising=False)

    import importlib
    import shadow_web.runtime_env as re
    monkeypatch.setattr(re, "load_dotenv_files", lambda *a, **kw: [])
    import shadow_web.agent.models as m
    re._DOTENV_LOADED = False
    importlib.reload(m)

    key, url = m.resolve_api_credentials()
    assert key == "sk-or"
    assert url == "https://openrouter.ai/api/v1"


def test_credential_explicit_base_url_respected(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    import importlib
    import shadow_web.runtime_env as re
    monkeypatch.setattr(re, "load_dotenv_files", lambda *a, **kw: [])
    import shadow_web.agent.models as m
    re._DOTENV_LOADED = False
    importlib.reload(m)

    key, url = m.resolve_api_credentials(
        base_url="https://custom-proxy.example.com/v1"
    )
    assert key == "sk-deep"
    assert url == "https://custom-proxy.example.com/v1"


def test_credential_explicit_api_key_wins(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    import importlib
    import shadow_web.runtime_env as re
    monkeypatch.setattr(re, "load_dotenv_files", lambda *a, **kw: [])
    import shadow_web.agent.models as m
    re._DOTENV_LOADED = False
    importlib.reload(m)

    key, url = m.resolve_api_credentials(api_key="sk-explicit")
    assert key == "sk-explicit"
    # Explicit api_key without base_url -> no inference
    assert url is None


# ---------------------------------------------------------------------------
# P0-2: multi-tool nudge must not interleave tool and user messages
# ---------------------------------------------------------------------------

def test_nudge_does_not_interleave_tool_responses():
    """Two parallel tool calls with one nudge must append all tool messages
    before any user nudge."""
    llm = ScriptedLLM(
        [
            ModelResponse(
                content=None,
                tool_calls=[
                    _tool_call("schema_page", {}, "ta"),
                    _tool_call("snapshot", {"detail": "terse"}, "tb"),
                ],
                usage={},
            ),
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("done", {"finalAnswer": "ok"}, "tc")],
                usage={},
            ),
        ]
    )
    shadow = FakeShadow()
    router = ToolRouter(shadow)
    seen: List[List[ChatMessage]] = []

    class CaptureLLM:
        def chat(self, messages, tools):
            seen.append(list(messages))
            return llm.chat(messages, tools)

    orig = router.dispatch

    def _dispatch(name, args):
        if name == "schema_page":
            return ToolResult(
                name="schema_page", ok=True, content='{"schema":{"tables":[]}}',
                nudge="call done now",
            )
        if name == "done":
            return ToolResult(
                name="done", ok=True, content="{}",
                terminal=True, final_answer=args.get("finalAnswer"),
            )
        return orig(name, args)

    router.dispatch = _dispatch

    result = AgentLoop(llm=CaptureLLM(), tools=router, max_steps=5).run("scan")
    assert result.ok is True

    second_call = seen[1]
    roles = [m.role for m in second_call]

    tool_indices = [i for i, r in enumerate(roles) if r == "tool"]
    nudge_indices = [
        i for i, r in enumerate(roles)
        if r == "user" and second_call[i].content and "call done" in (second_call[i].content or "")
    ]

    # All tool messages before all nudge user messages
    if tool_indices and nudge_indices:
        assert max(tool_indices) < min(nudge_indices), (
            f"Tool messages ({tool_indices}) must come before nudge ({nudge_indices})"
        )

    # Nudge must be present (not lost)
    assert nudge_indices, "Nudge was lost -- not present in second LLM call"

    # No tool after nudge
    tools_after_nudge = [i for i in tool_indices if i > min(nudge_indices)]
    assert not tools_after_nudge, f"Tool messages after nudge: {tools_after_nudge}"


# ---------------------------------------------------------------------------
# P1: content_blocks stale guard must reset on DOM change
# ---------------------------------------------------------------------------

def test_content_blocks_allowed_after_dom_change():
    """After using content_blocks on page A, changing the HTML should
    allow content_blocks again on page B."""
    shadow = FakeShadow()
    router = ToolRouter(shadow)

    # Prime: first page, one content_blocks call
    shadow.clean_html = (
        "<main><div class='card'><h2>Product A</h2><p>$10</p></div></main>"
    )
    from shadow_web.content_index import build as ci_build
    blocks = ci_build(shadow.clean_html)
    router._content_index_html = shadow.clean_html
    router._content_index_blocks = blocks

    # First call -- should succeed
    r1 = router.dispatch("content_blocks", {"ids": "p0"})
    assert r1.ok is True, f"First content_blocks should succeed: {r1.content}"

    # Second call on same page -- should be blocked
    r2 = router.dispatch("content_blocks", {"ids": "p0"})
    assert r2.ok is False, "Second content_blocks on same page should be blocked"
    assert r2.data.get("blocked") is True

    # --- DOM changes (simulating click -> new page) ---
    shadow.clean_html = (
        "<main><div class='card'><h2>Product B</h2><p>$20</p></div></main>"
    )
    new_blocks = ci_build(shadow.clean_html)
    # Force re-index by clearing cached HTML
    router._content_index_html = None
    router._content_index_blocks = []

    # content_blocks should succeed again on new page
    r3 = router.dispatch("content_blocks", {"ids": "p0"})
    assert r3.ok is True, (
        f"After DOM change, content_blocks should be re-allowed: "
        f"ok={r3.ok} blocked={r3.data.get('blocked')} reason={r3.data.get('reason')}"
    )
