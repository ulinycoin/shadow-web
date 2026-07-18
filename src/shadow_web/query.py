"""shadow_grep — filter Action Map elements before sending to an LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from lxml import etree

INTENT_PATTERNS = {
    "login": re.compile(r"login|log\s*in|sign\s*in|signin|password|username", re.I),
    "search": re.compile(r"search|query|find", re.I),
    "checkout": re.compile(r"checkout|payment|billing|cart", re.I),
    "fill_form": re.compile(r"form|submit|subscribe|register|sign\s*up", re.I),
    "navigate": re.compile(r"nav|menu|home|back|forgot", re.I),
    "read": re.compile(r"read|article|details|learn\s*more|more\s*info", re.I),
    "extract": re.compile(r"export|download|copy|print", re.I),
    "buy": re.compile(r"buy|add\s*to\s*cart|purchase", re.I),
}

FilterKind = Literal[
    "type",
    "group",
    "intent",
    "id",
    "label",
    "placeholder",
    "href",
    "text",
]


@dataclass
class QueryFilter:
    kind: FilterKind
    value: str
    regex: Optional[re.Pattern[str]] = None


@dataclass
class QueryResult:
    """Result of a shadow_grep query."""

    query: str
    matches: List[Dict[str, Any]] = field(default_factory=list)
    total: int = 0

    @property
    def count(self) -> int:
        return len(self.matches)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "matches": self.matches,
            "count": self.count,
            "total": self.total,
        }

    def terse(self) -> str:
        """Compact text for LLM context (~one line per match)."""
        if not self.matches:
            return f"# shadow_grep: {self.query}\n(no matches)"
        lines = [f"# shadow_grep: {self.query} ({self.count}/{self.total})"]
        for action in self.matches:
            sid = action.get("id", "?")
            kind = action.get("type", "?")
            label = action.get("label") or action.get("placeholder") or ""
            group = action.get("group", "")
            suffix = f" [{group}]" if group else ""
            lines.append(f"@{sid} {kind} {label}{suffix}".strip())
        return "\n".join(lines)

    def xml(self, url: str = "", title: str = "") -> str:
        """Grouped XML subset for matched actions only."""
        if not self.matches:
            root = etree.Element("page", url=url or "about:blank", title=title or "Query")
            etree.SubElement(root, "query", text=self.query, count="0")
            return etree.tostring(root, encoding="unicode", pretty_print=True)

        buckets: Dict[str, List[Dict[str, Any]]] = {}
        order: List[str] = []
        for action in self.matches:
            group_name = action.get("group") or "Page"
            if group_name not in buckets:
                buckets[group_name] = []
                order.append(group_name)
            buckets[group_name].append(action)

        root = etree.Element("page", url=url or "about:blank", title=title or "Query")
        etree.SubElement(root, "query", text=self.query, count=str(self.count))
        for group_name in order:
            group_el = etree.SubElement(root, "group", name=group_name)
            for action in buckets[group_name]:
                action_el = etree.SubElement(group_el, "action")
                for key, val in action.items():
                    action_el.set(key, str(val))
        return etree.tostring(root, encoding="unicode", pretty_print=True)


def shadow_grep(
    actions: List[Dict[str, Any]],
    query: str,
    *,
    groups: Optional[List[Dict[str, Any]]] = None,
    url: str = "",
    title: str = "",
) -> QueryResult:
    """
    Filter actions with shadow_grep query language (AND semantics).

    Examples:
        ``type:button``
        ``intent:login``
        ``group:Login Form``
        ``label~/checkout/i``
        ``type:button; intent:login``
        ``type:input intent:login``
        ``id:1,3,5``
    """
    query = (query or "").strip()
    total = len(actions)
    if not query:
        return QueryResult(query=query, matches=list(actions), total=total)

    filters = parse_query(query)
    matched = list(actions)
    for flt in filters:
        matched = _apply_filter(matched, flt, groups=groups)

    return QueryResult(query=query, matches=matched, total=total)


def query_actions(
    actions: List[Dict[str, Any]],
    query: str,
    *,
    groups: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Backward-compatible helper returning only the match list."""
    return shadow_grep(actions, query, groups=groups).matches


def parse_query(query: str) -> List[QueryFilter]:
    """Parse a shadow_grep query string into AND filters."""
    tokens = _tokenize_query(query)
    filters: List[QueryFilter] = []
    for token in tokens:
        if token in (";", "&&"):
            continue
        flt = _parse_token(token)
        if flt:
            filters.append(flt)
    return filters


def _tokenize_query(query: str) -> List[str]:
    tokens: List[str] = []
    for segment in query.split(";"):
        remainder = segment.strip()
        while remainder:
            matched = _match_next_clause(remainder)
            if not matched:
                break
            token, consumed = matched
            tokens.append(token)
            remainder = remainder[consumed:].lstrip()
    return tokens


def _match_next_clause(segment: str) -> Optional[tuple[str, int]]:
    for prefix in ("label~", "placeholder~"):
        if segment.startswith(prefix):
            body = segment[len(prefix) :]
            if body.startswith("/"):
                end = body.rfind("/")
                if end <= 0:
                    return segment.strip(), len(segment)
                flag_i = end + 1
                while flag_i < len(body) and body[flag_i] in "ims":
                    flag_i += 1
                return segment[: len(prefix) + flag_i], len(prefix) + flag_i
            m = re.match(r"\S+", body)
            if m:
                return segment[: len(prefix) + m.end()], len(prefix) + m.end()
            return segment, len(segment)

    m = re.match(
        r'group:"([^"]*)"'
        r'|group:(.+?)(?=\s+(?:type:|group:|intent:|id:|label~|placeholder~|href:)|$)'
        r'|type:\S+'
        r'|intent:\w+'
        r'|id:[\d,]+'
        r'|href:\S+'
        r'|\S+',
        segment,
        re.I | re.S,
    )
    if not m:
        return None
    return m.group(0).strip(), m.end()


def _parse_token(token: str) -> Optional[QueryFilter]:
    token = token.strip()
    if not token:
        return None

    if token.startswith("type:"):
        return QueryFilter("type", token.split(":", 1)[1].strip().lower())

    if token.startswith("group:"):
        value = token.split(":", 1)[1].strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        return QueryFilter("group", value.lower())

    if token.startswith("intent:"):
        return QueryFilter("intent", token.split(":", 1)[1].strip().lower())

    if token.startswith("id:"):
        return QueryFilter("id", token.split(":", 1)[1].strip())

    if token.startswith("href:"):
        return QueryFilter("href", token.split(":", 1)[1].strip().lower())

    if token.startswith("label~"):
        return _regex_filter("label", token[len("label~") :])

    if token.startswith("placeholder~"):
        return _regex_filter("placeholder", token[len("placeholder~") :])

    return QueryFilter("text", token.lower())


def _regex_filter(kind: FilterKind, body: str) -> QueryFilter:
    body = body.strip()
    if body.startswith("/"):
        end = body.rfind("/")
        if end <= 0:
            return QueryFilter(kind, body.lower())
        pattern = body[1:end]
        flags = body[end + 1 :] or "i"
        return QueryFilter(kind, pattern, regex=re.compile(pattern, _regex_flags(flags)))
    return QueryFilter(kind, body.lower())


def _apply_filter(
    actions: List[Dict[str, Any]],
    flt: QueryFilter,
    *,
    groups: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    if flt.kind == "type":
        return [a for a in actions if a.get("type", "").lower().startswith(flt.value)]

    if flt.kind == "group":
        return [a for a in actions if a.get("group", "").lower() == flt.value]

    if flt.kind == "intent":
        pattern = INTENT_PATTERNS.get(flt.value)
        if not pattern:
            return []
        return [a for a in actions if pattern.search(_action_text(a))]

    if flt.kind == "id":
        ids = {x.strip() for x in flt.value.split(",") if x.strip()}
        return [a for a in actions if a.get("id") in ids]

    if flt.kind == "href":
        return [a for a in actions if flt.value in a.get("href", "").lower()]

    if flt.kind == "label":
        if flt.regex:
            return [a for a in actions if flt.regex.search(a.get("label", ""))]
        return [a for a in actions if flt.value in a.get("label", "").lower()]

    if flt.kind == "placeholder":
        if flt.regex:
            return [a for a in actions if flt.regex.search(a.get("placeholder", ""))]
        return [a for a in actions if flt.value in a.get("placeholder", "").lower()]

    if flt.kind == "text":
        if groups and flt.value:
            group_hits: List[Dict[str, Any]] = []
            for block in groups:
                if flt.value in block.get("name", "").lower():
                    group_hits.extend(block.get("elements", []))
            if group_hits:
                allowed = {a.get("id") for a in group_hits}
                return [a for a in actions if a.get("id") in allowed]
        return [a for a in actions if flt.value in _action_text(a).lower()]

    return actions


def _action_text(action: Dict[str, Any]) -> str:
    parts = [
        action.get("label", ""),
        action.get("placeholder", ""),
        action.get("group", ""),
        action.get("type", ""),
        action.get("href", ""),
    ]
    return " ".join(p for p in parts if p)


def _regex_flags(raw: str) -> int:
    flags = 0
    if "i" in raw:
        flags |= re.I
    if "m" in raw:
        flags |= re.M
    if "s" in raw:
        flags |= re.S
    return flags
