"""LLM-assisted form filling with AgentOps execution modes.

Modes (per field / page):
  - auto_fill: safe profile fields (email, name, company, role, phone)
  - ask: ambiguous field → structured question for operator/LLM
  - handoff: blocker (CAPTCHA, OAuth, file upload, custom widgets, anti-bot)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal, Optional

from shadow_web.schema_snap import parse_forms

ExecutionAction = Literal["auto_fill", "ask", "handoff", "skip", "submit"]
FieldKind = Literal[
    "email",
    "name",
    "first_name",
    "last_name",
    "company",
    "role",
    "phone",
    "password",
    "select",
    "textarea",
    "checkbox",
    "date",
    "file",
    "hidden",
    "oauth",
    "captcha",
    "unknown",
]

SAFE_FIELD_KINDS = frozenset({"email", "name", "first_name", "last_name", "company", "role", "phone"})

KNOWN_PROFILE_KEYS = frozenset({
    "email",
    "name",
    "full_name",
    "first_name",
    "last_name",
    "company",
    "role",
    "job_title",
    "phone",
    "tel",
})

RECOMMENDED_PROFILE_KEYS = frozenset({"email", "name"})

_PROFILE_ALIASES: dict[str, tuple[str, ...]] = {
    "email": ("email", "e-mail", "mail", "work email", "business email"),
    "name": ("name", "full name", "your name", "display name"),
    "first_name": ("first name", "firstname", "given name"),
    "last_name": ("last name", "lastname", "surname", "family name"),
    "company": ("company", "organization", "organisation", "employer", "business"),
    "role": ("role", "job title", "title", "position", "job"),
    "phone": ("phone", "telephone", "mobile", "cell", "tel"),
}

_BLOCKER_LABEL = re.compile(
    r"\b(captcha|recaptcha|hcaptcha|turnstile|verify you are human|i'?m not a robot)\b",
    re.I,
)
_OAUTH_LABEL = re.compile(
    r"\b(sign in with|log in with|continue with|login with)\s+(google|github|apple|microsoft|facebook|sso|oauth)\b"
    r"|^(google|github|apple|microsoft|facebook)$",
    re.I,
)
_AMBIGUOUS_LABEL = re.compile(
    r"\b(how did you hear|referr|message|comment|bio|description|reason|why|notes|website|url|address line)\b",
    re.I,
)
_VALIDATION_MESSAGE = re.compile(
    r"\b(invalid|required|must be|please enter|please provide|error|too short|too long|format)\b",
    re.I,
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_INVALID_FIELDS_SCRIPT = """
() => {
  const out = [];
  for (const el of document.querySelectorAll("input, select, textarea")) {
    if (typeof el.checkValidity !== "function") continue;
    if (el.checkValidity()) continue;
    out.push({
      name: el.name || "",
      id: el.id || "",
      type: el.type || el.tagName.toLowerCase(),
      validationMessage: el.validationMessage || "Invalid value",
      value: el.value || "",
    });
  }
  return out;
}
"""


@dataclass
class LinkedField:
    """Schema field linked to an Action Map sid."""

    form_index: int
    field_index: int
    sid: Optional[str]
    tag: str
    type: str
    name: Optional[str] = None
    label: str = ""
    placeholder: str = ""
    required: bool = False
    options: list[dict[str, str]] = field(default_factory=list)
    kind: FieldKind = "unknown"
    match_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FormFillStep:
    action: ExecutionAction
    sid: Optional[str] = None
    value: Optional[str] = None
    field: Optional[dict[str, Any]] = None
    question: Optional[dict[str, Any]] = None
    handoff: Optional[dict[str, Any]] = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FormFillPlan:
    url: str = ""
    page_class: str = "Static"
    page_class_reason: str = ""
    form_index: int = 0
    steps: list[FormFillStep] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    profile_validation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "page_class": self.page_class,
            "page_class_reason": self.page_class_reason,
            "form_index": self.form_index,
            "steps": [s.to_dict() for s in self.steps],
            "blockers": self.blockers,
            "questions": self.questions,
            "profile_validation": self.profile_validation,
            "summary": summarize_plan(self),
        }


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[_\-\s]+", " ", text.lower()).strip()
    return text


def _label_blob(field: dict[str, Any]) -> str:
    parts = [field.get("label"), field.get("name"), field.get("placeholder")]
    return _norm(" ".join(p for p in parts if p))


def _action_label_blob(action: dict[str, Any]) -> str:
    return _norm(" ".join(p for p in (action.get("label"), action.get("placeholder")) if p))


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def classify_field_kind(field: dict[str, Any]) -> FieldKind:
    ftype = (field.get("type") or "text").lower()
    tag = (field.get("tag") or "input").lower()
    blob = _label_blob(field)

    if ftype == "hidden":
        return "hidden"
    if ftype == "file":
        return "file"
    if ftype in ("date", "datetime-local", "time", "month", "week"):
        return "date"
    if ftype == "password":
        return "password"
    if tag == "textarea" or ftype == "textarea":
        return "textarea"
    if tag == "select" or ftype == "select":
        return "select"
    if ftype == "checkbox":
        return "checkbox"
    if ftype == "email":
        return "email"
    if ftype == "tel":
        return "phone"

    if _BLOCKER_LABEL.search(blob):
        return "captcha"
    if _OAUTH_LABEL.search(blob):
        return "oauth"

    # Longer aliases first so "company name" beats bare "name".
    ranked: list[tuple[FieldKind, str]] = []
    for kind, aliases in _PROFILE_ALIASES.items():
        for alias in aliases:
            ranked.append((kind, alias))  # type: ignore[arg-type]
    ranked.sort(key=lambda item: len(item[1]), reverse=True)

    for kind, alias in ranked:
        if alias in blob or blob in alias:
            return kind

    if ftype == "email":
        return "email"
    return "unknown"


def _action_type_matches(action: dict[str, Any], field: dict[str, Any]) -> bool:
    atype = (action.get("type") or "").lower()
    ftype = (field.get("type") or "text").lower()
    tag = (field.get("tag") or "input").lower()

    if tag == "textarea" and atype == "textarea":
        return True
    if tag == "select" and atype == "select":
        return True
    if atype.startswith("input[") and atype[6:-1] == ftype:
        return True
    if atype == "input" and ftype == "text":
        return True
    if atype == "button" and ftype == "submit":
        return True
    if atype.startswith("input[") and ftype == "text" and atype == "input[text]":
        return True
    return False


def link_form_to_actions(
    forms: list[dict[str, Any]],
    action_map: list[dict[str, Any]],
    *,
    form_index: int = 0,
) -> list[LinkedField]:
    """Attach Action Map sids to schema_form fields."""
    if not forms or form_index >= len(forms):
        return []

    form = forms[form_index]
    linked: list[LinkedField] = []
    used_sids: set[str] = set()

    for fidx, raw in enumerate(form.get("fields") or []):
        ftype = (raw.get("type") or "text").lower()
        if ftype in ("submit", "button") and raw.get("tag") in ("button", "input"):
            # submit linked separately
            continue

        best_sid: Optional[str] = None
        best_score = 0.0
        blob = _label_blob(raw)

        for action in action_map:
            sid = str(action.get("id", ""))
            if not sid or sid in used_sids:
                continue
            if not _action_type_matches(action, raw):
                continue
            score = _similarity(blob, _action_label_blob(action))
            if raw.get("name"):
                score = max(score, _similarity(_norm(raw["name"]), _action_label_blob(action)))
            if score > best_score:
                best_score = score
                best_sid = sid

        if best_sid and best_score >= 0.45:
            used_sids.add(best_sid)

        kind = classify_field_kind(raw)
        linked.append(
            LinkedField(
                form_index=form_index,
                field_index=fidx,
                sid=best_sid,
                tag=raw.get("tag", "input"),
                type=ftype,
                name=raw.get("name"),
                label=raw.get("label") or "",
                placeholder=raw.get("placeholder") or "",
                required=bool(raw.get("required")),
                options=list(raw.get("options") or []),
                kind=kind,
                match_score=round(best_score, 3),
            )
        )

    return linked


def _find_submit_sid(form: dict[str, Any], action_map: list[dict[str, Any]], used_sids: set[str]) -> Optional[str]:
    for raw in form.get("fields") or []:
        if raw.get("type") in ("submit", "button") or raw.get("tag") == "button":
            blob = _label_blob(raw)
            for action in action_map:
                sid = str(action.get("id", ""))
                if sid in used_sids:
                    continue
                atype = (action.get("type") or "").lower()
                if atype not in ("button", "input[submit]"):
                    continue
                if _similarity(blob, _action_label_blob(action)) >= 0.4:
                    return sid

    for action in action_map:
        sid = str(action.get("id", ""))
        if sid in used_sids:
            continue
        atype = (action.get("type") or "").lower()
        if atype == "button":
            label = _action_label_blob(action)
            if any(k in label for k in ("submit", "sign up", "register", "continue", "next", "create")):
                return sid
    return None


def _profile_value(kind: FieldKind, profile: dict[str, Any]) -> Optional[str]:
    if kind == "email":
        return profile.get("email")
    if kind == "name":
        return profile.get("name") or profile.get("full_name")
    if kind == "first_name":
        return profile.get("first_name") or (profile.get("name", "").split()[0] if profile.get("name") else None)
    if kind == "last_name":
        parts = (profile.get("name") or "").split()
        return profile.get("last_name") or (parts[-1] if len(parts) > 1 else None)
    if kind == "company":
        return profile.get("company")
    if kind == "role":
        return profile.get("role") or profile.get("job_title")
    if kind == "phone":
        return profile.get("phone") or profile.get("tel")
    return None


def _detect_page_blockers(
    page_class: str,
    page_class_reason: str,
    action_map: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if page_class == "Anti-bot":
        blockers.append(
            {
                "code": "anti_bot",
                "reason": page_class_reason or "Anti-bot protection detected",
                "handoff": "human",
            }
        )
    if page_class == "Auth-gated":
        blockers.append(
            {
                "code": "auth_gated",
                "reason": page_class_reason or "Login or OAuth gate detected",
                "handoff": "human",
            }
        )

    for action in action_map:
        blob = _action_label_blob(action)
        if _BLOCKER_LABEL.search(blob):
            blockers.append(
                {
                    "code": "captcha",
                    "reason": f"CAPTCHA control: {action.get('label', '')[:80]}",
                    "handoff": "human",
                    "sid": action.get("id"),
                }
            )
        if _OAUTH_LABEL.search(blob):
            blockers.append(
                {
                    "code": "oauth",
                    "reason": f"OAuth / SSO control: {action.get('label', '')[:80]}",
                    "handoff": "human",
                    "sid": action.get("id"),
                }
            )
    return blockers


def _is_ambiguous(linked: LinkedField) -> bool:
    if linked.kind in SAFE_FIELD_KINDS:
        return False
    if linked.kind in ("file", "captcha", "oauth", "hidden", "password", "date"):
        return False
    if linked.kind == "textarea":
        return True
    if linked.kind == "select":
        return True
    if linked.kind == "checkbox":
        return True
    if linked.kind == "unknown":
        return True
    if _AMBIGUOUS_LABEL.search(_label_blob(linked.__dict__)):
        return True
    return False


def _is_blocker_field(linked: LinkedField, *, allow_password: bool = False) -> Optional[str]:
    if linked.kind == "file":
        return "file_upload"
    if linked.kind == "captcha":
        return "captcha"
    if linked.kind == "oauth":
        return "oauth"
    if linked.kind == "date":
        return "custom_datepicker"
    if linked.kind == "password" and not allow_password:
        return "password_requires_handoff"
    if linked.required and not linked.sid:
        return "missing_binding_invisible_validation"
    return None


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Validate onboarding profile keys; warn on typos like companny."""
    unknown_keys = sorted(k for k in profile if k not in KNOWN_PROFILE_KEYS)
    missing_recommended = sorted(k for k in RECOMMENDED_PROFILE_KEYS if not profile.get(k))
    warnings: list[dict[str, Any]] = []

    if unknown_keys:
        warnings.append(
            {
                "code": "unknown_profile_keys",
                "message": "Unknown profile keys will be ignored during auto_fill",
                "keys": unknown_keys,
            }
        )
    if missing_recommended:
        warnings.append(
            {
                "code": "missing_recommended_profile_keys",
                "message": "Recommended profile keys are empty",
                "keys": missing_recommended,
            }
        )

    return {
        "valid": len(unknown_keys) == 0,
        "unknown_keys": unknown_keys,
        "missing_recommended": missing_recommended,
        "warnings": warnings,
        "known_keys": sorted(KNOWN_PROFILE_KEYS),
    }


def validate_value_for_field(value: str, field: Optional[dict[str, Any]]) -> Optional[str]:
    """Pre-flight value check before fill (email format, etc.)."""
    if not field:
        return None
    kind = field.get("kind") or field.get("type")
    if kind == "email" and value and not _EMAIL_RE.match(value.strip()):
        return f"Value '{value}' is not a valid email format"
    return None


def match_validation_errors(
    filled_steps: list[FormFillStep],
    invalid_fields: list[dict[str, Any]],
    *,
    diff_actions: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Map browser :invalid fields and diff validation messages to filled steps."""
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in invalid_fields:
        name = (item.get("name") or "").strip()
        fid = (item.get("id") or "").strip()
        for step in filled_steps:
            if step.action != "auto_fill" or not step.field:
                continue
            field = step.field
            field_name = (field.get("name") or "").strip()
            if name and field_name and name != field_name:
                continue
            if fid and field.get("id") and fid != field.get("id"):
                continue
            key = f"{step.sid}:{name or fid}"
            if key in seen:
                continue
            seen.add(key)
            errors.append(
                {
                    "sid": step.sid,
                    "field": field,
                    "value": step.value,
                    "message": item.get("validationMessage") or "Client validation failed",
                    "source": "constraint_validation",
                }
            )
            break

    if diff_actions:
        for action in diff_actions:
            label = (action.get("label") or "").strip()
            if not label or not _VALIDATION_MESSAGE.search(label):
                continue
            for step in filled_steps:
                if step.action != "auto_fill" or not step.field:
                    continue
                blob = _label_blob(step.field)
                if blob and (blob in _norm(label) or _norm(label) in blob):
                    key = f"diff:{step.sid}:{label[:40]}"
                    if key in seen:
                        continue
                    seen.add(key)
                    errors.append(
                        {
                            "sid": step.sid,
                            "field": step.field,
                            "value": step.value,
                            "message": label,
                            "source": "diff_appeared",
                        }
                    )
                    break

    return errors


async def collect_validation_errors_async(
    shadow: Any,
    filled_steps: list[FormFillStep],
) -> list[dict[str, Any]]:
    """snapshot(diff) + HTML5 constraint validation in live DOM."""
    invalid_fields: list[dict[str, Any]] = []
    try:
        await shadow.refresh(diff=True)
        invalid_fields = await shadow.page.evaluate(_INVALID_FIELDS_SCRIPT)
    except Exception:
        invalid_fields = []

    diff_actions: list[dict[str, Any]] = []
    if getattr(shadow, "last_diff", None):
        diff_actions = [e.action for e in shadow.last_diff.appeared + shadow.last_diff.changed]

    return match_validation_errors(filled_steps, invalid_fields, diff_actions=diff_actions)


def collect_validation_errors_sync(
    shadow: Any,
    filled_steps: list[FormFillStep],
) -> list[dict[str, Any]]:
    invalid_fields: list[dict[str, Any]] = []
    try:
        shadow.refresh(diff=True)
        invalid_fields = shadow.page.evaluate(_INVALID_FIELDS_SCRIPT)
    except Exception:
        invalid_fields = []

    diff_actions: list[dict[str, Any]] = []
    if getattr(shadow, "last_diff", None):
        diff_actions = [e.action for e in shadow.last_diff.appeared + shadow.last_diff.changed]

    return match_validation_errors(filled_steps, invalid_fields, diff_actions=diff_actions)


def plan_from_dict(data: dict[str, Any]) -> FormFillPlan:
    steps = [FormFillStep(**step) for step in data.get("steps", [])]
    return FormFillPlan(
        url=data.get("url", ""),
        page_class=data.get("page_class", "Static"),
        page_class_reason=data.get("page_class_reason", ""),
        form_index=int(data.get("form_index", 0)),
        steps=steps,
        blockers=list(data.get("blockers") or []),
        questions=list(data.get("questions") or []),
        profile_validation=dict(data.get("profile_validation") or {}),
    )


def build_form_fill_plan(
    *,
    url: str,
    forms: list[dict[str, Any]],
    action_map: list[dict[str, Any]],
    profile: dict[str, Any],
    page_class: str = "Static",
    page_class_reason: str = "",
    form_index: int = 0,
    allow_password: bool = False,
    auto_submit: bool = True,
) -> FormFillPlan:
    """Build AgentOps plan: auto_fill safe fields, ask ambiguous, handoff blockers."""
    profile_validation = validate_profile(profile)

    plan = FormFillPlan(
        url=url,
        page_class=page_class,
        page_class_reason=page_class_reason,
        form_index=form_index,
        profile_validation=profile_validation,
    )

    page_blockers = _detect_page_blockers(page_class, page_class_reason, action_map)
    if page_blockers:
        plan.blockers.extend(page_blockers)
        for b in page_blockers:
            plan.steps.append(
                FormFillStep(
                    action="handoff",
                    handoff=b,
                    reason=b.get("code", "page_blocker"),
                )
            )
        return plan

    if not forms:
        plan.steps.append(
            FormFillStep(action="handoff", handoff={"code": "no_forms", "reason": "No HTML forms found"}, reason="no_forms")
        )
        return plan

    linked_fields = link_form_to_actions(forms, action_map, form_index=form_index)
    used_sids: set[str] = {lf.sid for lf in linked_fields if lf.sid}

    for lf in linked_fields:
        field_dict = lf.to_dict()
        blocker = _is_blocker_field(lf, allow_password=allow_password)
        if blocker:
            handoff = {
                "code": blocker,
                "field": field_dict,
                "reason": f"Field requires human: {lf.label or lf.name or lf.kind}",
                "handoff": "human",
            }
            plan.blockers.append(handoff)
            plan.steps.append(FormFillStep(action="handoff", field=field_dict, handoff=handoff, reason=blocker))
            continue

        if lf.kind == "hidden":
            plan.steps.append(FormFillStep(action="skip", field=field_dict, reason="hidden_field"))
            continue

        if lf.kind in SAFE_FIELD_KINDS:
            value = _profile_value(lf.kind, profile)
            if value and lf.sid:
                plan.steps.append(
                    FormFillStep(action="auto_fill", sid=lf.sid, value=str(value), field=field_dict, reason="safe_field")
                )
                continue
            if lf.required and not value:
                question = _build_question(lf, reason="missing_profile_value")
                plan.questions.append(question)
                plan.steps.append(FormFillStep(action="ask", field=field_dict, question=question, reason="missing_profile_value"))
                continue

        if _is_ambiguous(lf) or lf.kind not in SAFE_FIELD_KINDS:
            question = _build_question(lf, reason="ambiguous_field")
            plan.questions.append(question)
            plan.steps.append(FormFillStep(action="ask", field=field_dict, question=question, reason="ambiguous_field"))
            continue

        if lf.sid:
            question = _build_question(lf, reason="unclassified_field")
            plan.questions.append(question)
            plan.steps.append(FormFillStep(action="ask", field=field_dict, question=question, reason="unclassified_field"))

    if auto_submit and not plan.blockers:
        submit_sid = _find_submit_sid(forms[form_index], action_map, used_sids)
        if submit_sid:
            plan.steps.append(FormFillStep(action="submit", sid=submit_sid, reason="form_submit"))

    return plan


def _build_question(linked: LinkedField, *, reason: str) -> dict[str, Any]:
    label = linked.label or linked.name or linked.kind
    q: dict[str, Any] = {
        "id": f"form{linked.form_index}_field{linked.field_index}",
        "sid": linked.sid,
        "label": label,
        "kind": linked.kind,
        "required": linked.required,
        "reason": reason,
        "prompt": f"What value should be used for '{label}'?",
    }
    if linked.options:
        q["options"] = linked.options
        q["prompt"] = f"Choose a value for '{label}'"
    return q


def summarize_plan(plan: FormFillPlan) -> dict[str, int]:
    counts = {"auto_fill": 0, "ask": 0, "handoff": 0, "skip": 0, "submit": 0}
    for step in plan.steps:
        counts[step.action] = counts.get(step.action, 0) + 1
    return counts


def apply_question_answers(plan: FormFillPlan, answers: dict[str, str]) -> FormFillPlan:
    """Merge operator answers into plan (ask steps → auto_fill)."""
    new_steps: list[FormFillStep] = []
    for step in plan.steps:
        if step.action != "ask" or not step.question:
            new_steps.append(step)
            continue
        qid = step.question.get("id", "")
        sid = step.question.get("sid")
        value = answers.get(qid) or answers.get(step.question.get("label", ""))
        if value and sid:
            new_steps.append(
                FormFillStep(
                    action="auto_fill",
                    sid=sid,
                    value=value,
                    field=step.field,
                    reason="answered",
                )
            )
        else:
            new_steps.append(step)
    plan.steps = new_steps
    plan.questions = [s.question for s in plan.steps if s.action == "ask" and s.question]
    return plan


def _execution_result(
    plan: FormFillPlan,
    results: list[dict[str, Any]],
    *,
    status: str,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "results": results,
        "plan_summary": summarize_plan(plan),
        "questions": plan.questions,
        "handoffs": plan.blockers,
        "filled": [r for r in results if r.get("action") == "fill"],
    }
    payload.update(extra)
    return payload


async def execute_form_fill_plan_async(
    shadow: Any,
    plan: FormFillPlan,
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Run executable steps on AsyncShadowPage. Stops on handoff or validation_error."""
    results: list[dict[str, Any]] = []
    filled_steps: list[FormFillStep] = []

    for step in plan.steps:
        if step.action == "handoff":
            return _execution_result(
                plan,
                results,
                status="handoff",
                handoff=step.handoff,
            )
        if step.action == "ask":
            results.append({"action": step.action, "field": step.field, "question": step.question})
            continue
        if step.action == "skip":
            results.append({"action": step.action, "field": step.field})
            continue
        if step.action == "auto_fill" and step.sid and step.value is not None:
            preflight = validate_value_for_field(step.value, step.field)
            if preflight:
                return _execution_result(
                    plan,
                    results,
                    status="validation_error",
                    errors=[
                        {
                            "sid": step.sid,
                            "field": step.field,
                            "value": step.value,
                            "message": preflight,
                            "source": "preflight",
                        }
                    ],
                )
            await shadow.fill(step.sid, step.value)
            filled_steps.append(step)
            results.append({"action": "fill", "sid": step.sid, "value": step.value, "field": step.field})
            if validate:
                errors = await collect_validation_errors_async(shadow, filled_steps)
                if errors:
                    return _execution_result(plan, results, status="validation_error", errors=errors)
            continue
        if step.action == "submit" and step.sid:
            await shadow.click(step.sid)
            results.append({"action": "click", "sid": step.sid, "reason": "submit"})
            if validate:
                await shadow.refresh(diff=True)
            continue

    if any(s.action == "ask" for s in plan.steps):
        return _execution_result(plan, results, status="questions_pending")

    return _execution_result(plan, results, status="completed")


def execute_form_fill_plan(
    shadow: Any,
    plan: FormFillPlan,
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Run executable steps on sync ShadowPage. Stops on handoff or validation_error."""
    results: list[dict[str, Any]] = []
    filled_steps: list[FormFillStep] = []

    for step in plan.steps:
        if step.action == "handoff":
            return _execution_result(
                plan,
                results,
                status="handoff",
                handoff=step.handoff,
            )
        if step.action == "ask":
            results.append({"action": step.action, "field": step.field, "question": step.question})
            continue
        if step.action == "skip":
            results.append({"action": step.action, "field": step.field})
            continue
        if step.action == "auto_fill" and step.sid and step.value is not None:
            preflight = validate_value_for_field(step.value, step.field)
            if preflight:
                return _execution_result(
                    plan,
                    results,
                    status="validation_error",
                    errors=[
                        {
                            "sid": step.sid,
                            "field": step.field,
                            "value": step.value,
                            "message": preflight,
                            "source": "preflight",
                        }
                    ],
                )
            shadow.fill(step.sid, step.value)
            filled_steps.append(step)
            results.append({"action": "fill", "sid": step.sid, "value": step.value, "field": step.field})
            if validate:
                errors = collect_validation_errors_sync(shadow, filled_steps)
                if errors:
                    return _execution_result(plan, results, status="validation_error", errors=errors)
            continue
        if step.action == "submit" and step.sid:
            shadow.click(step.sid)
            results.append({"action": "click", "sid": step.sid, "reason": "submit"})
            if validate:
                shadow.refresh(diff=True)
            continue

    if any(s.action == "ask" for s in plan.steps):
        return _execution_result(plan, results, status="questions_pending")

    return _execution_result(plan, results, status="completed")


async def execute_form_fill_plan_multi_step_async(
    shadow: Any,
    profile: dict[str, Any],
    *,
    max_steps: int = 3,
    answers: Optional[dict[str, str]] = None,
    allow_password: bool = False,
    validate: bool = True,
    wait_after_submit_ms: int = 1500,
) -> dict[str, Any]:
    """Enterprise onboarding loop: plan → execute → snapshot → plan (next wizard step)."""
    steps_log: list[dict[str, Any]] = []
    answers = answers or {}
    previous_url = ""

    for step_index in range(max(1, max_steps)):
        await shadow.refresh()
        plan = plan_from_session(
            url=shadow.last_url,
            clean_html=shadow.clean_html,
            action_map=shadow.action_map,
            profile=profile,
            page_class=shadow.page_class,
            page_class_reason=shadow.page_class_reason,
            allow_password=allow_password,
            auto_submit=True,
        )
        if answers:
            apply_question_answers(plan, answers)

        if plan.blockers:
            return {
                "status": "handoff",
                "step_index": step_index,
                "handoffs": plan.blockers,
                "plan": plan.to_dict(),
                "steps": steps_log,
            }

        executable = [s for s in plan.steps if s.action in ("auto_fill", "submit")]
        if not executable:
            if plan.questions:
                return {
                    "status": "questions_pending",
                    "step_index": step_index,
                    "questions": plan.questions,
                    "plan": plan.to_dict(),
                    "steps": steps_log,
                }
            break

        result = await execute_form_fill_plan_async(shadow, plan, validate=validate)
        result["step_index"] = step_index
        steps_log.append(result)

        if result["status"] in ("handoff", "validation_error", "questions_pending"):
            return {"status": result["status"], "steps": steps_log, **result}

        if result["status"] != "completed":
            break

        if wait_after_submit_ms > 0:
            await shadow.page.wait_for_timeout(wait_after_submit_ms)

        if shadow.last_url == previous_url and step_index > 0:
            forms = parse_forms(shadow.clean_html)
            if not forms:
                break
        previous_url = shadow.last_url

    return {"status": "completed", "steps": steps_log, "step_count": len(steps_log)}


def execute_form_fill_plan_multi_step(
    shadow: Any,
    profile: dict[str, Any],
    *,
    max_steps: int = 3,
    answers: Optional[dict[str, str]] = None,
    allow_password: bool = False,
    validate: bool = True,
    wait_after_submit_ms: int = 1500,
) -> dict[str, Any]:
    """Sync multi-step form fill loop."""
    import time

    steps_log: list[dict[str, Any]] = []
    answers = answers or {}
    previous_url = ""

    for step_index in range(max(1, max_steps)):
        shadow.refresh()
        plan = plan_from_session(
            url=shadow.last_url,
            clean_html=shadow.clean_html,
            action_map=shadow.action_map,
            profile=profile,
            page_class=shadow.page_class,
            page_class_reason=shadow.page_class_reason,
            allow_password=allow_password,
            auto_submit=True,
        )
        if answers:
            apply_question_answers(plan, answers)

        if plan.blockers:
            return {
                "status": "handoff",
                "step_index": step_index,
                "handoffs": plan.blockers,
                "plan": plan.to_dict(),
                "steps": steps_log,
            }

        executable = [s for s in plan.steps if s.action in ("auto_fill", "submit")]
        if not executable:
            if plan.questions:
                return {
                    "status": "questions_pending",
                    "step_index": step_index,
                    "questions": plan.questions,
                    "plan": plan.to_dict(),
                    "steps": steps_log,
                }
            break

        result = execute_form_fill_plan(shadow, plan, validate=validate)
        result["step_index"] = step_index
        steps_log.append(result)

        if result["status"] in ("handoff", "validation_error", "questions_pending"):
            return {"status": result["status"], "steps": steps_log, **result}

        if result["status"] != "completed":
            break

        if wait_after_submit_ms > 0:
            time.sleep(wait_after_submit_ms / 1000.0)

        if shadow.last_url == previous_url and step_index > 0:
            forms = parse_forms(shadow.clean_html)
            if not forms:
                break
        previous_url = shadow.last_url

    return {"status": "completed", "steps": steps_log, "step_count": len(steps_log)}


def plan_from_session(
    *,
    url: str,
    clean_html: str,
    action_map: list[dict[str, Any]],
    profile: dict[str, Any],
    page_class: str = "Static",
    page_class_reason: str = "",
    form_index: int = 0,
    **kwargs: Any,
) -> FormFillPlan:
    """Convenience: parse forms from clean_html and build plan."""
    forms = parse_forms(clean_html)
    return build_form_fill_plan(
        url=url,
        forms=forms,
        action_map=action_map,
        profile=profile,
        page_class=page_class,
        page_class_reason=page_class_reason,
        form_index=form_index,
        **kwargs,
    )
