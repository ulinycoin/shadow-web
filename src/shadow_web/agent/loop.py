"""Observe → act → verify agent loop with single-call finish."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from .jev import JevSelector, observation_from
from .models import ChatMessage, ModelResponse, OpenAICompatibleAdapter
from .prompt import SYSTEM_PROMPT
from .session import BrowserSession
from .tools import TOOL_SCHEMAS, ToolResult, ToolRouter, parse_tool_arguments

logger = logging.getLogger(__name__)

# Default cumulative completion+prompt budget for one exec run.
DEFAULT_MAX_TOKENS = 15000

# Honest stop hints when a bounded run cannot finish a bulk job.
HINT_BUDGET = (
    "Bounded task run stopped (not a full-site scrape). "
    "Narrow the goal, or use thin tools: navigate → schema_session / "
    "content_outline(offset=…) pagination."
)
HINT_NO_PROGRESS = (
    "Agent repeated the same observation. Narrow the goal or switch to thin MCP tools."
)
HINT_PARTIAL = (
    "Answer is a task-sized slice, not the full dataset. "
    "Continue with thin tools + pagination (schema_session / content_outline offset)."
)


class SupportsChat(Protocol):
    def chat(
        self,
        messages: List[ChatMessage],
        tools: List[Dict[str, Any]],
    ) -> ModelResponse: ...


@dataclass
class AgentResult:
    ok: bool
    answer: Optional[str]
    steps: int
    usage: Dict[str, int] = field(default_factory=dict)
    url: str = ""
    browser: str = ""
    error: Optional[str] = None
    transcript: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    # Completeness contract: thick tool = bounded task, not bulk dump.
    stop_reason: str = "unknown"
    complete: bool = False
    truncated: bool = False
    next_hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "ok": self.ok,
            "answer": self.answer,
            "steps": self.steps,
            "usage": self.usage,
            "url": self.url,
            "stop_reason": self.stop_reason,
            "complete": self.complete,
            "truncated": self.truncated,
        }
        if self.browser:
            out["browser"] = self.browser
        if self.error:
            out["error"] = self.error
        if self.next_hint:
            out["next_hint"] = self.next_hint
        if self.evidence:
            out["evidence"] = self.evidence
        return out


def _result(
    *,
    ok: bool,
    answer: Optional[str],
    steps: int,
    usage: Dict[str, int],
    url: str,
    stop_reason: str,
    complete: bool,
    truncated: bool,
    error: Optional[str] = None,
    transcript: Optional[List[Dict[str, Any]]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    next_hint: Optional[str] = None,
) -> AgentResult:
    return AgentResult(
        ok=ok,
        answer=answer,
        steps=steps,
        usage=usage,
        url=url,
        error=error,
        transcript=transcript or [],
        evidence=evidence or {},
        stop_reason=stop_reason,
        complete=complete,
        truncated=truncated,
        next_hint=next_hint,
    )


@dataclass
class AgentLoop:
    """Core tool-calling loop. Browser/LLM injected for testability."""

    llm: SupportsChat
    tools: ToolRouter
    max_steps: int = 20
    max_tokens: int = DEFAULT_MAX_TOKENS
    no_progress_limit: int = 3
    system_prompt: str = SYSTEM_PROMPT
    # Optional Jev (TypeSafe System One) step selector. Injected for tests;
    # run_agent_task() builds it from env when SHADOW_WEB_AGENT_JEV=1.
    selector: Optional[JevSelector] = None
    max_selector_steps: int = 2
    selector_stats: Dict[str, Any] = field(default_factory=dict)

    def run(self, task: str) -> AgentResult:
        """Run the loop, recording selector telemetry in the result evidence.

        A provider failure (bad key, rate limit, timeout, outage) is part of the
        contract, not an exception: the caller gets the same envelope as any
        other unsuccessful run, with ``stop_reason="llm_error"``.
        """
        self.selector_stats = {
            "enabled": self.selector is not None,
            "steps_taken": 0,
            "fallbacks": 0,
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "picks": [],
            "rejected": [],
        }
        try:
            result = self._run(task)
        except Exception as exc:  # noqa: BLE001 - any provider/SDK failure
            logger.warning("agent run failed: %s", exc, exc_info=True)
            result = AgentResult(
                ok=False,
                answer=None,
                steps=0,
                error=f"{exc.__class__.__name__}: {exc}",
                stop_reason="llm_error",
                complete=False,
                truncated=False,
                next_hint=(
                    "check the API key, base URL and model id, then retry"
                    if "auth" in str(exc).lower() or "401" in str(exc)
                    else None
                ),
            )
        if self.selector is not None:
            evidence = dict(result.evidence or {})
            evidence["jev_selector"] = {
                k: v for k, v in self.selector_stats.items() if v not in (0, [], None)
            }
            result.evidence = evidence
        return result

    def _selector_response(
        self,
        task: str,
        observation: str,
        url: str,
        step: int,
    ) -> Optional[ModelResponse]:
        """Let Jev pre-empt the LLM step when it is positive and confident."""
        assert self.selector is not None
        if self.selector_stats.get("steps_taken", 0) >= self.max_selector_steps:
            return None
        decision = self.selector.select(goal=task, observation=observation, url=url)
        self.selector_stats["calls"] = self.selector_stats.get("calls", 0) + 1
        if decision is not None:
            self.selector_stats["prompt_tokens"] = self.selector_stats.get("prompt_tokens", 0) + int(
                decision.usage.get("input_tokens", 0)
            )
            self.selector_stats["completion_tokens"] = self.selector_stats.get(
                "completion_tokens", 0
            ) + int(decision.usage.get("output_tokens", 0))
        if decision is None or not decision.usable(self.selector.threshold):
            self.selector_stats["fallbacks"] = self.selector_stats.get("fallbacks", 0) + 1
            reason = (
                f"skip:{self.selector.last_skip_reason}"
                if decision is None and self.selector.last_skip_reason
                else ("error" if decision is None else decision.describe())
            )
            self.selector_stats.setdefault("rejected", []).append(reason)
            return None
        self.selector_stats["steps_taken"] = self.selector_stats.get("steps_taken", 0) + 1
        self.selector_stats.setdefault("picks", []).append(decision.describe())
        return ModelResponse(
            content=None,
            tool_calls=[
                {
                    "id": f"jev-{step}",
                    "type": "function",
                    "function": {
                        "name": decision.name,
                        "arguments": json.dumps(decision.args),
                    },
                }
            ],
            usage={},
        )

    def _run(self, task: str) -> AgentResult:
        messages: List[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt),
            ChatMessage(role="user", content=task),
        ]
        usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        transcript: List[Dict[str, Any]] = []
        last_sig: Optional[str] = None
        last_observation: Optional[str] = None
        stagnant = 0
        steps = 0
        url = lambda: self.tools.shadow.last_url or ""

        for _ in range(self.max_steps):
            steps += 1
            response: Optional[ModelResponse] = None
            if self.selector is not None and last_observation:
                response = self._selector_response(task, last_observation, url(), steps)
                if response is not None:
                    # Consumed: the acted-on observation must not be reused.
                    last_observation = None
            if response is None:
                response = self.llm.chat(messages, TOOL_SCHEMAS)
            for key in usage_totals:
                usage_totals[key] += int(response.usage.get(key, 0))

            if self.max_tokens > 0 and usage_totals["total_tokens"] >= self.max_tokens:
                return _result(
                    ok=False,
                    answer=None,
                    steps=steps,
                    usage=usage_totals,
                    url=url(),
                    stop_reason="max_tokens",
                    complete=False,
                    truncated=True,
                    error=f"Exceeded max_tokens ({self.max_tokens})",
                    transcript=transcript,
                    next_hint=HINT_BUDGET,
                )

            if not response.tool_calls:
                # Model tried to answer in plain text — accept as finish.
                answer = (response.content or "").strip()
                if answer:
                    return _result(
                        ok=True,
                        answer=answer,
                        steps=steps,
                        usage=usage_totals,
                        url=url(),
                        stop_reason="plain_text",
                        complete=True,
                        truncated=False,
                        transcript=transcript,
                    )
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            "No tools called and no answer. "
                            "Call a tool or done(finalAnswer, complete=…)."
                        ),
                    )
                )
                continue

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            finished: Optional[ToolResult] = None
            results_collected: list[tuple[dict[str, Any], ToolResult]] = []

            for tc in response.tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = parse_tool_arguments(fn.get("arguments"))
                except ValueError as exc:
                    result = ToolResult(
                        name=name or "unknown",
                        ok=False,
                        content=json.dumps({"error": str(exc)}),
                    )
                else:
                    result = self.tools.dispatch(name, args)
                results_collected.append((tc, result))

            # Append all tool messages first (required by OpenAI-compatible
            # protocol), then one combined user nudge afterwards.
            nudges: list[str] = []
            for tc, result in results_collected:
                entry: Dict[str, Any] = {
                    "step": steps,
                    "tool": result.name,
                    "ok": result.ok,
                    "terminal": result.terminal,
                }
                if str(tc.get("id") or "").startswith("jev-"):
                    entry["by"] = "jev"
                transcript.append(entry)
                observed = observation_from(result)
                if observed:
                    last_observation = observed
                messages.append(
                    ChatMessage(
                        role="tool",
                        tool_call_id=tc.get("id"),
                        name=result.name,
                        content=result.content,
                    )
                )
                if result.nudge:
                    nudges.append(result.nudge)

                sig = f"{result.name}:{result.content}"
                if sig == last_sig:
                    stagnant += 1
                else:
                    stagnant = 1
                    last_sig = sig

                if result.terminal:
                    finished = result
                    break

                if stagnant >= self.no_progress_limit:
                    return _result(
                        ok=False,
                        answer=None,
                        steps=steps,
                        usage=usage_totals,
                        url=url(),
                        stop_reason="no_progress",
                        complete=False,
                        truncated=True,
                        error=(
                            f"No progress after {self.no_progress_limit} "
                            "identical tool results"
                        ),
                        transcript=transcript,
                        next_hint=HINT_NO_PROGRESS,
                    )

            if finished is None and nudges:
                # Deduplicate order-preserving — same nudge from multiple
                # tools (schema_page + snapshot) shouldn't repeat.
                seen: set[str] = set()
                unique: list[str] = []
                for n in nudges:
                    if n not in seen:
                        seen.add(n)
                        unique.append(n)
                combined = " · ".join(unique)
                messages.append(ChatMessage(role="user", content=combined))

            if finished is not None:
                evidence = dict(finished.data or {})
                complete = bool(evidence.pop("complete", True))
                next_hint = evidence.pop("next_hint", None)
                if not complete and not next_hint:
                    next_hint = HINT_PARTIAL
                return _result(
                    ok=True,
                    answer=finished.final_answer,
                    steps=steps,
                    usage=usage_totals,
                    url=url(),
                    stop_reason="done",
                    complete=complete,
                    truncated=not complete,
                    transcript=transcript,
                    evidence=evidence,
                    next_hint=next_hint,
                )

            if self.max_tokens > 0 and usage_totals["total_tokens"] >= self.max_tokens:
                return _result(
                    ok=False,
                    answer=None,
                    steps=steps,
                    usage=usage_totals,
                    url=url(),
                    stop_reason="max_tokens",
                    complete=False,
                    truncated=True,
                    error=f"Exceeded max_tokens ({self.max_tokens})",
                    transcript=transcript,
                    next_hint=HINT_BUDGET,
                )

        return _result(
            ok=False,
            answer=None,
            steps=steps,
            usage=usage_totals,
            url=url(),
            stop_reason="max_steps",
            complete=False,
            truncated=True,
            error=f"Exceeded max_steps ({self.max_steps})",
            transcript=transcript,
            next_hint=HINT_BUDGET,
        )


def run_agent_task(
    task: str,
    *,
    model: str = "deepseek-chat",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_steps: int = 20,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    headed: bool = False,
    session: Optional[str] = None,
    heal_api_url: Optional[str] = None,
    browser_engine: str = "camoufox",
    llm: Optional[SupportsChat] = None,
    tools: Optional[ToolRouter] = None,
    browser: Optional[BrowserSession] = None,
    selector: Optional[JevSelector] = None,
) -> AgentResult:
    """
    Run a browser task to completion and return one result envelope.

    Owns browser lifecycle unless ``browser`` / ``tools`` are injected (tests).
    Default browser engine is Camoufox (falls back to Chromium if unavailable).
    """
    owns_browser = browser is None and tools is None
    session_obj = browser
    router = tools

    try:
        if router is None:
            if session_obj is None:
                session_obj = BrowserSession(
                    headed=headed,
                    session_name=session,
                    heal_api_url=heal_api_url,
                    browser=browser_engine,
                )
                session_obj.start()
            assert session_obj.shadow is not None
            router = ToolRouter(session_obj.shadow)

        adapter: SupportsChat = llm or OpenAICompatibleAdapter(
            model,
            api_key=api_key,
            base_url=base_url,
        )
        selected = selector if selector is not None else JevSelector.from_env()
        max_selector_steps = 2
        if selected is not None:
            try:
                max_selector_steps = int(os.environ.get("SHADOW_WEB_JEV_MAX_STEPS", "2"))
            except (TypeError, ValueError):
                max_selector_steps = 2
        loop = AgentLoop(
            llm=adapter,
            tools=router,
            max_steps=max_steps,
            max_tokens=max_tokens,
            selector=selected,
            max_selector_steps=max_selector_steps,
        )
        result = loop.run(task)
        if session_obj is not None:
            result.browser = getattr(session_obj, "browser_type", "") or ""
            if getattr(session_obj, "browser_fallback", False):
                evidence = dict(result.evidence or {})
                evidence["browser_fallback"] = True
                evidence["requested_browser"] = getattr(
                    session_obj, "requested_browser", ""
                )
                result.evidence = evidence
        return result
    finally:
        if owns_browser and session_obj is not None:
            session_obj.close()
