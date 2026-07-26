"""CLI: shadow-web exec …"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional, Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shadow-web",
        description="Shadow Web CLI — token-efficient browser agent tools",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    exec_p = sub.add_parser(
        "exec",
        help="Run a browser task with the built-in agent loop (one JSON result)",
    )
    exec_p.add_argument("task", help="Natural-language browser task")
    exec_p.add_argument(
        "--model",
        default=os.environ.get("SHADOW_WEB_LLM_MODEL", "deepseek-chat"),
        help="Model id (default: deepseek-chat or SHADOW_WEB_LLM_MODEL)",
    )
    exec_p.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible API base URL (DeepSeek/OpenRouter/custom)",
    )
    exec_p.add_argument(
        "--api-key",
        default=None,
        help="LLM API key (else DEEPSEEK_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY)",
    )
    exec_p.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Max model turns (default 20)",
    )
    exec_p.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window",
    )
    exec_p.add_argument(
        "--browser",
        choices=("camoufox", "chromium"),
        default=os.environ.get("SHADOW_WEB_BROWSER", "camoufox"),
        help="Browser engine (default: camoufox; falls back to chromium if unavailable)",
    )
    exec_p.add_argument(
        "--session",
        default=None,
        help="Named persistent profile under ~/.shadow-web/sessions/<name>",
    )
    exec_p.add_argument(
        "--heal-url",
        default=os.environ.get("SHADOW_WEB_HEAL_URL"),
        help="Optional self-heal API URL",
    )
    exec_p.add_argument(
        "--transcript",
        action="store_true",
        help="Include step transcript in the JSON output",
    )

    models_p = sub.add_parser(
        "models",
        help="Show how to select models / env vars for exec",
    )
    models_p.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Optional hint (deepseek|openai|openrouter) — informational only",
    )
    return parser


def _cmd_models(source: Optional[str]) -> int:
    tips = {
        "deepseek": (
            "export DEEPSEEK_API_KEY=...\n"
            "shadow-web exec \"...\" --model deepseek-chat"
        ),
        "openai": (
            "export OPENAI_API_KEY=...\n"
            "shadow-web exec \"...\" --model gpt-4o-mini"
        ),
        "openrouter": (
            "export OPENROUTER_API_KEY=...\n"
            "shadow-web exec \"...\" --model anthropic/claude-sonnet-4 "
            "--base-url https://openrouter.ai/api/v1"
        ),
    }
    if source and source.lower() in tips:
        print(tips[source.lower()])
        return 0
    print(
        "OpenAI-compatible adapters (set one key):\n"
        "  DEEPSEEK_API_KEY     → default base https://api.deepseek.com\n"
        "  OPENAI_API_KEY       → official OpenAI\n"
        "  OPENROUTER_API_KEY   → default base https://openrouter.ai/api/v1\n"
        "  SHADOW_WEB_LLM_BASE_URL / SHADOW_WEB_LLM_API_KEY for custom endpoints\n\n"
        "Examples:\n"
        f"  {tips['deepseek']}\n\n"
        f"  {tips['openai']}\n\n"
        f"  {tips['openrouter']}"
    )
    return 0


def _cmd_exec(args: argparse.Namespace) -> int:
    from .loop import run_agent_task

    result = run_agent_task(
        args.task,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        max_steps=args.max_steps,
        headed=args.headed,
        session=args.session,
        heal_api_url=args.heal_url,
        browser_engine=args.browser,
    )
    payload = result.to_dict()
    if args.transcript:
        payload["transcript"] = result.transcript
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "models":
        code = _cmd_models(args.source)
    elif args.command == "exec":
        code = _cmd_exec(args)
    else:
        parser.print_help()
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
