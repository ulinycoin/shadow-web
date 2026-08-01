#!/usr/bin/env python3
"""End-to-end agent task benchmark — v0.4.1 (2026-08-01).

Measures *task success / tokens / latency*, not HTML compression ratio.
Compares shadow-web exec against a plain Playwright→LLM oneshot baseline.

Usage:
  DEEPSEEK_API_KEY=sk-... python benchmarks/agent_tasks.py
  python benchmarks/agent_tasks.py --dry-run           # schema-only, no LLM
  python benchmarks/agent_tasks.py --no-llm-baseline   # skip Playwright→LLM
  python benchmarks/agent_tasks.py --only mdn-404,quotes-count
  python benchmarks/agent_tasks.py --limit 5

Output: benchmarks/results/agent_tasks_<timestamp>.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure src is in path (repo root / src)
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT / "src"))

# Cap naive baseline HTML so a single oneshot call fits common context windows.
BASELINE_HTML_TOKEN_CAP = 24_000


# ---------------------------------------------------------------------------
# Task definitions — 10 bounded tasks across page types
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
        # At least one real technique name from the article.
        "expected_any": [
            "HTTP",
            "DOM",
            "regex",
            "regular expression",
            "HTML parsing",
            "computer vision",
            "semantic annotation",
            "vertical aggregation",
        ],
        "max_steps": 8,
    },
    {
        "id": "hn-top-story",
        "category": "table",
        "url": "https://news.ycombinator.com",
        "goal": (
            "Navigate to https://news.ycombinator.com, then: "
            "What is the title of the top story on Hacker News right now? "
            "Return only the story title."
        ),
        # Live title — require a non-trivial answer (not a refusal).
        "min_answer_chars": 8,
        "expected_regex": [r"[A-Za-z]{3,}"],
        "max_steps": 10,
    },
    {
        "id": "github-trending-count",
        "category": "spa",
        "url": "https://github.com/trending",
        "goal": (
            "Navigate to https://github.com/trending, then: "
            "How many trending repositories are shown on this page? "
            "Return the count and the first repo as owner/name."
        ),
        # Must include a count and an owner/repo slug.
        "expected_regex": [
            r"\b([1-9]|[12]\d|30)\b",
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
        ],
        "max_steps": 12,
    },
    {
        "id": "form-detection",
        "category": "form",
        "url": "https://www.w3schools.com/html/html_forms.asp",
        "goal": (
            "Navigate to https://www.w3schools.com/html/html_forms.asp, then: "
            "What input fields does the first example form on this page have? "
            "List field names or types."
        ),
        "expected_any": ["fname", "first name", "lname", "last name"],
        "max_steps": 8,
    },
    {
        "id": "books-first-price",
        "category": "catalog",
        "url": "https://books.toscrape.com/",
        "goal": (
            "Navigate to https://books.toscrape.com/, then: "
            "What is the title and price of the first book shown?"
        ),
        "expected_contains": ["£", "A Light in the"],
        "max_steps": 8,
    },
    {
        "id": "quotes-count",
        "category": "list",
        "url": "https://quotes.toscrape.com/",
        "goal": (
            "Navigate to https://quotes.toscrape.com/, then: "
            "How many quote cards are shown on the first page? "
            "Return only the integer count."
        ),
        # Site serves 10 quotes per page.
        "expected_regex": [r"\b10\b"],
        "min_answer_chars": 1,
        "max_steps": 8,
        "max_tokens": 20000,
    },
    {
        "id": "login-fields",
        "category": "form",
        "url": "https://the-internet.herokuapp.com/login",
        "goal": (
            "Navigate to https://the-internet.herokuapp.com/login, then: "
            "What are the login form field names (or labels) on this page?"
        ),
        "expected_any": ["username", "password"],
        "max_steps": 6,
    },
    {
        "id": "mdn-404",
        "category": "text-heavy",
        "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404",
        "goal": (
            "Navigate to https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404, then: "
            "What does HTTP status 404 mean? One short sentence."
        ),
        "expected_any": [
            "not found",
            "Not Found",
            "not be found",
            "could not find",
            "cannot find",
            "can't find",
        ],
        "max_steps": 8,
        "max_tokens": 20000,
    },
    {
        "id": "heroku-table-due",
        "category": "table",
        "url": "https://the-internet.herokuapp.com/tables",
        "goal": (
            "Navigate to https://the-internet.herokuapp.com/tables, then: "
            "In Example 1, what is the Due amount for John Smith? "
            "Return only the dollar amount."
        ),
        "expected_contains": ["$50.00"],
        "max_steps": 8,
    },
]


def match_answer(answer: str, task: Dict[str, Any]) -> bool:
    """Strict-ish success check. Empty soft rules → require non-trivial text."""
    text = (answer or "").strip()
    if not text:
        return False

    min_chars = int(task.get("min_answer_chars") or 5)
    if len(text) < min_chars:
        return False

    lower = text.lower()
    for needle in task.get("expected_contains") or []:
        if needle.lower() not in lower:
            return False

    any_needles = task.get("expected_any") or []
    if any_needles and not any(n.lower() in lower for n in any_needles):
        return False

    for pattern in task.get("expected_regex") or []:
        if re.search(pattern, text, flags=re.IGNORECASE) is None:
            return False

    # If no positive rules beyond min length, still require substance.
    has_rule = bool(
        task.get("expected_contains")
        or task.get("expected_any")
        or task.get("expected_regex")
    )
    if not has_rule and len(text) < 8:
        return False
    return True


# ---------------------------------------------------------------------------
# Token helpers + baselines
# ---------------------------------------------------------------------------

def _count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        return max(1, len(text) // 4)


def _truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, int, bool]:
    """Return (text, tokens, truncated)."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        ids = enc.encode(text)
        if len(ids) <= max_tokens:
            return text, len(ids), False
        clipped = enc.decode(ids[:max_tokens])
        return clipped, max_tokens, True
    except ImportError:
        # ~4 chars/token heuristic
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text, max(1, len(text) // 4), False
        return text[:max_chars], max_tokens, True


def baseline_raw_html(url: str, goal: str) -> Dict[str, Any]:
    """Fetch raw HTML via HTTP, count tokens (cheap size reference)."""
    del goal  # size-only baseline
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

    return {
        "ok": True,
        "raw_html_chars": len(html),
        "raw_html_tokens": _count_tokens(html),
        "url": url,
    }


def _fetch_playwright_html(url: str) -> Dict[str, Any]:
    """Navigate with Playwright Chromium and return rendered HTML."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {"ok": False, "error": f"playwright not installed: {exc}", "html": ""}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(1_000)
            html = page.content()
            browser.close()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "html": ""}

    return {
        "ok": True,
        "html": html,
        "html_chars": len(html),
        "html_tokens": _count_tokens(html),
        "url": url,
    }


def baseline_playwright_llm(
    task: Dict[str, Any],
    *,
    model: str = "deepseek-chat",
) -> Dict[str, Any]:
    """Plain Playwright HTML → single LLM call (no Shadow Web tools).

    This is the fair task baseline: same goal, same model, naive HTML dump.
    """
    from shadow_web.agent.models import ChatMessage, OpenAICompatibleAdapter

    url = task["url"]
    goal = task["goal"]
    t0 = time.monotonic()
    fetched = _fetch_playwright_html(url)
    if not fetched.get("ok"):
        return {
            "ok": False,
            "error": fetched.get("error", "fetch failed"),
            "html_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "wall_time_s": round(time.monotonic() - t0, 2),
            "answer": "",
            "answer_matches": False,
            "truncated_html": False,
        }

    html = fetched["html"]
    html_tokens = int(fetched["html_tokens"])
    clipped, used_tokens, truncated = _truncate_to_tokens(html, BASELINE_HTML_TOKEN_CAP)

    prompt = (
        "You are answering from raw page HTML only. No tools.\n"
        f"Goal: {goal}\n\n"
        f"HTML ({'truncated' if truncated else 'full'}, ~{used_tokens} tokens):\n"
        f"{clipped}\n\n"
        "Reply with the final answer only."
    )

    try:
        llm = OpenAICompatibleAdapter(model=model)
        resp = llm.chat(
            [ChatMessage(role="user", content=prompt)],
            tools=None,
        )
        answer = (resp.content or "").strip()
        usage = resp.usage or {}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)[:300],
            "html_tokens": html_tokens,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "wall_time_s": round(time.monotonic() - t0, 2),
            "answer": "",
            "answer_matches": False,
            "truncated_html": truncated,
        }

    matches = match_answer(answer, task)

    prompt_tokens = int(usage.get("prompt_tokens") or used_tokens)
    completion_tokens = int(usage.get("completion_tokens") or _count_tokens(answer))
    total_tokens = int(
        usage.get("total_tokens") or (prompt_tokens + completion_tokens)
    )

    return {
        "ok": matches,
        "html_tokens": html_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "wall_time_s": round(time.monotonic() - t0, 2),
        "answer": answer[:500],
        "answer_matches": matches,
        "truncated_html": truncated,
        "model": model,
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
        max_tokens=int(task.get("max_tokens", 15000)),
        browser_engine="chromium",
        headed=False,
    )
    elapsed = time.monotonic() - t0

    d = result.to_dict()
    d["wall_time_s"] = round(elapsed, 2)
    d["task_id"] = task["id"]
    d["task_category"] = task.get("category", "")
    d["goal"] = task["goal"]

    answer = d.get("answer") or ""
    d["answer_matches"] = match_answer(answer, task)
    # Task success requires agent ok + answer match (not just loop exit).
    d["ok"] = bool(d.get("ok")) and bool(d["answer_matches"])

    return d


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_limit(argv: List[str]) -> Optional[int]:
    for i, arg in enumerate(argv):
        if arg == "--limit" and i + 1 < len(argv):
            try:
                return max(1, int(argv[i + 1]))
            except ValueError:
                return None
        if arg.startswith("--limit="):
            try:
                return max(1, int(arg.split("=", 1)[1]))
            except ValueError:
                return None
    return None


def _parse_only(argv: List[str]) -> Optional[List[str]]:
    """--only id1,id2  → run a subset by task id."""
    for i, arg in enumerate(argv):
        raw = None
        if arg == "--only" and i + 1 < len(argv):
            raw = argv[i + 1]
        elif arg.startswith("--only="):
            raw = arg.split("=", 1)[1]
        if raw:
            return [x.strip() for x in raw.split(",") if x.strip()]
    return None


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    json_only = "--json" in sys.argv
    # Skip expensive Playwright→LLM oneshot (HTTP size baseline still runs).
    skip_llm_baseline = "--no-llm-baseline" in sys.argv
    limit = _parse_limit(sys.argv)
    only_ids = _parse_only(sys.argv)

    out_dir = _HERE / "results"
    out_dir.mkdir(exist_ok=True)

    # Resolve API key
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key and not dry_run:
        from shadow_web.runtime_env import load_dotenv_files
        load_dotenv_files()
        api_key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

    model = os.environ.get("SHADOW_WEB_LLM_MODEL", "deepseek-chat")

    if not api_key and not dry_run:
        print("No DEEPSEEK_API_KEY or OPENAI_API_KEY in env/.env.")
        print("Running in DRY-RUN mode (schema-only, no agent).")
        print("Set the key and re-run for live results.\n")
        dry_run = True

    results: List[Dict[str, Any]] = []
    run_label = "dry-run" if dry_run else "live"
    tasks = list(TASKS)
    if only_ids:
        wanted = set(only_ids)
        tasks = [t for t in tasks if t["id"] in wanted]
        missing = wanted - {t["id"] for t in tasks}
        if missing:
            print(f"Unknown --only ids: {sorted(missing)}", file=sys.stderr)
            sys.exit(2)
    if limit:
        tasks = tasks[:limit]

    for task in tasks:
        name = f"{task['id']} ({task['category']})"
        if not json_only:
            print(f"\n{'='*70}")
            print(f" Task: {name}")
            print(f" Goal: {task['goal']}")
            print(f" URL:  {task['url']}")
            print(f"{'='*70}")

        # Size reference (HTTP raw HTML)
        bl = baseline_raw_html(task["url"], task["goal"])
        if not json_only:
            print(f" HTTP raw HTML: {bl.get('raw_html_tokens', '?'):,} tokens")

        # Fair baseline: Playwright rendered HTML → one LLM call
        if dry_run or skip_llm_baseline:
            bl_llm: Dict[str, Any] = {
                "ok": None,
                "html_tokens": 0,
                "total_tokens": 0,
                "wall_time_s": 0,
                "answer": "",
                "answer_matches": None,
                "truncated_html": False,
                "skipped": True,
            }
            if not json_only and skip_llm_baseline and not dry_run:
                print(" Playwright→LLM baseline: skipped (--no-llm-baseline)")
        else:
            if not json_only:
                print(" Running Playwright→LLM baseline...", end=" ", flush=True)
            bl_llm = baseline_playwright_llm(task, model=model)
            if not json_only:
                status = "OK" if bl_llm.get("ok") else "FAIL"
                print(
                    f"[{status}] html={bl_llm.get('html_tokens', '?'):,} "
                    f"tok={bl_llm.get('total_tokens', '?')}, "
                    f"{bl_llm.get('wall_time_s', '?')}s"
                    + (" truncated" if bl_llm.get("truncated_html") else "")
                )

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

        usage = sw.get("usage") or {}
        entry = {
            "task": task["id"],
            "category": task["category"],
            "baseline_tokens": bl.get("raw_html_tokens", 0),
            "baseline_llm_ok": bl_llm.get("ok"),
            "baseline_llm_html_tokens": bl_llm.get("html_tokens", 0),
            "baseline_llm_tokens": bl_llm.get("total_tokens", 0),
            "baseline_llm_wall_time_s": bl_llm.get("wall_time_s", 0),
            "baseline_llm_answer": (bl_llm.get("answer") or "")[:200],
            "baseline_llm_truncated_html": bl_llm.get("truncated_html", False),
            "agent_ok": sw.get("ok"),
            "agent_answer": (sw.get("answer") or "")[:200],
            "agent_steps": sw.get("steps", 0) or 0,
            "agent_tokens": usage.get("total_tokens", 0) or 0,
            "agent_prompt_tokens": usage.get("prompt_tokens", 0) or 0,
            "agent_wall_time_s": sw.get("wall_time_s", 0) or 0,
            "agent_stop_reason": sw.get("stop_reason", "") or "",
            "agent_complete": bool(sw.get("complete", False)),
            "agent_truncated": bool(sw.get("truncated", False)),
            "answer_matches": sw.get("answer_matches"),
            "run": run_label,
        }
        results.append(entry)

    # Summary table
    print(f"\n{'='*96}")
    print(f" RESULTS ({run_label})")
    print(f"{'='*96}")
    print(
        f"{'Task':<22} {'HTTP':>7} {'BL tok':>7} {'BL ok':>5} "
        f"{'SW tok':>7} {'Steps':>5} {'Time':>6} {'SW ok':>5}"
    )
    print("-" * 96)
    for r in results:
        bl_ok = r["baseline_llm_ok"]
        bl_ok_s = "✓" if bl_ok else ("?" if bl_ok is None else "✗")
        sw_ok = r["agent_ok"]
        sw_ok_s = "✓" if sw_ok else ("?" if sw_ok is None else "✗")
        print(
            f"{r['task']:<22} "
            f"{r['baseline_tokens']:>7,} "
            f"{r['baseline_llm_tokens']:>7,} "
            f"{bl_ok_s:>5} "
            f"{r['agent_tokens']:>7,} "
            f"{r['agent_steps']:>5} "
            f"{r['agent_wall_time_s']:>5.1f}s "
            f"{sw_ok_s:>5}"
        )

    total_http = sum(r["baseline_tokens"] for r in results)
    total_bl = sum(r["baseline_llm_tokens"] for r in results)
    total_agent = sum(r["agent_tokens"] for r in results)
    bl_ok_count = sum(1 for r in results if r["baseline_llm_ok"])
    ok_count = sum(1 for r in results if r["agent_ok"])
    honest_count = sum(
        1 for r in results
        if r["agent_complete"] and not r["agent_truncated"]
    )
    token_wins = sum(
        1 for r in results
        if r["agent_tokens"]
        and r["baseline_llm_tokens"]
        and r["agent_tokens"] < r["baseline_llm_tokens"]
    )

    print("-" * 96)
    print(
        f"{'TOTAL':<22} "
        f"{total_http:>7,} "
        f"{total_bl:>7,} "
        f"{bl_ok_count}/{len(results)} "
        f"{total_agent:>7,} "
        f"{'':>5} "
        f"{'':>6} "
        f"{ok_count}/{len(results)}"
    )
    if total_bl and total_agent:
        print(
            f"\nToken win vs Playwright→LLM baseline: "
            f"{token_wins}/{len(results)} tasks "
            f"(ratio {total_bl / max(total_agent, 1):.1f}x)"
        )
    print(f"Honest completions (SW): {honest_count}/{len(results)}")

    # Save JSON
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"agent_tasks_{run_label}_{ts}.json"
    payload = {
        "run": run_label,
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": {
            "http_baseline": "urllib raw HTML token count",
            "llm_baseline": (
                "playwright chromium HTML → single LLM call "
                f"(cap {BASELINE_HTML_TOKEN_CAP} tokens)"
            ),
            "shadow_web": "shadow-web exec agent loop",
        },
        "results": results,
        "summary": {
            "tasks": len(results),
            "ok": ok_count,
            "baseline_llm_ok": bl_ok_count,
            "token_wins_vs_baseline_llm": token_wins,
            "honest": honest_count,
            "total_http_tokens": total_http,
            "total_baseline_llm_tokens": total_bl,
            "total_agent_tokens": total_agent,
            "token_ratio_vs_http": (
                round(total_http / max(total_agent, 1), 1)
                if total_agent else None
            ),
            "token_ratio_vs_baseline_llm": (
                round(total_bl / max(total_agent, 1), 1)
                if total_agent and total_bl else None
            ),
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
