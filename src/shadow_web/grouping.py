"""Semantic grouping for Action Map elements (forms, nav, modals)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

LOGIN_HINTS = re.compile(
    r"login|log in|sign in|signin|password|email|username",
    re.I,
)
CHECKOUT_HINTS = re.compile(r"checkout|payment|billing|cart|buy|order", re.I)
SEARCH_HINTS = re.compile(r"search|query|find", re.I)


def _clean(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _form_group_name(form_el) -> str:
    for attr in ("aria-label", "name", "id"):
        val = _clean(form_el.get(attr))
        if val:
            return val[:80]
    legend = form_el.find(".//legend")
    if legend is not None:
        text = _clean(legend.text_content())
        if text:
            return text[:80]

    fields = form_el.xpath(".//input | .//select | .//textarea")
    types = {(_clean(f.get("type")) or "text").lower() for f in fields}
    labels = " ".join(_clean(f.get("placeholder")) for f in fields)

    if "password" in types or LOGIN_HINTS.search(labels):
        return "Login Form"
    if "search" in types or SEARCH_HINTS.search(labels):
        return "Search Form"
    if CHECKOUT_HINTS.search(labels):
        return "Checkout Form"
    return "Form"


def _section_group_name(el) -> Optional[str]:
    tag = el.tag.lower() if isinstance(el.tag, str) else ""
    role = _clean(el.get("role")).lower()

    if tag == "form":
        return _form_group_name(el)
    if tag == "nav" or role == "navigation":
        return "Navigation"
    if tag in ("header", "footer", "main", "aside"):
        return tag.capitalize()
    if role in ("dialog", "alertdialog"):
        return _clean(el.get("aria-label")) or "Dialog"
    if role == "search":
        return "Search"
    return None


def _find_group_for_element(el) -> str:
    current = el.getparent()
    while current is not None:
        name = _section_group_name(current)
        if name:
            return name
        current = current.getparent()
    return "Page"


def _sid_for_element(el) -> Optional[str]:
    sid = el.get("data-sid")
    return sid if sid else None


def group_action_map(tree: etree._Element, action_map: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Assign each action to a semantic group based on DOM ancestry.

    Returns a list of groups:
        [{"name": "Login Form", "elements": [{...action...}, ...]}, ...]
    """
    sid_to_group: Dict[str, str] = {}
    for el in tree.iter():
        sid = _sid_for_element(el)
        if sid:
            sid_to_group[sid] = _find_group_for_element(el)

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []

    for action in action_map:
        sid = action.get("id", "")
        group_name = sid_to_group.get(sid, "Page")
        enriched = dict(action)
        enriched["group"] = group_name

        if group_name not in buckets:
            buckets[group_name] = []
            order.append(group_name)
        buckets[group_name].append(enriched)

    return [{"name": name, "elements": buckets[name]} for name in order]


def apply_groups_to_actions(
    action_map: List[Dict[str, Any]], groups: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Flatten grouped actions back into a list with ``group`` field set."""
    grouped: List[Dict[str, Any]] = []
    for block in groups:
        for action in block.get("elements", []):
            grouped.append(action)
    if grouped:
        return grouped
    return action_map
