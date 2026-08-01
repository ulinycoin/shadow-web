#!/usr/bin/env python3
"""End-to-end agent task benchmark — v0.4.1 candidate (2026-08-01).

Measures *task success*, not HTML compression ratio.
Compares shadow-web exec against a plain-HTML baseline.

Usage:
  DEEPSEEK_API_KEY=sk-... python benchmarks/agent_tasks.py
  python benchmarks/agent_tasks.py --dry-run        # schema-only, no LLM

Output: benchmarks/results/agent_tasks_<timestamp>.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure src is in path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "src"))


# ---------------------------------------------------------------------------
# Task definitions — bounded, answerable in 1-3 turns
# ---------------------------------------------------------------------------

TASKS: List[Dict[str, Any]] = [
    {
        "id": "static-title",
        "category": "static",
        "url": "https://example.com",
        "goal": (
            "Navigate to https://example.com, then tell me: "
            "What is the heading text of this page?"
        ),
        "expected_contains": ["Example Domain"],
        "max_steps": 5,
    },
    {
        "id": "wikipedia-fact",
        "category": "text-heavy",
        "url": "https://en.wikipedia.org/wiki/Web_scraping",
        "goal": (
            "Navigate to https://en.wikipedia.org/wiki/Web_scraping, then: "
            "What are the main techniques listed for web scraping? "
            "List up to 3."
        ),
        "expected_contains": [],
        "max_steps": 8,
    },
    {
        "id": "hn-top-story",
        "category": "table",
        "url": "https://news.ycombinator.com",
        "goal": (
            "Navigate to https://news.ycombinator.com, then: "
            "What is the title of the top story on Hacker News right now?"
        ),
        "expected_contains": [],
        "max_steps": 10,
    },
    {
        "id": "github-trending-count",
        "category": "spa",
        "url": "https://github.com/trending",
        "goal": (
            "Navigate to https://github.com/trending, then: "
            "How many trending repositories are shown on this page? "
            "Return the count and name the first one."
        ),
        "expected_contains": [],
        "max_steps": 12,
    },
    {
        "id": "form-detection",
        "category": "form",
        "url": "https://www.w3schools.com/html/html_forms.asp",
        "goal": (
            "Navigate to https://www.w3schools.com/html/html_forms.asp, then: "
            "What input fields does the example form on this page have? "
            "List field names or types."
        ),
        "expected_contains": [],
        "max_steps": 8,
    },
]


# ---------------------------------------------------------------------------
# Baseline: raw HTML via HTTP (no browser needed)
# ---------------------------------------------------------------------------

def baseline_raw_html(url: str, goal: str) -> Dict[str, Any]:
    """Fetch raw HTML via HTTP, count tokens."""
    try:
        import ssl
        import urllib.request
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "shadow-web-benchmark/0.4"},
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "raw_html_chars": 0, "raw_html_tokens": 0}

    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = len(enc.encode(html))
    except ImportError:
        tokens = len(html) // 4

    return {
        "ok": True,
        "raw_html_chars": len(html),
        "raw_html_tokens": tokens,
        "url": url,
    }


# ---------------------------------------------------------------------------
# Shadow Web agent run
# ---------------------------------------------------------------------------

def run_shadow_task(
    task: Dict[str, Any],
    *,
    model: str = "deepseek-chat",
) -> Dict[str, Any]:
    """Run one task through the shadow-web exec agent loop.

    Credentials are resolved from the environment / .env by
    resolve_api_credentials() — do NOT pass api_key explicitly
    or the atomic provider→URL logic is bypassed.
    """
    from shadow_web.agent.loop import run_agent_task

    t0 = time.monotonic()
    result = run_agent_task(
        task["goal"],
        model=model,
        max_steps=task.get("max_steps", 10),
        max_tokens=15000,
        browser_engine="chromium",
        headed=False,
    )
    elapsed = time.monotonic() - t0

    d = result.to_dict()
    d["wall_time_s"] = round(elapsed, 2)
    d["task_id"] = task["id"]
    d["task_category"] = task.get("category", "")
    d["goal"] = task["goal"]

    # Success check
    answer = d.get("answer") or ""
    expected = task.get("expected_contains") or []
    if expected:
        d["answer_matches"] = all(e.lower() in answer.lower() for e in expected)
    else:
        d["answer_matches"] = bool(answer and len(answer) > 5)

    return d


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    dry_run = "--dry-run" in sys.argv
    json_only = "--json" in sys.argv

    out_dir = _HERE / "results"
    out_dir.mkdir(exist_ok=True)

    # Resolve API key
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key and not dry_run:
        # Try loading project .env
        sys.path.insert(0, str(_HERE / "src"))
        from shadow_web.runtime_env import load_dotenv_files
        load_dotenv_files()
        api_key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

    model = os.environ.get("SHADOW_WEB_LLM_MODEL", "deepseek-chat")
    base_url = os.environ.get("SHADOW_WEB_LLM_BASE_URL")

    if not api_key and not dry_run:
        print("No DEEPSEEK_API_KEY or OPENAI_API_KEY in env/.env.")
        print("Running in DRY-RUN mode (schema-only, no agent).")
        print("Set the key and re-run for live results.\n")
        dry_run = True

    results: List[Dict[str, Any]] = []
    run_label = "dry-run" if dry_run else "live"

    for task in TASKS:
        name = f"{task['id']} ({task['category']})"
        if not json_only:
            print(f"\n{'='*70}")
            print(f" Task: {name}")
            print(f" Goal: {task['goal']}")
            print(f" URL:  {task['url']}")
            print(f"{'='*70}")

        # Baseline
        bl = baseline_raw_html(task["url"], task["goal"])
        if not json_only:
            print(f" Baseline raw HTML: {bl.get('raw_html_tokens', '?'):,} tokens")

        # Shadow Web
        if dry_run:
            sw = {
                "ok": None,
                "task_id": task["id"],
                "task_category": task["category"],
                "wall_time_s": 0,
                "answer": "(dry-run — install playwright chromium + set API key)",
                "steps": 0,
                "usage": {},
                "stop_reason": "dry_run",
                "complete": False,
                "truncated": False,
                "answer_matches": None,
            }
        else:
            try:
                if not json_only:
                    print(" Running shadow-web exec...", end=" ", flush=True)
                sw = run_shadow_task(
                    task,
                    model=model,
                )
            except Exception as exc:
                sw = {
                    "ok": False,
                    "task_id": task["id"],
                    "task_category": task["category"],
                    "wall_time_s": 0,
                    "answer": "",
                    "steps": 0,
                    "usage": {},
                    "stop_reason": "error",
                    "complete": False,
                    "truncated": False,
                    "answer_matches": False,
                    "error": str(exc)[:200],
                }
                if not json_only:
                    print(f"[ERR] {exc}")
            if not json_only:
                status = "OK" if sw.get("ok") else "FAIL"
                print(f"[{status}] {sw.get('steps', '?')} steps, "
                      f"{sw.get('wall_time_s', '?')}s, "
                      f"{sw.get('usage', {}).get('total_tokens', '?')} tokens")

        entry = {
            "task": task["id"],
            "category": task["category"],
            "baseline_tokens": bl.get("raw_html_tokens", 0),
            "agent_ok": sw.get("ok"),
            "agent_answer": sw.get("answer", "")[:200],
            "agent_steps": sw.get("steps", 0),
            "agent_tokens": sw.get("usage", {}).get("total_tokens", 0),
            "agent_prompt_tokens": sw.get("usage", {}).get("prompt_tokens", 0),
            "agent_wall_time_s": sw.get("wall_time_s", 0),
            "agent_stop_reason": sw.get("stop_reason", ""),
            "agent_complete": sw.get("complete", False),
            "agent_truncated": sw.get("truncated", False),
            "answer_matches": sw.get("answer_matches"),
            "run": run_label,
        }
        results.append(entry)

    # Summary table
    print(f"\n{'='*80}")
    print(f" RESULTS ({run_label})")
    print(f"{'='*80}")
    print(f"{'Task':<22} {'Baseline':>8} {'SW tok':>8} {'Steps':>5} {'Time':>6} {'OK':>5} {'Honest':>6}")
    print("-" * 80)
    for r in results:
        baseline = r["baseline_tokens"]
        agent_tok = r["agent_tokens"]
        ratio = f"{baseline / max(agent_tok, 1):.1f}x" if agent_tok else "-"
        honest = (
            "✓" if r["agent_complete"] and not r["agent_truncated"]
            else "TRUNC" if r["agent_truncated"]
            else "PART" if not r["agent_complete"]
            else "?"
        )
        ok = "✓" if r["agent_ok"] else ("?" if r["agent_ok"] is None else "✗")
        print(
            f"{r['task']:<22} "
            f"{baseline:>7,} "
            f"{agent_tok:>7,} "
            f"{r['agent_steps']:>5} "
            f"{r['agent_wall_time_s']:>5.1f}s "
            f"{ok:>5} "
            f"{honest:>6}"
        )

    total_baseline = sum(r["baseline_tokens"] for r in results)
    total_agent = sum(r["agent_tokens"] for r in results)
    ok_count = sum(1 for r in results if r["agent_ok"])
    honest_count = sum(
        1 for r in results
        if r["agent_complete"] and not r["agent_truncated"]
    )

    print("-" * 80)
    print(
        f"{'TOTAL':<22} "
        f"{total_baseline:>7,} "
        f"{total_agent:>7,} "
        f"{'':>5} "
        f"{'':>6} "
        f"{ok_count}/{len(results):>5} "
        f"{honest_count}/{len(results):>6}"
    )

    # Save JSON
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"agent_tasks_{run_label}_{ts}.json"
    payload = {
        "run": run_label,
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "summary": {
            "tasks": len(results),
            "ok": ok_count,
            "honest": honest_count,
            "total_baseline_tokens": total_baseline,
            "total_agent_tokens": total_agent,
            "token_ratio": (
                round(total_baseline / max(total_agent, 1), 1)
                if total_agent else None
            ),
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
