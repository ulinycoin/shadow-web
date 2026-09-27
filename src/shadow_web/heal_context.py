"""Redact page context before sending it to a remote selector-healing LLM.

Self-healing falls back to an LLM when local fuzzy matching fails, and that
request carries a slice of the page.  The slice is taken from around the broken
element, so it can include what the user typed (a textarea's text, an input's
``value``) as well as hidden fields -- and ``heal_api_url`` may be a third-party
service.  This module strips those values and bounds the payload size, so a heal
request cannot leak credentials or form contents.

Behaviour is controlled by ``SHADOW_WEB_HEAL_SHARE_CONTEXT`` (default ``1``).
Set it to ``0`` to send only a minimal element stub derived from the Action Map
(label/type) instead of any page markup.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

REDACTED = "[redacted]"
DEFAULT_MAX_CONTEXT_CHARS = 4000

_VALUE_ATTR_RE = re.compile(r"""(value\s*=\s*)(["']).*?\2""", re.IGNORECASE | re.DOTALL)
_TEXTAREA_RE = re.compile(
    r"(<textarea\b[^>]*>).*?(</textarea\s*>)", re.IGNORECASE | re.DOTALL
)
_SAFE_ATTRS_TO_REDACT = {
    "data-value",
    "data-token",
    "data-secret",
    "data-api-key",
    "data-key",
    "data-password",
}
# Checkbox/radio values are structural ("on"/"yes"), not user input.
_VALUE_SAFE_INPUT_TYPES = {"checkbox", "radio", "submit", "button", "reset", "image", "file"}


def share_page_context() -> bool:
    """Whether a heal request may include (redacted) page markup at all."""
    flag = os.environ.get("SHADOW_WEB_HEAL_SHARE_CONTEXT", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def minimal_context(action_label: str = "", action_type: str = "") -> str:
    """Smallest useful context when page markup must not leave the process."""
    tag = (action_type or "div").split("[")[0] or "div"
    label = (action_label or "").replace("<", " ").replace(">", " ")
    return f"<{tag}>{label}</{tag}>"


def _redact_with_lxml(raw: str) -> Optional[str]:
    try:
        from lxml import html as lxml_html
    except ImportError:  # pragma: no cover - lxml is a hard dependency
        return None
    try:
        root = lxml_html.fromstring(raw, parser=lxml_html.HTMLParser(no_network=True))
    except Exception as exc:  # noqa: BLE001 - fall back to the regex scrubber
        logger.debug("heal context: lxml redaction failed (%s)", exc)
        return None

    for element in root.iter():
        tag = element.tag if isinstance(element.tag, str) else ""
        tag = tag.lower()
        if tag == "textarea":
            for child in list(element):
                element.remove(child)
            element.text = REDACTED
        elif tag == "input":
            input_type = (element.get("type") or "text").lower()
            if input_type not in _VALUE_SAFE_INPUT_TYPES and element.get("value"):
                element.set("value", REDACTED)
        for attr in list(element.attrib):
            if attr.lower() in _SAFE_ATTRS_TO_REDACT:
                element.set(attr, REDACTED)
    try:
        return lxml_html.tostring(root, encoding="unicode")
    except Exception as exc:  # noqa: BLE001
        logger.debug("heal context: lxml serialisation failed (%s)", exc)
        return None


def _redact_with_regex(raw: str) -> str:
    scrubbed = _TEXTAREA_RE.sub(rf"\1{REDACTED}\2", raw)
    scrubbed = _VALUE_ATTR_RE.sub(rf"\1\2{REDACTED}\2", scrubbed)
    for attr in _SAFE_ATTRS_TO_REDACT:
        scrubbed = re.sub(
            rf'({attr}\s*=\s*)(["\']).*?\2',
            rf"\1\2{REDACTED}\2",
            scrubbed,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return scrubbed


def redact_for_llm(raw: str, max_chars: int = DEFAULT_MAX_CONTEXT_CHARS) -> str:
    """Return ``raw`` with user-entered/hidden values removed and length bounded."""
    if not raw:
        return ""
    limit = max(1, int(max_chars))
    redacted = _redact_with_lxml(raw)
    if redacted is None:
        redacted = _redact_with_regex(raw)
    if len(redacted) > limit:
        redacted = redacted[:limit]
    return redacted
