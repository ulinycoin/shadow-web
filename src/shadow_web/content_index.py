"""Content Block Index — tree-aware block outline for on-demand text retrieval.

Builds a flat list of content blocks (h1-h6, p, li, blockquote, pre) with
heading-path ancestry. Excludes interactive/data zones (table, nav, footer,
aside, form). Designed to reduce Wikipedia-style pages from ~16K to ~500
outline tokens.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import html as _html_parser

# Tags that carry readable content for agents
_CONTENT_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "li", "blockquote", "pre",
})

# Tags excluded even if they contain content tags inside
_EXCLUDED_PARENTS = frozenset({
    "nav", "footer", "aside", "form", "table",
})

_HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def _is_excluded(el) -> bool:
    """Check if element sits inside an excluded parent."""
    parent = el.getparent()
    while parent is not None:
        tag = parent.tag if isinstance(parent.tag, str) else ""
        if tag in _EXCLUDED_PARENTS:
            return True
        parent = parent.getparent()
    return False


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return max(1, len(text) // 4)


def build(html_text: str) -> list[dict[str, Any]]:
    """Parse clean HTML and return a flat list of content blocks.

    Each block:
      id           str   — "p0", "p1", …
      tag          str   — element tag name
      heading_path str   — "Architecture" or "Architecture > Pipeline"
      type         str   — "heading" | "paragraph" | "list_item" | "blockquote" | "code"
      text         str   — full text content
      tokens       int   — estimated token count (len/4)
      level        int   — heading level (0 for non-heading)
    """
    root = _html_parser.fromstring(html_text)
    blocks: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []  # [(level, text), ...]
    block_id = 0

    # Walk body only if <body> exists, otherwise whole doc
    body = root.find("body")
    elements = body.iter() if body is not None else root.iter()

    _TYPE_MAP = {
        "p": "paragraph",
        "li": "list_item",
        "blockquote": "blockquote",
        "pre": "code",
    }

    for el in elements:
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag not in _CONTENT_TAGS:
            continue
        if _is_excluded(el):
            continue

        text = _clean_text(el.text_content())
        if not text:
            continue

        # Update heading stack
        if tag in _HEADING_LEVELS:
            level = _HEADING_LEVELS[tag]
            # Pop any headings at same or deeper level
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text))
            block_type = "heading"
        else:
            block_type = _TYPE_MAP.get(tag, "paragraph")

        # Build heading path
        if heading_stack:
            heading_path = " > ".join(h[1] for h in heading_stack)
        else:
            heading_path = "root"

        tokens = _estimate_tokens(text)

        blocks.append({
            "id": f"p{block_id}",
            "tag": tag,
            "heading_path": heading_path,
            "type": block_type,
            "text": text,
            "tokens": tokens,
            "level": _HEADING_LEVELS.get(tag, 0),
        })
        block_id += 1

    return blocks


def outline_text(blocks: list[dict[str, Any]], max_tokens: int = 600) -> str:
    """Build a terse text outline of content blocks, token-limited.

    Format per line:
      p0 | Architecture | heading | 12t | Architecture Overview
      p1 | Architecture | paragraph | 184t | Shadow Web compresses pages before…

    Headings are always included. Paragraphs are included token-first until
    max_tokens is reached (in estimated tokens).
    """
    lines: list[str] = []
    running = 0

    for block in blocks:
        # Estimate line tokens: id + heading_path + type + number + truncated text
        text_snippet = block["text"][:80].replace("\n", " ")
        line_tokens = block["tokens"]
        # Always include headings; for content, stop when over budget
        if block["type"] != "heading" and running + line_tokens > max_tokens:
            continue

        lines.append(
            f'{block["id"]} | {block["heading_path"]} | {block["type"]} | '
            f'{line_tokens}t | {text_snippet}'
        )
        running += line_tokens

    lines.append(f"\nblocks={len(lines)} total_estimated_tokens={running}")
    return "\n".join(lines)


def fetch(
    blocks: list[dict[str, Any]],
    ids: list[str],
    max_tokens: int = 2000,
) -> dict[str, str]:
    """Return full text of requested blocks, bounded by max_tokens total.

    Returns dict mapping block_id → text (truncated at sentence boundary if
    over max_tokens cumulative).
    """
    id_set = set(ids)
    result: dict[str, str] = {}
    running = 0

    for block in blocks:
        if block["id"] not in id_set:
            continue
        text = block["text"]
        tokens = block["tokens"]

        if running + tokens > max_tokens:
            # Truncate at sentence boundary
            remaining = max_tokens - running
            truncated = _truncate_at_sentence(text, remaining * 4)
            result[block["id"]] = truncated + " […truncated]"
            break

        result[block["id"]] = text
        running += tokens

    return result


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate text at the last sentence boundary under max_chars."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # Find last sentence end (.!?) within the truncated range
    for sep in ("! ", "? ", ". "):
        idx = truncated.rfind(sep)
        if idx > max_chars // 2:  # Don't go back more than halfway
            return truncated[: idx + 1]
    # No clean sentence boundary — truncate at word boundary
    idx = truncated.rfind(" ")
    if idx > max_chars // 2:
        return truncated[:idx]
    return truncated
