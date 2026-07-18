"""Rendered Text Index — tree-aware block outline for on-demand retrieval.

Indexes every readable text node exactly once. Semantic elements (headings,
paragraphs, lists, quotes, code) remain strong boundaries; unsemantic text in
div/span-heavy applications is clustered into bounded structural blocks.
Excludes navigation/data zones handled by dedicated tools.
"""

from __future__ import annotations

from collections import Counter
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

# Structural clusters are deliberately bounded. Starting from a text node, the
# owner climbs the DOM while the candidate subtree remains compact. On card
# grids this naturally stops at the product/article card rather than the grid.
_STRUCTURAL_MAX_TOKENS = 120
_STRUCTURAL_MAX_TEXT_NODES = 24
_STRUCTURAL_ROOT_TAGS = frozenset({"html", "body"})

# Price signals for retention diagnostics. Covers major retail currencies —
# symbols and ISO/local codes — without site-specific selectors.
_CURRENCY_SYMBOLS = r"[€$£¥₽]"
_CURRENCY_CODES = (
    r"(?:\b(?:EUR|USD|GBP|RUB|PLN|CZK|SEK|NOK|DKK|UAH|KZT|TRY|руб\.?)\b)"
)
_AMOUNT = r"\d{1,3}(?:[\s\u00a0.,]\d{3})*(?:[.,]\d{1,2})?"
_PRICE_RE = re.compile(
    rf"(?:{_AMOUNT}\s*(?:{_CURRENCY_SYMBOLS}|{_CURRENCY_CODES}))"
    rf"|(?:(?:{_CURRENCY_SYMBOLS}|{_CURRENCY_CODES})\s*{_AMOUNT})",
    re.IGNORECASE,
)


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


def _tag(el) -> str:
    return el.tag if isinstance(el.tag, str) else ""


def _scope(root):
    """Return body when present, otherwise the parsed fragment root."""
    body = root.find("body")
    return body if body is not None else root


def _walk_text_nodes(scope):
    """Yield readable direct text nodes in document order.

    The context element is the element that owns the direct text or child tail.
    Text under semantic blocks is omitted because `_block_text` owns it.
    """
    sequence = 0
    element_order: dict[Any, int] = {}
    chunks: list[tuple[int, Any, str]] = []

    def visit(el, semantic_depth: int = 0, excluded_depth: int = 0) -> None:
        nonlocal sequence
        element_order[el] = sequence
        sequence += 1

        tag = _tag(el)
        excluded = excluded_depth + int(tag in _EXCLUDED_PARENTS)
        semantic = semantic_depth + int(tag in _CONTENT_TAGS)

        if not excluded and not semantic and el.text:
            text = _clean_text(el.text)
            if text:
                chunks.append((sequence, el, text))
                sequence += 1

        for child in el:
            visit(child, semantic, excluded)
            # A child's tail belongs to the current element, not the child.
            if not excluded and not semantic and child.tail:
                text = _clean_text(child.tail)
                if text:
                    chunks.append((sequence, el, text))
                    sequence += 1

    visit(scope)
    return chunks, element_order


def _structural_records(scope) -> tuple[list[dict[str, Any]], dict[Any, int]]:
    """Cluster text not owned by semantic tags into bounded DOM regions."""
    chunks, element_order = _walk_text_nodes(scope)
    if not chunks:
        return [], element_order

    aggregate: dict[Any, dict[str, int]] = {}
    semantic_descendants: dict[Any, int] = {}

    for el in scope.iter():
        if _tag(el) in _CONTENT_TAGS and not _is_excluded(el):
            parent = el.getparent()
            while parent is not None:
                semantic_descendants[parent] = semantic_descendants.get(parent, 0) + 1
                if parent is scope:
                    break
                parent = parent.getparent()

    # Aggregate uncovered text size for every ancestor. This lets ownership
    # climb to a compact card/section without relying on tag names or classes.
    for _, context, text in chunks:
        node = context
        while node is not None:
            stats = aggregate.setdefault(node, {"chars": 0, "nodes": 0})
            stats["chars"] += len(text)
            stats["nodes"] += 1
            if node is scope:
                break
            node = node.getparent()

    grouped: dict[Any, dict[str, Any]] = {}
    for sequence, context, text in chunks:
        owner = context
        parent = owner.getparent()
        while parent is not None:
            tag = _tag(parent)
            stats = aggregate.get(parent, {})
            if tag in _STRUCTURAL_ROOT_TAGS or tag in _EXCLUDED_PARENTS:
                break
            if tag in _CONTENT_TAGS or semantic_descendants.get(parent, 0):
                break
            if stats.get("nodes", 0) > _STRUCTURAL_MAX_TEXT_NODES:
                break
            if stats.get("chars", 0) // 4 > _STRUCTURAL_MAX_TOKENS:
                break
            owner = parent
            if owner is scope:
                break
            parent = owner.getparent()

        record = grouped.setdefault(
            owner,
            {
                "order": sequence,
                "tag": _tag(owner) or "text",
                "type": "text_group",
                "level": 0,
                "parts": [],
            },
        )
        record["order"] = min(record["order"], sequence)
        record["parts"].append(text)

    records: list[dict[str, Any]] = []
    for owner, record in grouped.items():
        text = _clean_text(" ".join(record.pop("parts")))
        if not text:
            continue
        record["order"] = min(record["order"], element_order.get(owner, record["order"]))
        record["text"] = text
        record["tokens"] = _estimate_tokens(text)
        records.append(record)
    return records, element_order


def build(html_text: str) -> list[dict[str, Any]]:
    """Parse clean HTML and return semantic plus structural text blocks.

    Each block:
      id           str   — "p0", "p1", …
      tag          str   — element tag name
      heading_path str   — "Architecture" or "Architecture > Pipeline"
      type         str   — semantic type or "text_group"
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
    scope = _scope(root)
    records, element_order = _structural_records(scope)

    _TYPE_MAP = {
        "p": "paragraph",
        "li": "list_item",
        "blockquote": "blockquote",
        "pre": "code",
    }

    for el in scope.iter():
        tag = _tag(el)
        if tag not in _CONTENT_TAGS:
            continue
        if _is_excluded(el):
            continue

        text = _block_text(el)
        if not text:
            continue

        # Heading paths are assigned after structural and semantic records are
        # merged into document order.
        if tag in _HEADING_LEVELS:
            level = _HEADING_LEVELS[tag]
            block_type = "heading"
        else:
            level = 0
            block_type = _TYPE_MAP.get(tag, "paragraph")

        tokens = _estimate_tokens(text)

        records.append({
            "order": element_order.get(el, 0),
            "tag": tag,
            "type": block_type,
            "text": text,
            "tokens": tokens,
            "level": level,
            "_has_link": tag == "li" and bool(el.xpath(".//a[@href]")),
        })

    records.sort(key=lambda item: item["order"])
    blocks: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []
    for record in records:
        record.pop("order", None)
        if record["type"] == "heading":
            level = int(record["level"])
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, str(record["text"])))
        record["heading_path"] = (
            " > ".join(text for _, text in heading_stack)
            if heading_stack
            else "root"
        )
        record.setdefault("_has_link", False)
        blocks.append(record)

    # ── Boilerplate nav removal ──────────────────────────────────────
    # Runs of ≥5 consecutive linked <li> blocks each ≤10 tokens, before
    # the first real content paragraph (p >15t) or h2, are almost certainly
    # a language switcher / breadcrumb / mobile nav sidebar. Requiring links
    # preserves short data lists such as ingredients and product attributes.
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
                and b["_has_link"]
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

    blocks = [b for b in blocks if not b.get("_boilerplate")]
    for i, b in enumerate(blocks):
        b["id"] = f"p{i}"
        b.pop("_has_link", None)
        b.pop("_boilerplate", None)

    return blocks


def quality(html_text: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure text coverage, duplication, and price-signal retention."""
    if not html_text or not html_text.strip():
        return {
            "source_tokens": 0,
            "indexed_tokens": 0,
            "coverage_pct": 100.0,
            "duplicate_overhead_pct": 0.0,
            "source_price_signals": 0,
            "indexed_price_signals": 0,
            "signal_retention_pct": 100.0,
            "structural_blocks": 0,
            "card_blocks": 0,
            "mode": "semantic",
        }
    try:
        root = _html_parser.fromstring(html_text)
    except (etree.ParserError, ValueError):
        root = None

    source_parts: list[str] = []
    if root is not None:
        scope = _scope(root)
        for el in scope.iter():
            if _tag(el) in _EXCLUDED_PARENTS or _is_excluded(el):
                continue
            if el.text:
                text = _clean_text(el.text)
                if text:
                    source_parts.append(text)
            for child in el:
                if child.tail:
                    text = _clean_text(child.tail)
                    if text:
                        source_parts.append(text)

    source_text = " ".join(source_parts)
    indexed_text = " ".join(str(block.get("text", "")) for block in blocks)
    source_chars = sum(len(part) for part in source_parts)
    indexed_chars = sum(len(str(block.get("text", ""))) for block in blocks)
    source_prices = len(_PRICE_RE.findall(source_text))
    indexed_prices = len(_PRICE_RE.findall(indexed_text))
    structural_blocks = sum(block.get("type") == "text_group" for block in blocks)
    source_terms = Counter(re.findall(r"\S+", source_text.casefold()))
    indexed_terms = Counter(re.findall(r"\S+", indexed_text.casefold()))
    duplicate_terms = sum((indexed_terms - source_terms).values())
    indexed_term_count = sum(indexed_terms.values())

    return {
        "source_tokens": _estimate_tokens(source_text) if source_text else 0,
        "indexed_tokens": sum(int(block.get("tokens", 0)) for block in blocks),
        "coverage_pct": round(
            min(100.0, 100 * indexed_chars / max(1, source_chars)), 1
        ),
        "duplicate_overhead_pct": round(
            100 * duplicate_terms / max(1, indexed_term_count), 1
        ),
        "source_price_signals": source_prices,
        "indexed_price_signals": indexed_prices,
        "signal_retention_pct": round(
            100 * indexed_prices / max(1, source_prices), 1
        ) if source_prices else 100.0,
        "structural_blocks": structural_blocks,
        "card_blocks": sum(1 for block in blocks if _is_card_block(block)),
        "mode": "hybrid" if structural_blocks else "semantic",
    }


_CATALOG_PRICE_BLOCK_THRESHOLD = 5
_FEED_ITEM_THRESHOLD = 5
_FEED_TOKEN_MIN = 12
_FEED_TOKEN_MAX = 140
# Bucket by ~20-token bands so near-equal feed cards cluster together.
_FEED_TOKEN_BUCKET = 20
_ENGAGEMENT_RE = re.compile(
    r"\b(?:"
    r"like|likes|comment|comments|share|shares|retweet|reply|replies|"
    r"follow|followers|views|votes|subscribe|подпис|лайк|коммент|репост|"
    r"просмотр"
    r")\b",
    re.IGNORECASE,
)


def _has_price(text: str) -> bool:
    return bool(_PRICE_RE.search(text or ""))


def _is_card_block(block: dict[str, Any]) -> bool:
    """Compact priced block — the unit agents need from catalog pages."""
    text = str(block.get("text", ""))
    tokens = int(block.get("tokens", 0))
    if not _has_price(text):
        return False
    if block.get("type") == "heading":
        return False
    return 4 <= tokens <= 160


def _is_feed_item_block(block: dict[str, Any]) -> bool:
    """Mid-size repeating unit without requiring a price signal.

    Used for social/news feeds where cards are titles + meta, not €/$ tags.
    Excludes headings and very short engagement chrome.
    """
    if block.get("type") == "heading":
        return False
    tokens = int(block.get("tokens", 0))
    if tokens < _FEED_TOKEN_MIN or tokens > _FEED_TOKEN_MAX:
        return False
    text = str(block.get("text", ""))
    # Pure engagement counters are not feed items.
    if tokens <= 18 and _ENGAGEMENT_RE.search(text) and not _has_price(text):
        return False
    return True


def _feed_token_bucket(tokens: int) -> int:
    return int(tokens) // _FEED_TOKEN_BUCKET


def _detect_feed_mode(blocks: list[dict[str, Any]]) -> tuple[bool, set[int]]:
    """Find a dominant mid-size token cluster of repeating items.

    Returns (feed_mode, set of original indices that are in the dominant cluster).
    Catalog pages (priced cards) take precedence elsewhere — callers should
    skip this when catalog mode is active.
    """
    candidates: list[tuple[int, int]] = []
    for idx, block in enumerate(blocks):
        if not _is_feed_item_block(block):
            continue
        tokens = int(block.get("tokens", 0))
        candidates.append((idx, _feed_token_bucket(tokens)))
    if len(candidates) < _FEED_ITEM_THRESHOLD:
        return False, set()

    bucket_counts = Counter(bucket for _, bucket in candidates)
    dominant_bucket, dominant_count = bucket_counts.most_common(1)[0]
    if dominant_count < _FEED_ITEM_THRESHOLD:
        return False, set()
    # Require the cluster to be a meaningful share of mid-size blocks.
    if dominant_count < max(_FEED_ITEM_THRESHOLD, len(candidates) // 3):
        return False, set()
    feed_ids = {idx for idx, bucket in candidates if bucket == dominant_bucket}
    return True, feed_ids


def _outline_rank_score(
    block: dict[str, Any],
    *,
    catalog: bool,
    feed: bool,
    is_feed_item: bool,
) -> int:
    """Higher score → earlier in the token-budgeted outline.

    Catalog mode (many priced blocks) promotes product cards and demotes
    filters/nav chrome. Feed mode (repeating mid-size items, no prices)
    promotes those items. Article mode keeps document order via near-equal
    scores and stable original indices.
    """
    text = str(block.get("text", ""))
    tokens = int(block.get("tokens", 0))
    btype = str(block.get("type", ""))
    level = int(block.get("level", 0) or 0)
    priced = _has_price(text)
    card = _is_card_block(block)
    score = 0

    if btype == "heading":
        score += 40 if level <= 1 else 25 if level == 2 else 10

    if card:
        score += 120
        if 8 <= tokens <= 100:
            score += 30
    elif priced:
        score += 70
        if tokens <= 200:
            score += 15

    if catalog:
        if card:
            score += 80
        elif priced:
            score += 40
        elif btype == "heading" and level <= 2:
            score += 20
        else:
            # Filters, size charts, and chrome drown the first page otherwise.
            score -= 40
            if tokens <= 6:
                score -= 20
            if tokens > 180:
                score -= 25
    elif feed:
        if is_feed_item:
            score += 140
            if 20 <= tokens <= 100:
                score += 25
        elif btype == "heading" and level <= 2:
            score += 30
        else:
            score -= 35
            if tokens <= 8:
                score -= 25
            if _ENGAGEMENT_RE.search(text) and tokens <= 24:
                score -= 30
    else:
        # Articles: prefer natural reading order; light boosts only.
        if btype in {"paragraph", "list_item", "blockquote", "code"}:
            score += 5

    return score


def _ranked_blocks_for_outline(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    price_blocks = sum(1 for block in blocks if _has_price(str(block.get("text", ""))))
    catalog = price_blocks >= _CATALOG_PRICE_BLOCK_THRESHOLD
    feed = False
    feed_indices: set[int] = set()
    if not catalog:
        feed, feed_indices = _detect_feed_mode(blocks)

    ranked = sorted(
        enumerate(blocks),
        key=lambda item: (
            -_outline_rank_score(
                item[1],
                catalog=catalog,
                feed=feed,
                is_feed_item=item[0] in feed_indices,
            ),
            item[0],
        ),
    )
    meta = {
        "catalog": catalog,
        "feed": feed,
        "feed_count": len(feed_indices),
        "feed_indices": feed_indices,
    }
    return [block for _, block in ranked], meta


def outline_text(
    blocks: list[dict[str, Any]],
    max_tokens: int = 600,
    offset: int = 0,
    quality_data: dict[str, Any] | None = None,
) -> str:
    """Build a terse text outline of content blocks, token-limited.

    Format per line:
      p0 | Architecture | heading | 12t | Architecture Overview
      p1 | Architecture | paragraph | 184t | Shadow Web compresses pages before…

    On catalog-like pages (many priced blocks), product cards are ranked above
    chrome so the first budgeted page surfaces buyable items. On feed-like
    pages (repeating mid-size items without prices), feed items are ranked
    above engagement chrome. Block IDs stay stable for content_blocks();
    offset walks the ranked outline order.

    The budget applies to the actual outline text, not to source block sizes.
    Large blocks still receive an outline line so agents can fetch them.
    """
    if max_tokens <= 0:
        return ""

    ordered, rank_meta = _ranked_blocks_for_outline(blocks)
    start = max(0, offset)
    lines: list[str] = []
    source_tokens = sum(int(block.get("tokens", 0)) for block in blocks)
    card_count = sum(1 for block in blocks if _is_card_block(block))
    feed_indices = rank_meta.get("feed_indices") or set()
    # Map block id → whether it was in the dominant feed cluster (by identity).
    feed_block_ids = {
        blocks[i]["id"] for i in feed_indices if 0 <= i < len(blocks)
    }
    feed_count = int(rank_meta.get("feed_count") or 0)
    for block in ordered[start:]:
        heading_path = str(block["heading_path"])[:120]
        if _is_card_block(block):
            display_type = "card"
        elif block["id"] in feed_block_ids:
            display_type = "feed"
        else:
            display_type = block["type"]
        if block["type"] == "heading":
            line = (
                f'{block["id"]} | {block["tag"]} | {heading_path} | '
                f'{block["tokens"]}t'
            )
        else:
            snippet = str(block["text"])[:80].replace("\n", " ")
            line = (
                f'{block["id"]} | {heading_path} | {display_type} | '
                f'{block["tokens"]}t | {snippet}'
            )

        candidate_lines = [*lines, line]
        end = start + len(candidate_lines)
        next_offset = end if end < len(ordered) else None
        summary = _summary_line(
            start,
            end,
            len(ordered),
            source_tokens,
            next_offset,
            quality_data,
            card_count=card_count,
            feed_count=feed_count,
        )
        candidate = "\n".join([*candidate_lines, summary])
        if _estimate_tokens(candidate) > max_tokens:
            break
        lines.append(line)

    end = start + len(lines)
    next_offset = end if end < len(ordered) else None
    summary = _summary_line(
        start,
        end,
        len(ordered),
        source_tokens,
        next_offset,
        quality_data,
        card_count=card_count,
        feed_count=feed_count,
    )
    output = "\n".join([*lines, summary])
    if _estimate_tokens(output) <= max_tokens:
        return output
    return output[: max_tokens * 4]


def _summary_line(
    start: int,
    end: int,
    total: int,
    source_tokens: int,
    next_offset: int | None,
    quality_data: dict[str, Any] | None,
    card_count: int = 0,
    feed_count: int = 0,
) -> str:
    summary = f"range={start}:{end}/{total} source={source_tokens}t"
    if next_offset is not None:
        summary += f" next={next_offset}"
    if card_count:
        summary += f" cards={card_count}"
    if feed_count:
        summary += f" feeds={feed_count}"
    if quality_data:
        summary += (
            f" coverage={quality_data.get('coverage_pct', 0)}%"
            f" mode={quality_data.get('mode', 'semantic')}"
            f" signals={quality_data.get('indexed_price_signals', 0)}"
            f"/{quality_data.get('source_price_signals', 0)}"
        )
    return summary


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
