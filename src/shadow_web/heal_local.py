"""Local selector healing without LLM (fuzzy label / stable attribute matching)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from .utils import _parse_eval_res

PageLike = Any
logger = logging.getLogger(__name__)

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
    """Persistent cache: (origin, path, query, fragment, label, type) -> selector."""

    def __init__(self, path: str = CACHE_FILE):
        self.path = path
        self._data: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("cache file root must be an object")
            self._data = loaded
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                "Heal cache %s is unreadable (%s); starting empty. "
                "Quality of local healing may degrade until new entries are saved.",
                self.path,
                exc,
            )
            self._data = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp_path = self.path + f".tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.path)

    @staticmethod
    def cache_key(url: str, label: str, action_type: str) -> str:
        """Scope the key to the full page identity, not just host + path.

        Query and fragment are part of the key because SPA routes and checkout
        wizard steps share a path (``/checkout?step=1#pay`` vs
        ``/checkout?step=2#review``); a selector healed on one step must not be
        replayed on another. Schemeless input has no origin to scope by, so it
        lands in the ``about:blank`` namespace while the path/query/fragment
        keep distinct inputs apart.
        """
        parsed = urlparse(url or "")
        if parsed.netloc:
            namespace = f"{parsed.scheme}://{parsed.netloc}"
        elif parsed.scheme:
            namespace = f"{parsed.scheme}:"
        else:
            namespace = "about:blank"
        path = parsed.path or "/"
        query = re.sub(r"\s+", " ", parsed.query.strip())
        fragment = re.sub(r"\s+", " ", parsed.fragment.strip())
        norm_label = re.sub(r"\s+", " ", (label or "").strip().lower())
        base_tag = action_type.split("[")[0].lower()
        return f"{namespace}|{path}|{query}|{fragment}|{base_tag}|{norm_label}"

    def get(self, url: str, label: str, action_type: str) -> Optional[str]:
        with self._lock:
            return self._data.get(self.cache_key(url, label, action_type))

    def set(self, url: str, label: str, action_type: str, selector: str) -> None:
        with self._lock:
            self._data[self.cache_key(url, label, action_type)] = selector
            self._save()

    def invalidate(self, url: str, label: str, action_type: str) -> None:
        key = self.cache_key(url, label, action_type)
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._save()

    def clear(self) -> None:
        with self._lock:
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
    raw_candidates = _parse_eval_res(page.evaluate(
        _COLLECT_CANDIDATES_SCRIPT,
        {"tag": base_tag, "label": label or ""},
    ))
    if not isinstance(raw_candidates, list):
        raw_candidates = []
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
