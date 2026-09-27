"""Tests for AgentOps form fill planner and executor."""

from __future__ import annotations

import pytest

from shadow_web.compressor import process_html
from shadow_web.form_fill import (
    MATCH_THRESHOLD,
    FormFillPlan,
    FormFillStep,
    apply_question_answers,
    build_form_fill_plan,
    classify_field_kind,
    execute_form_fill_plan,
    execute_form_fill_plan_async,
    execute_form_fill_plan_multi_step,
    execute_form_fill_plan_multi_step_async,
    link_form_to_actions,
    match_validation_errors,
    plan_from_dict,
    plan_from_session,
    summarize_plan,
    validate_profile,
    validate_value_for_field,
)
from shadow_web.schema_snap import parse_forms

LOGIN_FORM = """<form action="/login" method="POST">
  <label for="email">Email Address</label>
  <input type="email" id="email" name="email" required placeholder="you@example.com">
  <label for="password">Password</label>
  <input type="password" id="password" name="password" required minlength="8">
  <button type="submit">Sign In</button>
</form>"""

SIGNUP_FORM = """<form action="/register" method="POST">
  <label for="email">Work Email</label>
  <input type="email" id="email" name="email" required>
  <label for="name">Full Name</label>
  <input type="text" id="name" name="name" required>
  <label for="company">Company</label>
  <input type="text" id="company" name="company" required>
  <label for="role">Job Title</label>
  <input type="text" id="role" name="role">
  <label for="bio">Tell us about yourself</label>
  <textarea id="bio" name="bio" rows="4"></textarea>
  <label for="resume">Resume</label>
  <input type="file" id="resume" name="resume">
  <button type="submit">Create account</button>
</form>"""

CAPTCHA_FORM = """<form action="/signup" method="POST">
  <input type="email" name="email" placeholder="Email">
  <div class="g-recaptcha">I'm not a robot</div>
  <button type="submit">Submit</button>
</form>"""


def _session(html: str):
    clean, action_map, _ = process_html(html)
    forms = parse_forms(clean)
    return clean, forms, action_map


def test_classify_safe_kinds():
    assert classify_field_kind({"type": "email", "label": "Email"}) == "email"
    assert classify_field_kind({"type": "text", "label": "Company name"}) == "company"
    assert classify_field_kind({"type": "textarea", "label": "Bio"}) == "textarea"
    assert classify_field_kind({"type": "file", "label": "Upload"}) == "file"


def test_link_form_to_actions_assigns_sids():
    clean, forms, actions = _session(LOGIN_FORM)
    linked = link_form_to_actions(forms, actions)
    email = next(lf for lf in linked if lf.kind == "email")
    assert email.sid is not None
    assert email.match_score >= 0.45


def test_auto_fill_safe_fields_only():
    clean, forms, actions = _session(SIGNUP_FORM)
    plan = build_form_fill_plan(
        url="https://app.example.com/register",
        forms=forms,
        action_map=actions,
        profile={
            "email": "ada@example.com",
            "name": "Ada Lovelace",
            "company": "Analytical Engines",
            "role": "Engineer",
        },
        auto_submit=False,
    )
    summary = summarize_plan(plan)
    assert summary["auto_fill"] >= 4
    assert summary["ask"] >= 1  # bio textarea
    assert summary["handoff"] >= 1  # file upload
    assert not any(s.action == "auto_fill" and s.field and s.field.get("kind") == "file" for s in plan.steps)


def test_password_handoff_by_default():
    clean, forms, actions = _session(LOGIN_FORM)
    plan = build_form_fill_plan(
        url="https://app.example.com/login",
        forms=forms,
        action_map=actions,
        profile={"email": "a@b.com"},
        auto_submit=False,
    )
    assert any(s.action == "handoff" and s.reason == "password_requires_handoff" for s in plan.steps)
    assert any(s.action == "auto_fill" and s.field and s.field.get("kind") == "email" for s in plan.steps)


def test_page_anti_bot_immediate_handoff():
    clean, forms, actions = _session(LOGIN_FORM)
    plan = build_form_fill_plan(
        url="https://app.example.com/login",
        forms=forms,
        action_map=actions,
        profile={"email": "a@b.com"},
        page_class="Anti-bot",
        page_class_reason="Cloudflare",
    )
    assert plan.blockers
    assert plan.steps[0].action == "handoff"
    assert summarize_plan(plan)["auto_fill"] == 0


def test_ask_question_structure():
    clean, forms, actions = _session(SIGNUP_FORM)
    plan = build_form_fill_plan(
        url="https://app.example.com/register",
        forms=forms,
        action_map=actions,
        profile={"email": "x@y.com", "name": "Test", "company": "Co"},
        auto_submit=False,
    )
    ask_steps = [s for s in plan.steps if s.action == "ask"]
    assert ask_steps
    q = ask_steps[0].question
    assert q and "prompt" in q and "sid" in q


def test_apply_question_answers():
    clean, forms, actions = _session(SIGNUP_FORM)
    plan = build_form_fill_plan(
        url="https://app.example.com/register",
        forms=forms,
        action_map=actions,
        profile={"email": "x@y.com", "name": "Test", "company": "Co"},
        auto_submit=False,
    )
    bio_q = next(s.question for s in plan.steps if s.action == "ask" and s.question and s.question.get("kind") == "textarea")
    apply_question_answers(plan, {bio_q["id"]: "I build parsers."})
    assert any(s.action == "auto_fill" and s.value == "I build parsers." for s in plan.steps)


def test_plan_from_session_helper():
    clean, _, actions = _session(LOGIN_FORM)
    plan = plan_from_session(
        url="https://example.com",
        clean_html=clean,
        action_map=actions,
        profile={"email": "a@b.com"},
        auto_submit=False,
    )
    assert plan.url == "https://example.com"


def test_validate_profile_unknown_keys():
    result = validate_profile({"email": "a@b.com", "companny": "Typo Inc"})
    assert "companny" in result["unknown_keys"]
    assert result["valid"] is False
    assert any(w["code"] == "unknown_profile_keys" for w in result["warnings"])


def test_validate_value_for_email_preflight():
    msg = validate_value_for_field("not-an-email", {"kind": "email", "type": "email"})
    assert msg is not None
    assert validate_value_for_field("ok@example.com", {"kind": "email"}) is None


def test_match_validation_errors_by_name():
    from shadow_web.form_fill import FormFillStep

    filled = [
        FormFillStep(
            action="auto_fill",
            sid="2",
            value="bad",
            field={"name": "email", "kind": "email", "type": "email", "label": "Email"},
        )
    ]
    invalid = [{"name": "email", "validationMessage": "Please include an '@'", "value": "bad"}]
    errors = match_validation_errors(filled, invalid)
    assert len(errors) == 1
    assert errors[0]["field"]["name"] == "email"
    assert errors[0]["source"] == "constraint_validation"


def test_plan_includes_profile_validation():
    clean, forms, actions = _session(SIGNUP_FORM)
    plan = build_form_fill_plan(
        url="https://example.com",
        forms=forms,
        action_map=actions,
        profile={"email": "x@y.com", "companny": "Oops"},
        auto_submit=False,
    )
    assert plan.profile_validation["unknown_keys"] == ["companny"]


def test_plan_from_dict_roundtrip():
    clean, forms, actions = _session(LOGIN_FORM)
    original = build_form_fill_plan(
        url="https://example.com",
        forms=forms,
        action_map=actions,
        profile={"email": "a@b.com"},
        auto_submit=False,
    )
    restored = plan_from_dict(original.to_dict())
    assert restored.url == original.url
    assert len(restored.steps) == len(original.steps)


# ---------------------------------------------------------------------------
# Regression: unauthorised/repeated submits, wrong-field fill, ask vs submit
# ---------------------------------------------------------------------------

LEAD_FORM = """<form action="/lead" method="POST">
  <input type="email" name="email" aria-label="Work Email" required>
  <input type="text" name="name" aria-label="Full Name" required>
  <input type="text" name="company" aria-label="Company" required>
  <button type="submit">Get Started</button>
</form>"""

LEAD_PROFILE = {
    "email": "ada@example.com",
    "name": "Ada Lovelace",
    "company": "Analytical Engines",
}

ASK_FORM = """<form action="/ask" method="POST">
  <input type="email" name="email" aria-label="Work Email" required>
  <textarea name="bio" aria-label="Tell us about yourself"></textarea>
  <button type="submit">Send</button>
</form>"""


class _FakePage:
    def __init__(self):
        self.invalid_fields: list[dict] = []

    def evaluate(self, script):
        return list(self.invalid_fields)


class _AsyncFakePage(_FakePage):
    async def evaluate(self, script):
        return list(self.invalid_fields)

    async def wait_for_timeout(self, ms):
        return None


class _FakeShadow:
    """Minimal ShadowPage stand-in: records fills/clicks, never touches the network."""

    def __init__(self, html: str, url: str = "https://app.example.com/lead"):
        clean, action_map, _ = process_html(html)
        self.last_url = url
        self.clean_html = clean
        self.action_map = action_map
        self.page_class = "Static"
        self.page_class_reason = ""
        self.last_diff = None
        self.page = _FakePage()
        self.fills: list[tuple[str, str]] = []
        self.clicks: list[str] = []

    def refresh(self, diff: bool = False):
        return self.clean_html, "<page/>"

    def fill(self, sid, value):
        self.fills.append((sid, value))

    def click(self, sid):
        self.clicks.append(sid)


class _AsyncFakeShadow(_FakeShadow):
    def __init__(self, html: str, url: str = "https://app.example.com/lead"):
        super().__init__(html, url)
        self.page = _AsyncFakePage()

    async def refresh(self, diff: bool = False):
        return self.clean_html, "<page/>"

    async def fill(self, sid, value):
        self.fills.append((sid, value))

    async def click(self, sid):
        self.clicks.append(sid)


def _ask_then_submit_plan() -> FormFillPlan:
    """Hand-built plan (e.g. from plan_from_dict) with an unanswered ask before submit."""
    return FormFillPlan(
        url="https://app.example.com/lead",
        steps=[
            FormFillStep(action="ask", question={"id": "q1", "prompt": "?"}),
            FormFillStep(action="submit", sid="4", reason="form_submit"),
        ],
        questions=[{"id": "q1"}],
    )


# (a) auto_submit=False multi-step -> never clicks a submit element


def test_multi_step_sync_auto_submit_false_never_clicks_submit():
    shadow = _FakeShadow(LEAD_FORM)
    result = execute_form_fill_plan_multi_step(
        shadow, LEAD_PROFILE, max_steps=3, auto_submit=False, wait_after_submit_ms=0
    )
    assert shadow.fills  # fills still ran
    assert shadow.clicks == []
    assert all(entry["plan_summary"]["submit"] == 0 for entry in result["steps"])


@pytest.mark.anyio
async def test_multi_step_async_auto_submit_false_never_clicks_submit():
    shadow = _AsyncFakeShadow(LEAD_FORM)
    result = await execute_form_fill_plan_multi_step_async(
        shadow, LEAD_PROFILE, max_steps=3, auto_submit=False, wait_after_submit_ms=0
    )
    assert shadow.fills
    assert shadow.clicks == []
    assert all(entry["plan_summary"]["submit"] == 0 for entry in result["steps"])


# (b) auto_submit=True, max_steps=3, URL never changes -> exactly one submit


def test_multi_step_sync_submits_at_most_once_per_url():
    shadow = _FakeShadow(LEAD_FORM)
    result = execute_form_fill_plan_multi_step(
        shadow, LEAD_PROFILE, max_steps=3, auto_submit=True, wait_after_submit_ms=0
    )
    assert shadow.last_url == "https://app.example.com/lead"  # URL never changed
    assert shadow.clicks == ["4"]  # the submit control, exactly once
    assert result["step_count"] == 1


@pytest.mark.anyio
async def test_multi_step_async_submits_at_most_once_per_url():
    shadow = _AsyncFakeShadow(LEAD_FORM)
    result = await execute_form_fill_plan_multi_step_async(
        shadow, LEAD_PROFILE, max_steps=3, auto_submit=True, wait_after_submit_ms=0
    )
    assert shadow.last_url == "https://app.example.com/lead"
    assert shadow.clicks == ["4"]
    assert result["step_count"] == 1


# (c) unanswered ask -> no submit step, and executors never click through it


def test_plan_with_pending_questions_has_no_submit_step():
    clean, forms, actions = _session(ASK_FORM)
    plan = build_form_fill_plan(
        url="https://app.example.com/ask",
        forms=forms,
        action_map=actions,
        profile={"email": "ada@example.com"},
        auto_submit=True,
    )
    assert plan.questions
    assert not any(s.action == "submit" for s in plan.steps)


def test_executor_does_not_submit_with_pending_questions():
    shadow = _FakeShadow(LEAD_FORM)
    result = execute_form_fill_plan(shadow, _ask_then_submit_plan(), validate=False)
    assert shadow.clicks == []
    assert result["status"] == "questions_pending"


@pytest.mark.anyio
async def test_executor_async_does_not_submit_with_pending_questions():
    shadow = _AsyncFakeShadow(LEAD_FORM)
    result = await execute_form_fill_plan_async(shadow, _ask_then_submit_plan(), validate=False)
    assert shadow.clicks == []
    assert result["status"] == "questions_pending"


# (d) linker must not store a sid below MATCH_THRESHOLD


def test_linker_ignores_matches_below_threshold():
    forms = [
        {
            "fields": [
                {"tag": "input", "type": "text", "name": "company", "label": "Company"},
            ]
        }
    ]
    action_map = [{"id": "s1", "type": "input[text]", "label": "Full name"}]

    linked = link_form_to_actions(forms, action_map)
    assert linked
    assert linked[0].match_score < MATCH_THRESHOLD
    assert linked[0].sid is None

    plan = build_form_fill_plan(
        url="https://app.example.com/lead",
        forms=forms,
        action_map=action_map,
        profile={"company": "Analytical Engines"},
        auto_submit=True,
    )
    assert not any(s.action == "auto_fill" for s in plan.steps)
    assert any(s.action == "handoff" and s.reason == "low_confidence_match" for s in plan.steps)
