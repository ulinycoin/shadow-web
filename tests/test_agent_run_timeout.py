"""mcp.agent_run: a wall-clock timeout must not leave work running behind it.

A Python thread cannot be cancelled, so ``asyncio.wait_for(asyncio.to_thread(...))``
used to release the agent semaphore while the browser kept running: the next run
started next to the "timed-out" one, and real concurrency exceeded the cap.
These tests pin the two halves of the fix - the stop hook reaching the loop, and
the slot being held until the worker has actually exited.
"""

from __future__ import annotations

import asyncio
import time

from shadow_web.mcp import server as mcp_server


class _FakeResult:
    """Stand-in for AgentResult: only to_dict() is used by agent_run."""

    def to_dict(self):
        return {"ok": True, "stop_reason": "done", "complete": True, "truncated": False}


def _agent_run_fn():
    mcp = mcp_server.create_mcp_server()
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    return tools["agent_run"].fn


def test_agent_run_passes_a_stop_hook_to_the_loop(monkeypatch):
    import shadow_web.agent.loop as loop_mod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(mcp_server, "_agent_semaphore", None)

    seen: dict = {}

    def fake_run(task, **kwargs):
        seen.update(kwargs)
        seen["task"] = task
        return _FakeResult()

    monkeypatch.setattr(loop_mod, "run_agent_task", fake_run)

    out = asyncio.run(_agent_run_fn()(goal="check the title"))

    assert out["ok"] is True
    assert seen["task"] == "check the title"
    assert callable(seen.get("should_stop")), "the loop needs a way to unwind"


def test_timed_out_run_holds_the_slot_until_the_worker_exits(monkeypatch):
    """The second run must not start while the first worker is still alive."""
    import shadow_web.agent.loop as loop_mod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(mcp_server, "_AGENT_RUN_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(mcp_server, "_AGENT_CANCEL_GRACE_SEC", 5.0)
    monkeypatch.setattr(mcp_server, "_agent_semaphore", None)

    events: list[tuple[str, float]] = []
    worker_seconds = 0.4

    def fake_run(task, **kwargs):
        start = time.monotonic()
        # Simulates a worker stuck inside a browser/LLM call: it cannot observe
        # should_stop() until it returns.
        time.sleep(worker_seconds)
        events.append(("start", start))
        events.append(("end", time.monotonic()))
        return _FakeResult()

    monkeypatch.setattr(loop_mod, "run_agent_task", fake_run)
    fn = _agent_run_fn()

    async def _both():
        return await asyncio.gather(fn(goal="one"), fn(goal="two"))

    results = asyncio.run(_both())

    assert [r["stop_reason"] for r in results] == ["timeout", "timeout"]
    assert all(r["truncated"] is True for r in results)
    assert all(r["ok"] is False for r in results)

    starts = sorted(when for kind, when in events if kind == "start")
    ends = sorted(when for kind, when in events if kind == "end")
    assert len(starts) == 2 and len(ends) == 2
    assert starts[1] >= ends[0] - 0.01, (
        "a second agent run started while the timed-out worker still owned a browser"
    )


def test_a_worker_that_finishes_in_time_returns_its_result(monkeypatch):
    import shadow_web.agent.loop as loop_mod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(mcp_server, "_agent_semaphore", None)
    monkeypatch.setattr(loop_mod, "run_agent_task", lambda task, **kw: _FakeResult())

    out = asyncio.run(_agent_run_fn()(goal="quick"))

    assert out == {"ok": True, "stop_reason": "done", "complete": True, "truncated": False}
