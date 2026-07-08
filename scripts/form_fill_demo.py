#!/usr/bin/env python3
"""AgentOps form fill demo — plan + optional execute.

Usage:
  python scripts/form_fill_demo.py https://httpbin.org/forms/post --profile profile.json
  python scripts/form_fill_demo.py URL --profile profile.json --execute
  python scripts/form_fill_demo.py URL --dry-run --json plan.json

Profile JSON example:
{
  "email": "demo@example.com",
  "name": "Ada Lovelace",
  "company": "Analytical Engines Inc",
  "role": "Founding Engineer",
  "phone": "+1 555 0100"
}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from shadow_web.browser_use import AsyncShadowPage
from shadow_web.form_fill import (
    apply_question_answers,
    build_form_fill_plan,
    execute_form_fill_plan_async,
    execute_form_fill_plan_multi_step_async,
    validate_profile,
)


DEFAULT_PROFILE = {
    "email": "demo@example.com",
    "name": "Ada Lovelace",
    "company": "Analytical Engines",
    "role": "Founding Engineer",
    "phone": "+1 555 0100",
}


async def run_demo(
    url: str,
    profile: dict,
    *,
    execute: bool,
    auto_submit: bool,
    answers: dict | None,
    json_out: Path | None,
    multi_step: bool = False,
    max_steps: int = 3,
    validate: bool = True,
) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            shadow = AsyncShadowPage(page, capture_mode="auto")
            clean_html, _ = await shadow.refresh()

            from shadow_web.schema_snap import parse_forms

            forms = parse_forms(clean_html)
            plan = build_form_fill_plan(
                url=url,
                forms=forms,
                action_map=shadow.action_map,
                profile=profile,
                page_class=shadow.page_class,
                page_class_reason=shadow.page_class_reason,
                auto_submit=auto_submit,
            )

            if answers:
                apply_question_answers(plan, answers)

            report = plan.to_dict()

            if execute:
                if multi_step:
                    report["execution"] = await execute_form_fill_plan_multi_step_async(
                        shadow,
                        profile,
                        answers=answers or {},
                        max_steps=max_steps,
                        validate=validate,
                    )
                elif any(s.action == "handoff" for s in plan.steps):
                    report["execution"] = {
                        "status": "handoff",
                        "message": "Blockers detected — not executing fills. Resolve handoff first.",
                    }
                elif any(s.action == "ask" for s in plan.steps) and not answers:
                    report["execution"] = {
                        "status": "questions_pending",
                        "message": "Answer questions (see plan.questions) then re-run with --answers",
                    }
                else:
                    exec_result = await execute_form_fill_plan_async(shadow, plan, validate=validate)
                    if exec_result.get("status") == "completed" and auto_submit:
                        await shadow.refresh(diff=True)
                        exec_result["page_class_after"] = shadow.page_class
                        exec_result["url_after"] = shadow.last_url
                    report["execution"] = exec_result

            return report
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow Web AgentOps form fill demo")
    parser.add_argument("url", help="Page URL with a form")
    parser.add_argument("--profile", type=Path, help="JSON profile (email, name, company, role, phone)")
    parser.add_argument("--answers", type=Path, help="JSON answers for ask steps {question_id: value}")
    parser.add_argument("--execute", action="store_true", help="Execute auto_fill + submit steps")
    parser.add_argument("--no-submit", action="store_true", help="Plan fills but do not click submit")
    parser.add_argument("--multi-step", action="store_true", help="Wizard loop (max 3 steps)")
    parser.add_argument("--max-steps", type=int, default=3, help="Multi-step limit")
    parser.add_argument("--no-validate", action="store_true", help="Skip post-fill validation loop")
    parser.add_argument("--json", type=Path, dest="json_out", help="Write plan/report JSON")
    args = parser.parse_args()

    profile = DEFAULT_PROFILE
    if args.profile:
        profile = {**DEFAULT_PROFILE, **json.loads(args.profile.read_text(encoding="utf-8"))}

    profile_validation = validate_profile(profile)
    if profile_validation.get("unknown_keys"):
        print(f"Profile warnings: {profile_validation['warnings']}", file=sys.stderr)

    answers = None
    if args.answers:
        answers = json.loads(args.answers.read_text(encoding="utf-8"))

    report = asyncio.run(
        run_demo(
            args.url,
            profile,
            execute=args.execute,
            auto_submit=not args.no_submit,
            answers=answers,
            json_out=args.json_out,
            multi_step=args.multi_step,
            max_steps=args.max_steps,
            validate=not args.no_validate,
        )
    )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.json_out}")
    else:
        print(text)

    summary = report.get("summary", {})
    print(
        f"\nPlan: auto_fill={summary.get('auto_fill', 0)} ask={summary.get('ask', 0)} "
        f"handoff={summary.get('handoff', 0)} submit={summary.get('submit', 0)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
