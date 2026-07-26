"""Unit tests for the standalone exec agent loop (no browser / no live LLM)."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from shadow_web.agent.loop import AgentLoop, run_agent_task
from shadow_web.agent.models import ChatMessage, ModelResponse
from shadow_web.agent.tools import ToolResult, ToolRouter, parse_tool_arguments


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

    def click(self, sid: str, timeout_ms: int = 5000):
        return None

    def fill(self, sid: str, value: str, timeout_ms: int = 5000):
        return None

    def diff_terse(self) -> str:
        return "# diff: (none)"


class ScriptedLLM:
    """Returns a predetermined sequence of ModelResponse objects."""

    def __init__(self, responses: List[ModelResponse]):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages: List[ChatMessage], tools: List[Dict[str, Any]]) -> ModelResponse:
        if self.calls >= len(self.responses):
            raise AssertionError("LLM called more times than scripted")
        resp = self.responses[self.calls]
        self.calls += 1
        return resp


def _tool_call(name: str, args: Dict[str, Any], call_id: str = "c1") -> Dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_parse_tool_arguments_ok():
    assert parse_tool_arguments('{"sid": "1"}') == {"sid": "1"}
    assert parse_tool_arguments({}) == {}
    assert parse_tool_arguments(None) == {}


def test_parse_tool_arguments_invalid():
    try:
        parse_tool_arguments("not-json")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_done_stops_loop():
    llm = ScriptedLLM(
        [
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("done", {"finalAnswer": "Example Domain"}, "t1")],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        ]
    )
    shadow = FakeShadow()
    # Bypass ToolRouter browser tools — inject a router with patched dispatch
    router = ToolRouter(shadow)  # type: ignore[arg-type]

    def _done_only(name: str, arguments: Dict[str, Any]) -> ToolResult:
        assert name == "done"
        return ToolResult(
            name="done",
            ok=True,
            content=json.dumps({"finalAnswer": arguments["finalAnswer"]}),
            terminal=True,
            final_answer=arguments["finalAnswer"],
            data={"url": shadow.last_url},
        )

    router.dispatch = _done_only  # type: ignore[method-assign]
    result = AgentLoop(llm=llm, tools=router, max_steps=5).run("What is the title?")
    assert result.ok is True
    assert result.answer == "Example Domain"
    assert result.steps == 1
    assert result.usage["total_tokens"] == 15
    assert llm.calls == 1


def test_no_progress_guard():
    same = ModelResponse(
        content=None,
        tool_calls=[_tool_call("snapshot", {"detail": "terse"}, "t1")],
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    llm = ScriptedLLM([same, same, same, same])
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]

    payload = json.dumps({"url": shadow.last_url, "observation": "same"})

    def _snap(name: str, arguments: Dict[str, Any]) -> ToolResult:
        return ToolResult(name="snapshot", ok=True, content=payload)

    router.dispatch = _snap  # type: ignore[method-assign]
    result = AgentLoop(llm=llm, tools=router, max_steps=10, no_progress_limit=3).run("loop")
    assert result.ok is False
    assert result.error and "No progress" in result.error
    assert result.steps == 3


def test_max_steps_exhausted():
    resp = ModelResponse(
        content=None,
        tool_calls=[_tool_call("snapshot", {"detail": "terse"}, "t1")],
        usage={},
    )
    # Vary content so no-progress does not trigger
    responses = []
    for i in range(3):
        responses.append(
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("snapshot", {"detail": "terse"}, f"t{i}")],
                usage={},
            )
        )
    llm = ScriptedLLM(responses)
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    n = {"i": 0}

    def _snap(name: str, arguments: Dict[str, Any]) -> ToolResult:
        n["i"] += 1
        return ToolResult(
            name="snapshot",
            ok=True,
            content=json.dumps({"n": n["i"]}),
        )

    router.dispatch = _snap  # type: ignore[method-assign]
    result = AgentLoop(llm=llm, tools=router, max_steps=3).run("keep going")
    assert result.ok is False
    assert result.error and "max_steps" in result.error
    assert result.steps == 3


def test_plain_text_finish():
    llm = ScriptedLLM(
        [ModelResponse(content="The answer is 42", tool_calls=[], usage={})]
    )
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    result = AgentLoop(llm=llm, tools=router).run("what?")
    assert result.ok is True
    assert result.answer == "The answer is 42"


def test_run_agent_task_with_injected_deps():
    llm = ScriptedLLM(
        [
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("done", {"finalAnswer": "ok"}, "t1")],
                usage={},
            )
        ]
    )
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]

    def _done(name: str, arguments: Dict[str, Any]) -> ToolResult:
        return ToolResult(
            name="done",
            ok=True,
            content="{}",
            terminal=True,
            final_answer=arguments["finalAnswer"],
        )

    router.dispatch = _done  # type: ignore[method-assign]
    result = run_agent_task("x", llm=llm, tools=router)
    assert result.ok and result.answer == "ok"
    assert result.to_dict()["ok"] is True


def test_tool_router_done_and_schema(monkeypatch):
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    done = router.dispatch("done", {"finalAnswer": "hi"})
    assert done.terminal is True
    assert done.final_answer == "hi"

    schema = router.dispatch("schema_page", {"max_rows": 5})
    assert schema.ok is True
    body = json.loads(schema.content)
    assert "schema" in body


def test_tool_router_unknown():
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    result = router.dispatch("not_a_tool", {})
    assert result.ok is False
    assert "Unknown tool" in result.content


def test_normalize_browser_engine():
    from shadow_web.agent.session import normalize_browser_engine

    assert normalize_browser_engine(None) == "camoufox"
    assert normalize_browser_engine("Chromium") == "chromium"
    try:
        normalize_browser_engine("webkit")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_camoufox_falls_back_to_chromium(monkeypatch):
    from shadow_web.agent import session as session_mod

    calls: List[str] = []

    class FakeSession(session_mod.BrowserSession):
        def _start_camoufox(self, headless: bool):
            calls.append("camoufox")
            raise RuntimeError("no camoufox binary")

        def _start_chromium(self, headless: bool):
            calls.append("chromium")
            self.browser_type = "chromium"
            self.page = type("P", (), {"title": lambda self: "t"})()
            self.shadow = FakeShadow()  # type: ignore[assignment]
            return self.shadow

    sess = FakeSession(browser="camoufox")
    shadow = sess.start()
    assert calls == ["camoufox", "chromium"]
    assert sess.browser_type == "chromium"
    assert shadow is sess.shadow


def test_camoufox_uses_dom_capture_mode(monkeypatch):
    from shadow_web.agent.session import BrowserSession
    from shadow_web.wrapper import ShadowPage

    created: Dict[str, Any] = {}

    def fake_shadow(self_page, **kwargs):
        created.update(kwargs)
        shadow = FakeShadow()
        return shadow

    monkeypatch.setattr(
        "shadow_web.agent.session.ShadowPage",
        lambda page, **kwargs: fake_shadow(page, **kwargs),
    )
    sess = BrowserSession(browser="camoufox")
    sess.browser_type = "camoufox"
    sess.page = object()
    sess._attach_shadow()
    assert created.get("capture_mode") == "dom"


def test_cli_browser_flag_defaults_camoufox():
    from shadow_web.agent.cli import _build_parser

    args = _build_parser().parse_args(["exec", "do thing"])
    assert args.browser == "camoufox"
    args2 = _build_parser().parse_args(["exec", "do thing", "--browser", "chromium"])
    assert args2.browser == "chromium"
