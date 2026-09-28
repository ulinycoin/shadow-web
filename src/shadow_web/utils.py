from typing import Any

# How long to let a click's navigation start before inspecting the new document.
NAVIGATION_SETTLE_MS = 250


def _parse_eval_res(raw: Any) -> Any:
    """
    Parse javascript execution results.
    cdp-use in browser-use stringifies objects and arrays into JSON strings,
    so we need to deserialize them back to dictionaries/lists.
    """
    if isinstance(raw, str) and raw.strip().startswith(("{", "[")):
        try:
            import json
            return json.loads(raw)
        except Exception:
            return raw
    return raw


# Playwright error fragments raised when a navigation destroys the JS execution
# context mid-evaluate. The observation is retryable; the action that triggered
# the navigation is not (it already happened), so callers retry the capture
# instead of failing the whole interaction.
_TRANSIENT_NAVIGATION_ERRORS = (
    "Execution context was destroyed",
    "Cannot find context with specified id",
    "because of a navigation",
    "frame was detached",
    "Target navigated",
)


def is_transient_navigation_error(exc: BaseException | str) -> bool:
    """True when `exc` is a navigation race, not a real failure.

    Deliberately excludes "Target closed" / "browser has been closed": a page
    that is gone is a terminal condition, not something to retry.
    """
    message = str(exc)
    if "closed" in message.lower() and "context" not in message.lower():
        return False
    return any(fragment in message for fragment in _TRANSIENT_NAVIGATION_ERRORS)

