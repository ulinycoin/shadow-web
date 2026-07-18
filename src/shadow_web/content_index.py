"""Content Block Index — tree-aware block outline for on-demand text retrieval.

Builds a flat list of content blocks (h1-h6, p, li, blockquote, pre) with
heading-path ancestry. Excludes interactive/data zones (table, nav, footer,
aside, form). Designed to reduce Wikipedia-style pages from ~16K to ~500
outline tokens.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree
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


# Max tokens for a single boilerplate list-item before the first h1.
# Language links, breadcrumbs, and mobile nav are typically very short.
_BOILERPLATE_MAX_LI_TOKENS = 10
_BOILERPLATE_MIN_RUN = 5


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return max(1, len(text) // 4)


def _block_text(el) -> str:
    """Read an element without duplicating nested content blocks."""
    parts: list[str] = []

    def collect(node) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = child.tag if isinstance(child.tag, str) else ""
            if tag not in _CONTENT_TAGS:
                collect(child)
            if child.tail:
                parts.append(child.tail)

    collect(el)
    return _clean_text(" ".join(parts))


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
    if not html_text or not html_text.strip():
        return []
    try:
        root = _html_parser.fromstring(html_text)
    except (etree.ParserError, ValueError):
        return []
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

        text = _block_text(el)
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

    # ── Boilerplate nav removal ──────────────────────────────────────
    # Runs of ≥5 consecutive <li> blocks each ≤10 tokens, before the
    # first real content paragraph (p >15t) or h2, are almost certainly
    # a language switcher / breadcrumb / mobile nav sidebar.
    # This catches patterns both before and immediately after an h1.
    first_content = None
    for idx, b in enumerate(blocks):
        if b["tag"] == "h2":
            first_content = idx
            break
        if b["tag"] == "p" and b["tokens"] > 15:
            first_content = idx
            break
    if first_content is not None and first_content > 0:
        pre = blocks[:first_content]
        run_start = None
        for idx, b in enumerate(pre):
            is_short_li = (
                b["tag"] == "li"
                and b["type"] == "list_item"
                and b["tokens"] <= _BOILERPLATE_MAX_LI_TOKENS
            )
            if is_short_li:
                if run_start is None:
                    run_start = idx
            else:
                if run_start is not None and (idx - run_start) >= _BOILERPLATE_MIN_RUN:
                    for ri in range(run_start, idx):
                        blocks[ri]["_boilerplate"] = True
                run_start = None
        if (
            run_start is not None
            and (len(pre) - run_start) >= _BOILERPLATE_MIN_RUN
        ):
            for ri in range(run_start, len(pre)):
                blocks[ri]["_boilerplate"] = True

    if any(b.get("_boilerplate") for b in blocks):
        blocks = [b for b in blocks if not b.get("_boilerplate")]
        # Re-number IDs
        for i, b in enumerate(blocks):
            b["id"] = f"p{i}"

    return blocks


def outline_text(
    blocks: list[dict[str, Any]],
    max_tokens: int = 600,
    offset: int = 0,
) -> str:
    """Build a terse text outline of content blocks, token-limited.

    Format per line:
      p0 | Architecture | heading | 12t | Architecture Overview
      p1 | Architecture | paragraph | 184t | Shadow Web compresses pages before…

    The budget applies to the actual outline text, not to source block sizes.
    Large blocks still receive an outline line so agents can fetch them.
    """
    if max_tokens <= 0:
        return ""

    start = max(0, offset)
    lines: list[str] = []
    source_tokens = sum(int(block.get("tokens", 0)) for block in blocks)
    for block in blocks[start:]:
        heading_path = str(block["heading_path"])[:120]
        if block["type"] == "heading":
            line = (
                f'{block["id"]} | {block["tag"]} | {heading_path} | '
                f'{block["tokens"]}t'
            )
        else:
            snippet = str(block["text"])[:80].replace("\n", " ")
            line = (
                f'{block["id"]} | {heading_path} | {block["type"]} | '
                f'{block["tokens"]}t | {snippet}'
            )

        candidate_lines = [*lines, line]
        end = start + len(candidate_lines)
        next_offset = end if end < len(blocks) else None
        summary = (
            f"range={start}:{end}/{len(blocks)} source={source_tokens}t"
            + (f" next={next_offset}" if next_offset is not None else "")
        )
        candidate = "\n".join([*candidate_lines, summary])
        if _estimate_tokens(candidate) > max_tokens:
            break
        lines.append(line)

    end = start + len(lines)
    next_offset = end if end < len(blocks) else None
    summary = (
        f"range={start}:{end}/{len(blocks)} source={source_tokens}t"
        + (f" next={next_offset}" if next_offset is not None else "")
    )
    output = "\n".join([*lines, summary])
    if _estimate_tokens(output) <= max_tokens:
        return output
    return output[: max_tokens * 4]


def fetch(
    blocks: list[dict[str, Any]],
    ids: list[str],
    max_tokens: int = 2000,
) -> dict[str, str]:
    """Return full text of requested blocks, bounded by max_tokens total.

    Returns dict mapping block_id → text (truncated at sentence boundary if
    over max_tokens cumulative).
    """
    if max_tokens <= 0:
        return {}

    by_id = {block["id"]: block for block in blocks}
    result: dict[str, str] = {}
    running = 0

    for block_id in dict.fromkeys(ids):
        block = by_id.get(block_id)
        if not block:
            continue
        text = block["text"]
        tokens = block["tokens"]

        if running + tokens > max_tokens:
            remaining = max_tokens - running
            marker = " […truncated]"
            max_chars = max(0, remaining * 4 - len(marker))
            truncated = _truncate_at_sentence(text, max_chars)
            if truncated:
                result[block_id] = truncated + marker
            break

        result[block_id] = text
        running += tokens

    return result


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate text at the last sentence boundary under max_chars."""
    if max_chars <= 0:
        return ""
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
