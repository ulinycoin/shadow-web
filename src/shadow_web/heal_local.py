"""Local selector healing without LLM (fuzzy label / stable attribute matching)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

PageLike = Any

HEAL_THRESHOLD = 0.85
CACHE_DIR = os.path.expanduser("~/.shadow-web")
CACHE_FILE = os.path.join(CACHE_DIR, "heal_cache.json")

# Returns raw candidates; scoring happens in Python (single source of truth).
_COLLECT_CANDIDATES_SCRIPT = """
({ tag, label }) => {
  const results = [];

  function textOf(el) {
    return (el.textContent || "").replace(/\\s+/g, " ").trim();
  }

  function labelOf(el) {
    return (
      el.getAttribute("aria-label") ||
      el.getAttribute("placeholder") ||
      el.getAttribute("name") ||
      el.getAttribute("alt") ||
      textOf(el)
    );
  }

  function selectorFor(el) {
    if (el.getAttribute("data-testid")) {
      return `[data-testid="${el.getAttribute("data-testid")}"]`;
    }
    if (el.id) {
      return `#${CSS.escape(el.id)}`;
    }
    const name = el.getAttribute("name");
    if (name) {
      return `${el.tagName.toLowerCase()}[name="${name}"]`;
    }
    const aria = el.getAttribute("aria-label");
    if (aria) {
      return `${el.tagName.toLowerCase()}[aria-label="${aria}"]`;
    }
    const cls = (el.getAttribute("class") || "").trim().split(/\\s+/).filter(Boolean)[0];
    if (cls) {
      return `${el.tagName.toLowerCase()}.${cls}`;
    }
    return el.tagName.toLowerCase();
  }

  function walk(root) {
    if (!root) return;
    const stack = [root];
    while (stack.length) {
      const node = stack.pop();
      if (node.nodeType === 1) {
        const el = node;
        const elTag = el.tagName.toLowerCase();
        if (!tag || elTag === tag) {
          results.push({
            selector: selectorFor(el),
            label: labelOf(el),
            has_testid: !!el.getAttribute("data-testid"),
            has_id: !!el.id,
            has_name: !!el.getAttribute("name"),
            has_aria: !!el.getAttribute("aria-label"),
          });
        }
        if (el.shadowRoot) stack.push(el.shadowRoot);
        for (const child of el.children) stack.push(child);
      }
    }
  }

  walk(document.body);
  return results;
}
"""


@dataclass
class HealResult:
    selector: str
    confidence: float
    source: str  # "local" | "cache"


class HealCache:
    """Persistent cache: (domain, label, type) -> selector."""

    def __init__(self, path: str = CACHE_FILE):
        self.path = path
        self._data: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    @staticmethod
    def cache_key(url: str, label: str, action_type: str) -> str:
        domain = urlparse(url).netloc or "local"
        path = urlparse(url).path or "/"
        norm_label = re.sub(r"\s+", " ", (label or "").strip().lower())
        base_tag = action_type.split("[")[0].lower()
        return f"{domain}|{path}|{base_tag}|{norm_label}"

    def get(self, url: str, label: str, action_type: str) -> Optional[str]:
        return self._data.get(self.cache_key(url, label, action_type))

    def set(self, url: str, label: str, action_type: str, selector: str) -> None:
        self._data[self.cache_key(url, label, action_type)] = selector
        self._save()

    def invalidate(self, url: str, label: str, action_type: str) -> None:
        key = self.cache_key(url, label, action_type)
        if key in self._data:
            del self._data[key]
            self._save()

    def clear(self) -> None:
        self._data = {}
        self._save()


def fuzzy_ratio(a: str, b: str) -> float:
    """Shared fuzzy compare (Python + unit tests)."""
    if not a or not b:
        return 0.0
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.92
    return SequenceMatcher(None, a, b).ratio()


def score_candidate(
    label: str,
    candidate_label: str,
    *,
    has_testid: bool = False,
    has_id: bool = False,
    has_name: bool = False,
    has_aria: bool = False,
) -> float:
    """Score a candidate element for local heal."""
    text_score = fuzzy_ratio(label, candidate_label)
    bonus = 0.0
    if has_testid:
        bonus += 0.3
    if has_id:
        bonus += 0.2
    if has_name:
        bonus += 0.2
    if has_aria:
        bonus += 0.25
    return min(1.0, text_score * 0.7 + bonus)


def rank_candidates(label: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Score and sort raw browser candidates."""
    ranked: List[Dict[str, Any]] = []
    for candidate in candidates:
        score = score_candidate(
            label,
            candidate.get("label", ""),
            has_testid=bool(candidate.get("has_testid")),
            has_id=bool(candidate.get("has_id")),
            has_name=bool(candidate.get("has_name")),
            has_aria=bool(candidate.get("has_aria")),
        )
        if score >= 0.35:
            ranked.append({**candidate, "score": score})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:10]


def local_heal(
    page: PageLike,
    url: str,
    label: str,
    action_type: str,
    cache: Optional[HealCache] = None,
    threshold: float = HEAL_THRESHOLD,
    *,
    verify: bool = True,
) -> Optional[HealResult]:
    """
    Attempt to recover a CSS selector locally.

    Returns None if confidence is below ``threshold`` or verification fails.
    """
    cache = cache or HealCache()
    cached = cache.get(url, label, action_type)
    if cached:
        if verify:
            from .verified_heal import verify_selector_on_page

            if not verify_selector_on_page(page, cached, label, action_type):
                cache.invalidate(url, label, action_type)
            else:
                return HealResult(selector=cached, confidence=1.0, source="cache")
        else:
            return HealResult(selector=cached, confidence=1.0, source="cache")

    base_tag = action_type.split("[")[0].lower()
    raw_candidates: List[Dict[str, Any]] = page.evaluate(
        _COLLECT_CANDIDATES_SCRIPT,
        {"tag": base_tag, "label": label or ""},
    )
    candidates = rank_candidates(label or "", raw_candidates)

    if not candidates:
        return None

    if verify:
        from .verified_heal import verify_selector_on_page

        for candidate in candidates:
            confidence = float(candidate.get("score", 0))
            selector = candidate.get("selector")
            if not selector or confidence < threshold:
                continue
            if verify_selector_on_page(page, selector, label, action_type):
                cache.set(url, label, action_type, selector)
                return HealResult(selector=selector, confidence=confidence, source="local")
        return None

    best = candidates[0]
    confidence = float(best.get("score", 0))
    selector = best.get("selector")
    if not selector or confidence < threshold:
        return None

    cache.set(url, label, action_type, selector)
    return HealResult(selector=selector, confidence=confidence, source="local")
