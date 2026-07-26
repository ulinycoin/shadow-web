"""Observe → act → verify agent loop with single-call finish."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from .models import ChatMessage, ModelResponse, OpenAICompatibleAdapter
from .prompt import SYSTEM_PROMPT
from .session import BrowserSession
from .tools import TOOL_SCHEMAS, ToolResult, ToolRouter, parse_tool_arguments


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

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "ok": self.ok,
            "answer": self.answer,
            "steps": self.steps,
            "usage": self.usage,
            "url": self.url,
        }
        if self.browser:
            out["browser"] = self.browser
        if self.error:
            out["error"] = self.error
        if self.evidence:
            out["evidence"] = self.evidence
        return out


@dataclass
class AgentLoop:
    """Core tool-calling loop. Browser/LLM injected for testability."""

    llm: SupportsChat
    tools: ToolRouter
    max_steps: int = 20
    no_progress_limit: int = 3
    system_prompt: str = SYSTEM_PROMPT

    def run(self, task: str) -> AgentResult:
        messages: List[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt),
            ChatMessage(role="user", content=task),
        ]
        usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        transcript: List[Dict[str, Any]] = []
        last_sig: Optional[str] = None
        stagnant = 0
        steps = 0

        for _ in range(self.max_steps):
            steps += 1
            response = self.llm.chat(messages, TOOL_SCHEMAS)
            for key in usage_totals:
                usage_totals[key] += int(response.usage.get(key, 0))

            if not response.tool_calls:
                # Model tried to answer in plain text — accept as finish.
                answer = (response.content or "").strip()
                if answer:
                    return AgentResult(
                        ok=True,
                        answer=answer,
                        steps=steps,
                        usage=usage_totals,
                        url=self.tools.shadow.last_url or "",
                        transcript=transcript,
                    )
                messages.append(
                    ChatMessage(
                        role="user",
                        content="No tools called and no answer. Call a tool or done(finalAnswer).",
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

                transcript.append(
                    {
                        "step": steps,
                        "tool": result.name,
                        "ok": result.ok,
                        "terminal": result.terminal,
                    }
                )
                messages.append(
                    ChatMessage(
                        role="tool",
                        tool_call_id=tc.get("id"),
                        name=result.name,
                        content=result.content,
                    )
                )

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
                    return AgentResult(
                        ok=False,
                        answer=None,
                        steps=steps,
                        usage=usage_totals,
                        url=self.tools.shadow.last_url or "",
                        error=f"No progress after {self.no_progress_limit} identical tool results",
                        transcript=transcript,
                    )

            if finished is not None:
                return AgentResult(
                    ok=True,
                    answer=finished.final_answer,
                    steps=steps,
                    usage=usage_totals,
                    url=self.tools.shadow.last_url or "",
                    transcript=transcript,
                    evidence=finished.data or {},
                )

        return AgentResult(
            ok=False,
            answer=None,
            steps=steps,
            usage=usage_totals,
            url=self.tools.shadow.last_url or "",
            error=f"Exceeded max_steps ({self.max_steps})",
            transcript=transcript,
        )


def run_agent_task(
    task: str,
    *,
    model: str = "deepseek-chat",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_steps: int = 20,
    headed: bool = False,
    session: Optional[str] = None,
    heal_api_url: Optional[str] = None,
    browser_engine: str = "camoufox",
    llm: Optional[SupportsChat] = None,
    tools: Optional[ToolRouter] = None,
    browser: Optional[BrowserSession] = None,
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
        loop = AgentLoop(llm=adapter, tools=router, max_steps=max_steps)
        result = loop.run(task)
        if session_obj is not None:
            result.browser = getattr(session_obj, "browser_type", "") or ""
        return result
    finally:
        if owns_browser and session_obj is not None:
            session_obj.close()
