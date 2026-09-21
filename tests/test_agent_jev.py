"""Unit tests for the Jev (TypeSafe System One) gated step selector (no network)."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from shadow_web.agent.jev import JevSelector, observation_from, parse_action_lines
from shadow_web.agent.loop import AgentLoop
from shadow_web.agent.models import ChatMessage, ModelResponse
from shadow_web.agent.tools import ToolResult, ToolRouter

OBSERVATION = "\n".join(
    [
        "# page https://example.com (3 actions, class=Static)",
        "@1 link More information... [Main]",
        "@2 a Pricing [Header]",
        "@3 button Search [Header]",
    ]
)


class FakeShadow:
    def __init__(self):
        self.last_url = "https://example.com"
        self.action_map: List[Dict[str, Any]] = [
            {"id": "1", "type": "link", "label": "More information...", "group": "Main"}
        ]
        self.action_groups = [{"name": "Main", "elements": self.action_map}]
        self.clean_html = "<html><body></body></html>"
        self.last_diff = None
        self.page_class = "Static"
        self.page = type("P", (), {"title": lambda self: "Example Domain"})()


class ScriptedLLM:
    def __init__(self, responses: List[ModelResponse]):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages: List[ChatMessage], tools: List[Dict[str, Any]]) -> ModelResponse:
        if self.calls >= len(self.responses):
            raise AssertionError(f"LLM called {self.calls + 1} times, only {len(self.responses)} scripted")
        resp = self.responses[self.calls]
        self.calls += 1
        return resp


def _tool_call(name: str, args: Dict[str, Any], call_id: str = "c1") -> Dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _jev_payload(choice: str, *, probability: float = 0.9, confidence: float = 0.9, presence: float = 0.9) -> Dict[str, Any]:
    return {
        "answers": {
            "step_exists": {"noul": presence},
            "next_step": {
                "choice": choice,
                "probabilities": {choice: probability},
                "confidence": confidence,
            },
        },
        "usage": {"input_tokens": 800, "output_tokens": 0},
    }


def _selector(payload):
    """Selector with a stubbed transport (no network, no API key needed)."""
    return JevSelector(api_key="test", transport=lambda _p: payload)


# ---------------------------------------------------------------------------
# observation parsing
# ---------------------------------------------------------------------------

def test_parse_action_lines_reads_terse_rows():
    rows = parse_action_lines(OBSERVATION)
    assert [r["sid"] for r in rows] == ["1", "2", "3"]
    assert rows[1]["label"] == "Pricing"
    assert rows[0]["kind"] == "link"


def test_parse_action_lines_ignores_header_and_prose():
    assert parse_action_lines("# page x\nplain text\n") == []


def test_observation_from_reads_tool_result_data():
    res = ToolResult(name="navigate", ok=True, content="{}", data={"observation": OBSERVATION})
    assert observation_from(res) == OBSERVATION
    assert observation_from(ToolResult(name="done", ok=True, content="{}")) is None


# ---------------------------------------------------------------------------
# selector behaviour
# ---------------------------------------------------------------------------

def test_from_env_disabled_without_flag():
    assert JevSelector.from_env({"TYPESAFE_API_KEY": "k"}) is None
    assert JevSelector.from_env({"SHADOW_WEB_AGENT_JEV": "1"}) is None  # no key


def test_from_env_enabled_with_flag_and_key():
    sel = JevSelector.from_env(
        {"SHADOW_WEB_AGENT_JEV": "1", "TYPESAFE_API_KEY": "k", "SHADOW_WEB_JEV_THRESHOLD": "0.7"}
    )
    assert sel is not None and sel.threshold == 0.7


def test_candidates_include_tools_and_clicks():
    options = _selector(_jev_payload("tool:schema_page")).candidates_for(OBSERVATION)
    assert "tool:content_outline" in options
    assert "click:2" in options and "Pricing" in options["click:2"]
    assert "tool:done" not in options


def test_select_returns_gated_decision_for_tool():
    decision = _selector(_jev_payload("tool:schema_page")).select(
        goal="get the price table", observation=OBSERVATION, url="https://example.com"
    )
    assert decision is not None
    assert decision.name == "schema_page"
    assert decision.args == {}
    assert decision.usable(0.6) is True


def test_select_returns_gated_decision_for_click():
    decision = _selector(_jev_payload("click:2")).select(goal="open pricing", observation=OBSERVATION)
    assert decision is not None
    assert decision.name == "click"
    assert decision.args == {"sid": "2"}
    assert decision.usable(0.6) is True


def test_select_not_usable_when_unconfident():
    decision = _selector(_jev_payload("click:2", confidence=0.3)).select(
        goal="open pricing", observation=OBSERVATION
    )
    assert decision is not None
    assert decision.usable(0.6) is False


def test_select_not_usable_on_empty_page_signal():
    """Negative control: the page holds nothing for the task (presence ~0)."""
    decision = _selector(_jev_payload("click:1", presence=0.02, probability=1.0)).select(
        goal="log in", observation=OBSERVATION
    )
    assert decision is not None
    assert decision.usable(0.6) is False


def test_select_usable_when_presence_is_low_but_pick_is_confident():
    """presence is a floor, not a gate — measured 0.33 presence on a correct pick."""
    decision = _selector(_jev_payload("tool:content_outline", presence=0.33)).select(
        goal="report the title", observation=OBSERVATION
    )
    assert decision is not None
    assert decision.usable(0.6) is True


def test_select_needs_a_real_action_map():
    body_only = "# page x (0 actions, class=Static)"
    assert _selector(_jev_payload("click:1")).select(goal="open pricing", observation=body_only) is None


def test_select_fails_open_on_transport_error():
    def boom(_payload):
        raise RuntimeError("network down")

    sel = JevSelector(api_key="test", transport=boom)
    assert sel.select(goal="x", observation=OBSERVATION) is None


def test_select_fails_open_on_malformed_payload():
    sel = JevSelector(api_key="test", transport=lambda _p: {"answers": {}})
    assert sel.select(goal="x", observation=OBSERVATION) is None


def test_select_fails_open_on_unknown_choice():
    sel = _selector(_jev_payload("tool:teleport"))
    assert sel.select(goal="x", observation=OBSERVATION) is None


# ---------------------------------------------------------------------------
# loop integration
# ---------------------------------------------------------------------------

def _router(shadow, seen: List[Any], observation: str = OBSERVATION) -> ToolRouter:
    router = ToolRouter(shadow)  # type: ignore[arg-type]

    def _dispatch(name: str, arguments: Dict[str, Any]) -> ToolResult:
        seen.append((name, arguments))
        if name == "done":
            return ToolResult(
                name="done",
                ok=True,
                content=json.dumps({"finalAnswer": arguments["finalAnswer"]}),
                terminal=True,
                final_answer=arguments["finalAnswer"],
                data={"url": shadow.last_url, "complete": True},
            )
        if name == "navigate":
            return ToolResult(
                name="navigate",
                ok=True,
                content=observation,
                data={"observation": observation, "url": shadow.last_url},
            )
        if name == "content_outline":
            return ToolResult(
                name="content_outline",
                ok=True,
                content="Example Domain — an illustrative page.",
                data={"url": shadow.last_url},
            )
        if name == "click":
            return ToolResult(
                name="click",
                ok=True,
                content="# diff: (none)",
                data={"url": shadow.last_url},
            )
        raise AssertionError(f"unexpected tool {name}")

    router.dispatch = _dispatch  # type: ignore[method-assign]
    return router


def test_loop_lets_jev_take_the_step_and_skips_the_llm():
    llm = ScriptedLLM(
        [
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("navigate", {"url": "https://example.com"}, "t1")],
                usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            ),
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("done", {"finalAnswer": "Example Domain"}, "t2")],
                usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            ),
        ]
    )
    shadow = FakeShadow()
    seen: List[Any] = []
    router = _router(shadow, seen)
    loop = AgentLoop(
        llm=llm,
        tools=router,
        max_steps=5,
        selector=_selector(_jev_payload("tool:content_outline")),
    )
    result = loop.run("What is the title of example.com?")

    assert result.ok is True and result.answer == "Example Domain"
    assert llm.calls == 2, "the LLM must be skipped for the selector step"
    assert [name for name, _ in seen] == ["navigate", "content_outline", "done"]
    jev_steps = [t for t in result.transcript if t.get("by") == "jev"]
    assert len(jev_steps) == 1 and jev_steps[0]["tool"] == "content_outline"
    assert result.evidence["jev_selector"]["steps_taken"] == 1
    assert result.evidence["jev_selector"]["calls"] == 1
    assert result.evidence["jev_selector"]["prompt_tokens"] == 800


def test_loop_falls_back_to_llm_when_unconfident():
    llm = ScriptedLLM(
        [
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("navigate", {"url": "https://example.com"}, "t1")],
                usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            ),
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("done", {"finalAnswer": "Example Domain"}, "t2")],
                usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            ),
        ]
    )
    shadow = FakeShadow()
    seen: List[Any] = []
    router = _router(shadow, seen)
    loop = AgentLoop(
        llm=llm,
        tools=router,
        max_steps=5,
        selector=_selector(_jev_payload("tool:content_outline", presence=0.05, confidence=0.05)),
    )
    result = loop.run("What is the title of example.com?")

    assert llm.calls == 2
    assert [name for name, _ in seen] == ["navigate", "done"]
    assert not [t for t in result.transcript if t.get("by") == "jev"]
    stats = result.evidence["jev_selector"]
    assert stats["fallbacks"] == 1
    assert "steps_taken" not in stats


def test_loop_without_selector_is_unchanged():
    llm = ScriptedLLM(
        [
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("navigate", {"url": "https://example.com"}, "t1")],
                usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            ),
            ModelResponse(
                content=None,
                tool_calls=[_tool_call("done", {"finalAnswer": "Example Domain"}, "t2")],
                usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            ),
        ]
    )
    shadow = FakeShadow()
    seen: List[Any] = []
    router = _router(shadow, seen)
    result = AgentLoop(llm=llm, tools=router, max_steps=5).run("title?")
    assert llm.calls == 2
    assert "jev_selector" not in result.evidence
    assert all("by" not in t for t in result.transcript)
