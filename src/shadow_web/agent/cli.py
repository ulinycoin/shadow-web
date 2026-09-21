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
        help=(
            "Run a bounded browser TASK (one JSON result). "
            "Not a bulk scraper — see complete/truncated/next_hint in output."
        ),
    )
    exec_p.add_argument(
        "task",
        help="Natural-language browser task (one goal, not a full-site scrape)",
    )
    exec_p.add_argument(
        "--model",
        default=os.environ.get("SHADOW_WEB_LLM_MODEL", "deepseek-v4-flash"),
        help="Model id (default: deepseek-v4-flash or SHADOW_WEB_LLM_MODEL)",
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
        "--max-tokens",
        type=int,
        default=int(os.environ.get("SHADOW_WEB_MAX_TOKENS", "15000")),
        help="Stop when cumulative LLM tokens reach this budget (default 15000; 0 = unlimited)",
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
        help="Browser engine (default: camoufox; Chromium fallback only with SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK=1)",
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
    exec_p.add_argument(
        "--jev",
        action="store_true",
        help=(
            "Let TypeSafe Jev pre-select cheap steps (needs TYPESAFE_API_KEY); "
            "low-confidence picks still go to the model"
        ),
    )
    exec_p.add_argument(
        "--jev-threshold",
        type=float,
        default=None,
        help="Confidence gate for Jev steps (default 0.6, or SHADOW_WEB_JEV_THRESHOLD)",
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
            "shadow-web exec \"...\" --model deepseek-v4-flash"
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

    if args.jev:
        os.environ["SHADOW_WEB_AGENT_JEV"] = "1"
    if args.jev_threshold is not None:
        os.environ["SHADOW_WEB_JEV_THRESHOLD"] = str(args.jev_threshold)

    result = run_agent_task(
        args.task,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        max_steps=args.max_steps,
        max_tokens=args.max_tokens,
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
    from shadow_web.runtime_env import load_dotenv_files

    load_dotenv_files()
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
