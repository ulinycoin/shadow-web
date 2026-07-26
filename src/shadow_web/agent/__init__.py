"""Standalone agent loop: observe → act → verify → done."""

from .loop import AgentResult, run_agent_task

__all__ = ["AgentResult", "run_agent_task"]
