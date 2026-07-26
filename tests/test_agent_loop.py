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

    def query(self, q: str, *, fmt: str = "result"):
        from shadow_web.query import QueryResult

        matches = [a for a in self.action_map if q.lower() in json.dumps(a).lower()]
        result = QueryResult(query=q, matches=matches, total=len(self.action_map))
        if fmt == "terse":
            return result.terse()
        return result


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
            data={"url": shadow.last_url, "complete": True},
        )

    router.dispatch = _done_only  # type: ignore[method-assign]
    result = AgentLoop(llm=llm, tools=router, max_steps=5).run("What is the title?")
    assert result.ok is True
    assert result.answer == "Example Domain"
    assert result.steps == 1
    assert result.usage["total_tokens"] == 15
    assert llm.calls == 1
    assert result.complete is True
    assert result.truncated is False
    assert result.stop_reason == "done"
    payload = result.to_dict()
    assert payload["complete"] is True
    assert payload["truncated"] is False
    assert payload["stop_reason"] == "done"


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
    assert result.stop_reason == "no_progress"
    assert result.complete is False
    assert result.truncated is True
    assert result.next_hint


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
    assert result.stop_reason == "max_steps"
    assert result.truncated is True
    assert result.complete is False
    assert "thin" in (result.next_hint or "").lower() or "Bound" in (result.next_hint or "")


def test_plain_text_finish():
    llm = ScriptedLLM(
        [ModelResponse(content="The answer is 42", tool_calls=[], usage={})]
    )
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    result = AgentLoop(llm=llm, tools=router).run("what?")
    assert result.ok is True
    assert result.answer == "The answer is 42"
    assert result.stop_reason == "plain_text"
    assert result.complete is True


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

    sess = FakeSession(browser="camoufox", allow_chromium_fallback_flag=True)
    shadow = sess.start()
    assert calls == ["camoufox", "chromium"]
    assert sess.browser_type == "chromium"
    assert sess.browser_fallback is True
    assert shadow is sess.shadow


def test_camoufox_fail_loud_without_fallback():
    from shadow_web.agent import session as session_mod

    class FakeSession(session_mod.BrowserSession):
        def _start_camoufox(self, headless: bool):
            raise RuntimeError("no camoufox binary")

    sess = FakeSession(browser="camoufox", allow_chromium_fallback_flag=False)
    try:
        sess.start()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "Camoufox unavailable" in str(exc)
        assert "ALLOW_CHROMIUM_FALLBACK" in str(exc)


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
    args3 = _build_parser().parse_args(["exec", "do thing", "--max-tokens", "8000"])
    assert args3.max_tokens == 8000


def test_dense_observe_auto_minimal():
    from shadow_web.agent.tools import DENSE_ACTION_THRESHOLD, _observe

    shadow = FakeShadow()
    shadow.action_map = [
        {"id": str(i), "type": "link", "label": f"Item {i}", "group": "Cat"}
        for i in range(DENSE_ACTION_THRESHOLD + 5)
    ]
    shadow.action_groups = [{"name": "Cat", "elements": shadow.action_map}]
    payload = _observe(shadow, diff=False)
    assert payload["detail"] == "minimal"
    assert payload["action_count"] == DENSE_ACTION_THRESHOLD + 5
    assert "hint" in payload
    assert "Dense page" in payload["hint"]
    # only top-10 action lines + header (+ maybe overflow line)
    lines = [ln for ln in payload["observation"].splitlines() if ln.startswith("@")]
    assert len(lines) == 10


def test_schema_page_nudge_when_payload():
    shadow = FakeShadow()
    shadow.clean_html = (
        "<html><body><form action='/s'><input name='q'/></form>"
        "<ul><li>Brand A</li><li>Brand B</li></ul></body></html>"
    )
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    result = router.dispatch("schema_page", {})
    assert result.ok is True
    assert result.nudge is not None
    assert "done(finalAnswer)" in result.nudge


def test_schema_nudge_injected_into_messages():
    llm = ScriptedLLM(
        [
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("schema_page", {}, "t1")],
                usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            ),
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("done", {"finalAnswer": "ok"}, "t2")],
                usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            ),
        ]
    )
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    seen: List[List[ChatMessage]] = []

    class CaptureLLM:
        def chat(self, messages, tools):
            seen.append(list(messages))
            return llm.chat(messages, tools)

    def _dispatch(name: str, arguments: Dict[str, Any]) -> ToolResult:
        if name == "schema_page":
            return ToolResult(
                name="schema_page",
                ok=True,
                content='{"schema":{"forms":[1]}}',
                nudge="schema_page returned structured data. call done now",
            )
        return ToolResult(
            name="done",
            ok=True,
            content="{}",
            terminal=True,
            final_answer=arguments.get("finalAnswer"),
        )

    router.dispatch = _dispatch  # type: ignore[method-assign]
    result = AgentLoop(llm=CaptureLLM(), tools=router, max_steps=5).run("list brands")
    assert result.ok is True
    assert any(
        m.role == "user" and m.content and "done" in m.content
        for batch in seen[1:]
        for m in batch
    )


def test_max_tokens_stops_loop():
    resp = ModelResponse(
        content=None,
        tool_calls=[_tool_call("snapshot", {"detail": "terse"}, "t1")],
        usage={"prompt_tokens": 4000, "completion_tokens": 1000, "total_tokens": 5000},
    )
    llm = ScriptedLLM([resp, resp, resp])
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    n = {"i": 0}

    def _snap(name: str, arguments: Dict[str, Any]) -> ToolResult:
        n["i"] += 1
        return ToolResult(name="snapshot", ok=True, content=json.dumps({"n": n["i"]}))

    router.dispatch = _snap  # type: ignore[method-assign]
    result = AgentLoop(llm=llm, tools=router, max_steps=10, max_tokens=6000).run("x")
    assert result.ok is False
    assert result.error and "max_tokens" in result.error
    assert result.usage["total_tokens"] >= 5000
    assert result.stop_reason == "max_tokens"
    assert result.truncated is True


def test_done_partial_completeness():
    llm = ScriptedLLM(
        [
            ModelResponse(
                content=None,
                tool_calls=[
                    _tool_call(
                        "done",
                        {
                            "finalAnswer": "Top 3: A, B, C",
                            "complete": False,
                            "next_hint": "content_outline offset=20",
                        },
                        "t1",
                    )
                ],
                usage={},
            )
        ]
    )
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    result = AgentLoop(llm=llm, tools=router).run("list products")
    assert result.ok is True
    assert result.answer == "Top 3: A, B, C"
    assert result.complete is False
    assert result.truncated is True
    assert result.stop_reason == "done"
    assert result.next_hint == "content_outline offset=20"
    assert "complete" not in result.evidence
    assert "next_hint" not in result.evidence


def test_tool_router_done_partial_flags():
    shadow = FakeShadow()
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    done = router.dispatch(
        "done",
        {"finalAnswer": "slice", "complete": False, "next_hint": "page 2"},
    )
    assert done.terminal is True
    body = json.loads(done.content)
    assert body["complete"] is False
    assert body["next_hint"] == "page 2"
    assert done.data["complete"] is False


def test_shadow_query_tool():
    shadow = FakeShadow()
    shadow.action_map = [
        {"id": "1", "type": "link", "label": "Nike Air", "group": "Brands"},
        {"id": "2", "type": "link", "label": "Other", "group": "Brands"},
    ]
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    result = router.dispatch("shadow_query", {"query": "nike", "limit": 5})
    assert result.ok is True
    body = json.loads(result.content)
    assert body["count"] >= 1
    assert "Nike" in body["observation"]


def test_schema_empty_nudges_content_outline():
    from shadow_web.agent.tools import SCHEMA_EMPTY_NUDGE

    shadow = FakeShadow()
    shadow.clean_html = "<html><body><div>SPA shell</div></body></html>"
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    result = router.dispatch("schema_page", {})
    assert result.nudge == SCHEMA_EMPTY_NUDGE


def test_schema_page_compacts_mega_lists():
    from shadow_web.agent.tools import compact_schema_for_agent

    blob = "{" + "&amp;quot;widgets&amp;quot;:" * 20 + "x" * 500
    huge = {
        "tables": [],
        "forms": [{"action": "/s", "method": "GET", "fields": [{"name": "q"}], "field_count": 1}],
        "lists": [{"type": "unordered", "items": [blob, "Piens 1l"] + [f"nav{i}" for i in range(30)], "total": 40}],
    }
    compact = compact_schema_for_agent(huge)
    assert len(compact["lists"]) <= 5
    assert len(compact["lists"][0]["items"]) <= 8
    assert all(len(str(i)) <= 80 for i in compact["lists"][0]["items"])


def test_content_outline_and_blocks():
    from shadow_web.agent.tools import CONTENT_BLOCKS_NUDGE, CONTENT_BLOCKS_REPEAT_NUDGE

    shadow = FakeShadow()
    shadow.clean_html = """
    <html><body>
      <h1>Books</h1>
      <p>Nike Air Max — 55,45 €</p>
      <p>Birkenstock Arizona — 99,95 €</p>
      <div><span>Polo Ralph Lauren</span><span>85 €</span></div>
    </body></html>
    """
    router = ToolRouter(shadow)  # type: ignore[arg-type]
    outline = router.dispatch("content_outline", {"max_tokens": 400})
    assert outline.ok is True
    body = json.loads(outline.content)
    assert body["block_count"] >= 1
    assert body["outline"]
    assert outline.nudge is not None

    # Pick first pN id from outline lines
    block_id = None
    for line in body["outline"].splitlines():
        if "|" in line and line.strip()[0] == "p":
            block_id = line.split("|", 1)[0].strip()
            break
    assert block_id
    blocks = router.dispatch("content_blocks", {"ids": block_id, "max_tokens": 800})
    assert blocks.ok is True
    fetched = json.loads(blocks.content)["blocks"]
    assert block_id in fetched
    assert blocks.nudge == CONTENT_BLOCKS_NUDGE

    # Second fetch on same page is blocked (prevents token blowups).
    again = router.dispatch("content_blocks", {"ids": block_id})
    assert again.ok is False
    assert again.nudge == CONTENT_BLOCKS_REPEAT_NUDGE
    assert json.loads(again.content).get("blocked") is True
