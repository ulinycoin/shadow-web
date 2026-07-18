"""Universal capture readiness: consent dismiss + wait-for-content.

No site-specific selectors. Consent matches role/text/aria across languages.
Readiness waits for visible text growth and repeating sibling clusters
(card-like layouts) before the first DOM capture.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_MS = 8000
_DEFAULT_POLL_MS = 400
_MIN_READY_TEXT = 800
_MIN_CARD_TEXT = 400
_MIN_CARD_CANDIDATES = 4
_SHELL_TEXT_CHARS = 250

_DISMISS_CONSENT_JS = r"""
() => {
  const ACCEPT = [
    "accept all", "accept all cookies", "allow all", "allow cookies",
    "accept cookies", "i agree", "i accept", "agree and continue", "got it",
    "agree", "accept", "allow", "consent", "continue",
    "alle akzeptieren", "alles akzeptieren", "akzeptieren und weiter",
    "akzeptieren", "zustimmen", "einverstanden", "alle zulassen",
    "tout accepter", "accepter tout", "accepter", "j'accepte",
    "aceptar todo", "aceptar todas", "aceptar", "acepto",
    "принять все", "принять всё", "согласен", "согласна", "разрешить",
    "принять", "соглашаюсь",
    "accetta tutto", "accetta tutti", "accetta", "accetto",
    "aceitar todos", "aceitar tudo", "aceitar", "concordo",
    "zaakceptuj wszystkie", "zaakceptuj", "akceptuję",
    "accepteren", "alles toestaan",
  ];
  const REJECT = [
    "reject", "decline", "deny", "refuse", "necessary only",
    "ablehnen", "nur erforderliche", "refus", "rechazar",
    "отклонить", "только необходимые", "rifiuta", "recusar",
  ];

  const normalize = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();

  function collectRoots(root, out = []) {
    out.push(root);
    try {
      root.querySelectorAll("*").forEach((el) => {
        if (el.shadowRoot) collectRoots(el.shadowRoot, out);
      });
    } catch (_) {}
    return out;
  }

  function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity || "1") === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 2 && rect.height > 2;
  }

  function labelOf(el) {
    return normalize(
      el.getAttribute("aria-label") ||
      el.getAttribute("value") ||
      el.innerText ||
      el.textContent ||
      ""
    );
  }

  function score(label) {
    if (!label || label.length > 80) return 0;
    if (REJECT.some((r) => label.includes(r))) return 0;
    let best = 0;
    for (const phrase of ACCEPT) {
      if (label === phrase) best = Math.max(best, 100 + phrase.length);
      else if (label.includes(phrase)) best = Math.max(best, 50 + phrase.length);
    }
    return best;
  }

  const candidates = [];
  for (const root of collectRoots(document)) {
    const nodes = root.querySelectorAll(
      "button, [role='button'], input[type='button'], input[type='submit'], a"
    );
    for (const el of nodes) {
      if (!isVisible(el)) continue;
      const label = labelOf(el);
      const s = score(label);
      if (s > 0) candidates.push({ el, label, s });
    }
  }
  candidates.sort((a, b) => b.s - a.s);
  if (!candidates.length) {
    return { dismissed: false, label: null, method: "none" };
  }
  const top = candidates[0];
  try {
    top.el.click();
    return { dismissed: true, label: top.label, method: "click" };
  } catch (err) {
    return { dismissed: false, label: top.label, method: "error:" + String(err) };
  }
}
"""

_PROBE_JS = r"""
() => {
  const body = document.body;
  if (!body) {
    return {
      has_body: false,
      text_chars: 0,
      html_chars: document.documentElement
        ? document.documentElement.outerHTML.length
        : 0,
      card_candidates: 0,
      price_hits: 0,
      title: document.title || "",
    };
  }
  const text = (body.innerText || "").replace(/\s+/g, " ").trim();
  const PRICE = /(?:€|\$|£|¥|₽)\s?\d|(?:\d[\d.,\s]{0,12}\s?(?:€|\$|£|¥|₽|eur|usd|gbp|rub|руб))/gi;
  const price_hits = (text.match(PRICE) || []).length;

  let card_candidates = 0;
  const parents = body.querySelectorAll("div, ul, ol, section, main, [role='list']");
  for (const parent of parents) {
    const kids = Array.from(parent.children).filter((child) => {
      const t = (child.innerText || "").replace(/\s+/g, " ").trim();
      return t.length >= 16 && child.children.length > 0;
    });
    if (kids.length < 4) continue;
    const tags = kids.map((k) => k.tagName);
    const counts = {};
    for (const tag of tags) counts[tag] = (counts[tag] || 0) + 1;
    let modeCount = 0;
    for (const n of Object.values(counts)) modeCount = Math.max(modeCount, n);
    if (modeCount >= 4 && modeCount / tags.length >= 0.6) {
      card_candidates = Math.max(card_candidates, modeCount);
    }
  }

  const lower = text.toLowerCase();
  const cookiey = /cookie|privacy|consent|we use cookies|datenschutz|akzeptieren|alle akzeptieren/.test(lower);
  const contentish = price_hits > 0 || card_candidates > 0 || text.length >= 2000;
  return {
    has_body: true,
    text_chars: text.length,
    html_chars: document.documentElement.outerHTML.length,
    card_candidates,
    price_hits,
    title: document.title || "",
    consent_only: cookiey && !contentish,
  };
}
"""


class PageLike(Protocol):
    def evaluate(self, expression: str, arg: Any = None) -> Any: ...
    def wait_for_timeout(self, timeout: float) -> None: ...


class AsyncPageLike(Protocol):
    async def evaluate(self, expression: str, arg: Any = None) -> Any: ...
    async def wait_for_timeout(self, timeout: float) -> None: ...


@dataclass
class CaptureReadyResult:
    ready: bool
    shell: bool
    consent_dismissed: bool
    consent_label: Optional[str]
    text_chars: int
    html_chars: int
    card_candidates: int
    price_hits: int
    wait_ms: int
    reason: str
    has_body: bool = True

    def as_stats(self) -> dict[str, Any]:
        return asdict(self)


def _parse_probe(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "has_body": False,
            "text_chars": 0,
            "html_chars": 0,
            "card_candidates": 0,
            "price_hits": 0,
            "title": "",
        }
    return {
        "has_body": bool(raw.get("has_body", True)),
        "text_chars": int(raw.get("text_chars") or 0),
        "html_chars": int(raw.get("html_chars") or 0),
        "card_candidates": int(raw.get("card_candidates") or 0),
        "price_hits": int(raw.get("price_hits") or 0),
        "title": str(raw.get("title") or ""),
        "consent_only": bool(raw.get("consent_only")),
    }


def is_probe_ready(probe: dict[str, Any], previous: Optional[dict[str, Any]] = None) -> bool:
    """Pure readiness check — unit-testable without a browser."""
    if not probe.get("has_body"):
        return False
    text = int(probe.get("text_chars") or 0)
    cards = int(probe.get("card_candidates") or 0)
    prices = int(probe.get("price_hits") or 0)
    if text >= 2000:
        return True
    if cards >= _MIN_CARD_CANDIDATES and text >= _MIN_CARD_TEXT:
        return True
    if prices >= 3 and text >= _MIN_CARD_TEXT:
        return True
    if text >= _MIN_READY_TEXT and previous is not None:
        prev = int(previous.get("text_chars") or 0)
        if prev > 0 and abs(text - prev) <= max(80, int(text * 0.05)):
            return True
    return False


def is_sparse_shell(probe: dict[str, Any]) -> bool:
    """True when the page is a cookie/anti-bot shell with almost no content."""
    if not probe.get("has_body"):
        return True
    text = int(probe.get("text_chars") or 0)
    html_chars = int(probe.get("html_chars") or 0)
    cards = int(probe.get("card_candidates") or 0)
    prices = int(probe.get("price_hits") or 0)
    if text <= 40:
        return True
    if text <= _SHELL_TEXT_CHARS and html_chars >= 20000:
        return True
    # Cookie banners often land ~200-500 chars with huge JS payloads and no cards.
    if text < 500 and cards == 0 and prices == 0 and html_chars >= 20000:
        return True
    # Consent dialog filled the viewport; catalog never hydrated.
    if probe.get("consent_only") and cards == 0 and prices == 0 and text < 2000:
        return True
    return False


def dismiss_cookie_consent(page: PageLike) -> dict[str, Any]:
    """Click a visible Accept/Allow control using multilingual text heuristics."""
    try:
        raw = page.evaluate(_DISMISS_CONSENT_JS)
    except Exception as exc:
        logger.debug("consent dismiss failed: %s", exc)
        return {"dismissed": False, "label": None, "method": f"error:{exc}"}
    if not isinstance(raw, dict):
        return {"dismissed": False, "label": None, "method": "invalid"}
    return {
        "dismissed": bool(raw.get("dismissed")),
        "label": raw.get("label"),
        "method": raw.get("method") or "none",
    }


async def adismiss_cookie_consent(page: AsyncPageLike) -> dict[str, Any]:
    try:
        raw = await page.evaluate(_DISMISS_CONSENT_JS)
    except Exception as exc:
        logger.debug("async consent dismiss failed: %s", exc)
        return {"dismissed": False, "label": None, "method": f"error:{exc}"}
    if not isinstance(raw, dict):
        return {"dismissed": False, "label": None, "method": "invalid"}
    return {
        "dismissed": bool(raw.get("dismissed")),
        "label": raw.get("label"),
        "method": raw.get("method") or "none",
    }


def prepare_page_for_capture(
    page: PageLike,
    *,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    poll_ms: int = _DEFAULT_POLL_MS,
) -> CaptureReadyResult:
    """Dismiss consent (if any) and wait until content looks ready or times out."""
    started = time.time()
    consent = dismiss_cookie_consent(page)
    if consent.get("dismissed"):
        try:
            page.wait_for_timeout(350)
        except Exception:
            pass

    previous: Optional[dict[str, Any]] = None
    probe = _parse_probe({})
    deadline = started + max(0, timeout_ms) / 1000.0

    while True:
        try:
            probe = _parse_probe(page.evaluate(_PROBE_JS))
        except Exception as exc:
            logger.debug("readiness probe failed: %s", exc)
            probe = _parse_probe({"has_body": False})

        if is_probe_ready(probe, previous):
            wait_ms = int((time.time() - started) * 1000)
            return CaptureReadyResult(
                ready=True,
                shell=False,
                consent_dismissed=bool(consent.get("dismissed")),
                consent_label=consent.get("label"),
                text_chars=probe["text_chars"],
                html_chars=probe["html_chars"],
                card_candidates=probe["card_candidates"],
                price_hits=probe["price_hits"],
                wait_ms=wait_ms,
                reason="content_ready",
                has_body=probe["has_body"],
            )

        if time.time() >= deadline:
            break
        previous = probe
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            break

    wait_ms = int((time.time() - started) * 1000)
    shell = is_sparse_shell(probe)
    return CaptureReadyResult(
        ready=False,
        shell=shell,
        consent_dismissed=bool(consent.get("dismissed")),
        consent_label=consent.get("label"),
        text_chars=probe["text_chars"],
        html_chars=probe["html_chars"],
        card_candidates=probe["card_candidates"],
        price_hits=probe["price_hits"],
        wait_ms=wait_ms,
        reason="sparse_shell" if shell else "timeout",
        has_body=probe["has_body"],
    )


async def aprepare_page_for_capture(
    page: AsyncPageLike,
    *,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    poll_ms: int = _DEFAULT_POLL_MS,
) -> CaptureReadyResult:
    started = time.time()
    consent = await adismiss_cookie_consent(page)
    if consent.get("dismissed"):
        try:
            await page.wait_for_timeout(350)
        except Exception:
            pass

    previous: Optional[dict[str, Any]] = None
    probe = _parse_probe({})
    deadline = started + max(0, timeout_ms) / 1000.0

    while True:
        try:
            probe = _parse_probe(await page.evaluate(_PROBE_JS))
        except Exception as exc:
            logger.debug("async readiness probe failed: %s", exc)
            probe = _parse_probe({"has_body": False})

        if is_probe_ready(probe, previous):
            wait_ms = int((time.time() - started) * 1000)
            return CaptureReadyResult(
                ready=True,
                shell=False,
                consent_dismissed=bool(consent.get("dismissed")),
                consent_label=consent.get("label"),
                text_chars=probe["text_chars"],
                html_chars=probe["html_chars"],
                card_candidates=probe["card_candidates"],
                price_hits=probe["price_hits"],
                wait_ms=wait_ms,
                reason="content_ready",
                has_body=probe["has_body"],
            )

        if time.time() >= deadline:
            break
        previous = probe
        try:
            await page.wait_for_timeout(poll_ms)
        except Exception:
            break

    wait_ms = int((time.time() - started) * 1000)
    shell = is_sparse_shell(probe)
    return CaptureReadyResult(
        ready=False,
        shell=shell,
        consent_dismissed=bool(consent.get("dismissed")),
        consent_label=consent.get("label"),
        text_chars=probe["text_chars"],
        html_chars=probe["html_chars"],
        card_candidates=probe["card_candidates"],
        price_hits=probe["price_hits"],
        wait_ms=wait_ms,
        reason="sparse_shell" if shell else "timeout",
        has_body=probe["has_body"],
    )
