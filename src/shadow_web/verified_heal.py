"""Verify healed selectors resolve to visible, label-matching elements."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from .heal_local import fuzzy_ratio

PageLike = Any
logger = logging.getLogger(__name__)

# Fuzzy ratio only; a plain substring relation is not evidence that the element
# is the same control ("login" vs "logout" scores 0.545, so 0.5 accepted it).
LABEL_MATCH_THRESHOLD = 0.8

# Cap concurrent Playwright verification launches. Each browser instance is
# expensive (CPU/RAM), and /v1/heal can otherwise fan out one browser per
# request under load.
_VERIFY_SEMAPHORE = threading.BoundedSemaphore(
    max(1, int(os.environ.get("SHADOW_WEB_VERIFY_CONCURRENCY", "4")))
)
_VERIFY_LAUNCH_TIMEOUT_S = float(
    os.environ.get("SHADOW_WEB_VERIFY_LAUNCH_TIMEOUT", "30")
)


def verify_selector_on_page(
    page: PageLike,
    selector: str,
    label: str,
    action_type: str,
    *,
    max_matches: int = 3,
    label_threshold: float = LABEL_MATCH_THRESHOLD,
) -> bool:
    """
    Return True when ``selector`` resolves to visible element(s) matching ``label``.

    Allows up to ``max_matches`` for ambiguous lists; rejects zero or too many.
    """
    if not selector or not selector.strip():
        return False

    try:
        locator = page.locator(selector)
        count = locator.count()
        if count == 0 or count > max_matches:
            return False

        target = locator.first
        if not target.is_visible():
            return False

        if not label:
            return _tag_matches(target, action_type)

        actual = target.evaluate(
            """(el) => {
                return (
                    el.getAttribute('aria-label') ||
                    el.getAttribute('placeholder') ||
                    el.getAttribute('name') ||
                    el.getAttribute('alt') ||
                    (el.textContent || '').replace(/\\s+/g, ' ').trim()
                );
            }"""
        )
        actual = str(actual or "").strip()
        if fuzzy_ratio(label, actual) >= label_threshold:
            return True
        # Empty/absent label text: fall back to the type check, which itself
        # fails closed when the tag cannot be read.
        return not actual and _tag_matches(target, action_type)
    except Exception:
        return False


async def averify_selector_on_page(
    page: PageLike,
    selector: str,
    label: str,
    action_type: str,
    *,
    max_matches: int = 3,
    label_threshold: float = LABEL_MATCH_THRESHOLD,
) -> bool:
    """Async verify for Playwright async Page."""
    if not selector or not selector.strip():
        return False

    try:
        locator = page.locator(selector)
        count = await locator.count()
        if count == 0 or count > max_matches:
            return False

        target = locator.first
        if not await target.is_visible():
            return False

        if not label:
            return await _atag_matches(target, action_type)

        actual = await target.evaluate(
            """(el) => {
                return (
                    el.getAttribute('aria-label') ||
                    el.getAttribute('placeholder') ||
                    el.getAttribute('name') ||
                    el.getAttribute('alt') ||
                    (el.textContent || '').replace(/\\s+/g, ' ').trim()
                );
            }"""
        )
        actual = str(actual or "").strip()
        if fuzzy_ratio(label, actual) >= label_threshold:
            return True
        # Empty/absent label text: fall back to the type check, which itself
        # fails closed when the tag cannot be read.
        return not actual and await _atag_matches(target, action_type)
    except Exception:
        return False


def verify_selector_in_html(
    context_html: str,
    selector: str,
    label: str,
    action_type: str,
) -> bool:
    """Verify selector against isolated HTML (server-side heal validation).

    Bounded by ``_VERIFY_SEMAPHORE`` so N requests do not spawn N browsers.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    wrapped = context_html.strip()
    if not wrapped.lower().startswith("<html"):
        wrapped = f"<html><body>{wrapped}</body></html>"

    acquired = _VERIFY_SEMAPHORE.acquire(timeout=_VERIFY_LAUNCH_TIMEOUT_S)
    if not acquired:
        logger.warning(
            "verify_selector_in_html: concurrency limit reached; "
            "failing verification closed (no browser launched)"
        )
        return False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, timeout=int(_VERIFY_LAUNCH_TIMEOUT_S * 1000)
            )
            try:
                page = browser.new_page()
                page.set_content(wrapped, wait_until="domcontentloaded")
                return verify_selector_on_page(page, selector, label, action_type)
            finally:
                browser.close()
    except PlaywrightTimeoutError:
        logger.warning("verify_selector_in_html: browser launch timed out")
        return False
    finally:
        _VERIFY_SEMAPHORE.release()


def _tag_matches(target: Any, action_type: str) -> bool:
    base_tag = action_type.split("[")[0].lower()
    if base_tag in ("webmcp_tool", "iframe"):
        return True
    try:
        tag = target.evaluate("(el) => el.tagName.toLowerCase()")
        if base_tag == "a" and tag == "a":
            return True
        if base_tag == "button" and tag in ("button", "input"):
            return True
        if base_tag.startswith("input") and tag == "input":
            return True
        return tag == base_tag
    except Exception:
        # Unreadable tag is not evidence of a type match: fail closed.
        return False


async def _atag_matches(target: Any, action_type: str) -> bool:
    base_tag = action_type.split("[")[0].lower()
    if base_tag in ("webmcp_tool", "iframe"):
        return True
    try:
        tag = await target.evaluate("(el) => el.tagName.toLowerCase()")
        if base_tag == "a" and tag == "a":
            return True
        if base_tag == "button" and tag in ("button", "input"):
            return True
        if base_tag.startswith("input") and tag == "input":
            return True
        return tag == base_tag
    except Exception:
        # Unreadable tag is not evidence of a type match: fail closed.
        return False
