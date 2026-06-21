"""Opt-in page diff for Action Map snapshots (with skeleton + breadcrumbs)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from lxml import etree


@dataclass
class PageSnapshot:
    """Baseline snapshot used for diffing."""

    url: str
    title: str
    interaction_mode: str
    action_map: List[Dict[str, Any]]
    action_groups: List[Dict[str, Any]]
    fingerprints: Dict[str, str] = field(default_factory=dict)  # fp -> sid


@dataclass
class DiffEntry:
    sid: str
    fingerprint: str
    breadcrumb: str
    action: Dict[str, Any]
    previous: Optional[Dict[str, Any]] = None


@dataclass
class PageDiff:
    url: str
    title: str
    interaction_mode: str
    skeleton_groups: List[str]
    appeared: List[DiffEntry] = field(default_factory=list)
    changed: List[DiffEntry] = field(default_factory=list)
    disappeared: List[DiffEntry] = field(default_factory=list)
    is_full_snapshot: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.appeared or self.changed or self.disappeared)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "interaction_mode": self.interaction_mode,
            "skeleton_groups": self.skeleton_groups,
            "is_full_snapshot": self.is_full_snapshot,
            "appeared": [entry.action for entry in self.appeared],
            "changed": [
                {"current": entry.action, "previous": entry.previous}
                for entry in self.changed
            ],
            "disappeared": [entry.action for entry in self.disappeared],
        }


def build_snapshot(
    url: str,
    title: str,
    interaction_mode: str,
    action_map: List[Dict[str, Any]],
    action_groups: List[Dict[str, Any]],
) -> PageSnapshot:
    fps = _assign_fingerprints(action_map)
    return PageSnapshot(
        url=url,
        title=title,
        interaction_mode=interaction_mode,
        action_map=list(action_map),
        action_groups=list(action_groups),
        fingerprints=fps,
    )


def compute_page_diff(
    previous: Optional[PageSnapshot],
    current: PageSnapshot,
) -> PageDiff:
    """Compare two snapshots. First snapshot returns full baseline marker."""
    skeleton_groups = [block.get("name", "Page") for block in current.action_groups]

    if previous is None or previous.url != current.url:
        return PageDiff(
            url=current.url,
            title=current.title,
            interaction_mode=current.interaction_mode,
            skeleton_groups=skeleton_groups,
            is_full_snapshot=True,
        )

    prev_by_fp = _index_actions(previous.action_map)
    curr_by_fp = _index_actions(current.action_map)
    prev_fps = set(prev_by_fp.keys())
    curr_fps = set(curr_by_fp.keys())

    appeared: List[DiffEntry] = []
    changed: List[DiffEntry] = []
    disappeared: List[DiffEntry] = []

    for fp in sorted(curr_fps - prev_fps):
        action = curr_by_fp[fp]
        appeared.append(
            DiffEntry(
                sid=str(action.get("id", "")),
                fingerprint=fp,
                breadcrumb=_breadcrumb(action),
                action=action,
            )
        )

    for fp in sorted(prev_fps - curr_fps):
        action = prev_by_fp[fp]
        disappeared.append(
            DiffEntry(
                sid=str(action.get("id", "")),
                fingerprint=fp,
                breadcrumb=_breadcrumb(action),
                action=action,
            )
        )

    for fp in sorted(prev_fps & curr_fps):
        prev_action = prev_by_fp[fp]
        curr_action = curr_by_fp[fp]
        if _action_content_hash(prev_action) != _action_content_hash(curr_action):
            changed.append(
                DiffEntry(
                    sid=str(curr_action.get("id", "")),
                    fingerprint=fp,
                    breadcrumb=_breadcrumb(curr_action),
                    action=curr_action,
                    previous=prev_action,
                )
            )

    return PageDiff(
        url=current.url,
        title=current.title,
        interaction_mode=current.interaction_mode,
        skeleton_groups=skeleton_groups,
        appeared=appeared,
        changed=changed,
        disappeared=disappeared,
        is_full_snapshot=False,
    )


def generate_diff_xml(diff: PageDiff, full_xml: str = "") -> str:
    """Render diff as XML. Falls back to full snapshot when requested or no baseline."""
    if diff.is_full_snapshot and full_xml:
        return full_xml

    root = etree.Element(
        "page",
        url=diff.url,
        title=diff.title,
        mode=diff.interaction_mode,
        diff="true",
    )
    skeleton = etree.SubElement(root, "skeleton")
    etree.SubElement(skeleton, "url").text = diff.url
    etree.SubElement(skeleton, "title").text = diff.title
    groups_el = etree.SubElement(skeleton, "groups")
    for name in diff.skeleton_groups:
        etree.SubElement(groups_el, "group", name=name)

    delta = etree.SubElement(root, "delta")
    _append_diff_section(delta, "appeared", diff.appeared)
    _append_diff_section(delta, "changed", diff.changed, include_previous=True)
    _append_diff_section(delta, "disappeared", diff.disappeared)

    if not diff.has_changes:
        etree.SubElement(delta, "note").text = "No action changes since last snapshot."

    return etree.tostring(root, encoding="unicode", pretty_print=True)


def diff_terse(diff: PageDiff) -> str:
    """Compact diff summary for LLM context."""
    if diff.is_full_snapshot:
        return f"# snapshot (full) {diff.url} [{', '.join(diff.skeleton_groups)}]"

    lines = [
        f"# diff {diff.url}",
        f"groups: {', '.join(diff.skeleton_groups)}",
    ]
    for section, entries in (
        ("appeared", diff.appeared),
        ("changed", diff.changed),
        ("disappeared", diff.disappeared),
    ):
        if not entries:
            continue
        lines.append(f"## {section}")
        for entry in entries:
            label = entry.action.get("label") or entry.action.get("tool_name") or entry.action.get("type")
            lines.append(f"@{entry.sid} {entry.breadcrumb} — {label}")
    if not diff.has_changes:
        lines.append("(no changes)")
    return "\n".join(lines)


def _assign_fingerprints(action_map: List[Dict[str, Any]]) -> Dict[str, str]:
    counts: Dict[str, int] = {}
    mapping: Dict[str, str] = {}
    for action in action_map:
        base = _action_fingerprint_base(action)
        counts[base] = counts.get(base, 0) + 1
        fp = base if counts[base] == 1 else f"{base}#{counts[base]}"
        mapping[fp] = str(action.get("id", ""))
    return mapping


def _index_actions(action_map: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    counts: Dict[str, int] = {}
    for action in action_map:
        base = _action_fingerprint_base(action)
        counts[base] = counts.get(base, 0) + 1
        fp = base if counts[base] == 1 else f"{base}#{counts[base]}"
        indexed[fp] = action
    return indexed


def _action_fingerprint_base(action: Dict[str, Any]) -> str:
    parts = [
        action.get("group", "Page"),
        action.get("type", ""),
        _norm(action.get("label", "")),
        _norm(action.get("placeholder", "")),
        action.get("href", ""),
        action.get("tool_name", ""),
        action.get("bind_id", ""),
    ]
    return "|".join(parts)


def _action_content_hash(action: Dict[str, Any]) -> str:
    payload = {
        key: action.get(key)
        for key in ("type", "label", "placeholder", "href", "group", "tool_name", "input_schema")
        if action.get(key)
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _breadcrumb(action: Dict[str, Any]) -> str:
    group = action.get("group") or "Page"
    kind = action.get("type", "element")
    label = action.get("label") or action.get("placeholder") or action.get("tool_name") or kind
    sid = action.get("id", "?")
    return f"{group} > {kind}[{label}] (#{sid})"


def _norm(value: str) -> str:
    return " ".join((value or "").lower().split())


def _append_diff_section(
    parent: etree._Element,
    tag: str,
    entries: List[DiffEntry],
    *,
    include_previous: bool = False,
) -> None:
    section = etree.SubElement(parent, tag, count=str(len(entries)))
    for entry in entries:
        attrs = {
            "id": entry.sid,
            "breadcrumb": entry.breadcrumb,
            "fingerprint": entry.fingerprint,
        }
        action_el = etree.SubElement(section, "action", **attrs)
        for key, value in entry.action.items():
            if value:
                action_el.set(key, str(value))
        if include_previous and entry.previous:
            prev_el = etree.SubElement(action_el, "previous")
            for key, value in entry.previous.items():
                if value:
                    prev_el.set(key, str(value))
