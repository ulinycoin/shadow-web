# Form Fill — AgentOps playbook

Three execution modes per field:

| Mode | When | Result |
|------|------|--------|
| `auto_fill` | email, name, company, role, phone | `fill(sid, value)` |
| `ask` | textarea, select, unknown labels | `plan.questions[]` |
| `handoff` | CAPTCHA, OAuth, file, password, anti-bot | `status: handoff` |

## MCP workflow (single step)

```
form_fill_plan(profile='{"email":"...","name":"...","company":"..."}', url="https://...")
form_fill_execute(plan='...', answers='{"form0_field4":"..."}')
```

## MCP workflow (multi-step wizard)

```
navigate(url)
form_fill_execute(profile='{"email":"..."}', multi_step=true, max_steps=3)
```

## Status codes

| status | Meaning |
|--------|---------|
| `completed` | Fills (+ optional submit) ran |
| `questions_pending` | Operator must answer `questions` |
| `handoff` | Human required (CAPTCHA, OAuth, file upload) |
| `validation_error` | Client validation failed after fill — see `errors[]` |

## Profile keys

Known: `email`, `name`, `full_name`, `first_name`, `last_name`, `company`, `role`, `job_title`, `phone`, `tel`.

Unknown keys (e.g. `companny`) → warning at plan time via `profile_validation`.

## Validation loop

After each `fill()`:

1. `snapshot(diff=true)`
2. HTML5 `checkValidity()` on live DOM
3. Diff scan for appeared validation messages

Returns `validation_error` with `{ sid, field, message, source }`.
