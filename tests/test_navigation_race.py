"""Regression tests for the click → navigation → refresh race (F1).

A click can commit a navigation. Playwright then raises "Execution context was
destroyed" from the evaluate() inside refresh(), even though the click itself
succeeded. refresh() must retry that observation exactly once, and must NOT
retry real failures.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shadow_web.browser_use import AsyncShadowPage
from shadow_web.capture_ready import CaptureReadyResult
from shadow_web.dom_capture import FlattenResult
from shadow_web.utils import is_transient_navigation_error
from shadow_web.wrapper import ShadowPage

NAV_ERROR = (
    "Page.evaluate: Execution context was destroyed, most likely because of a navigation"
)


def _ready() -> CaptureReadyResult:
    return CaptureReadyResult(
        ready=True,
        shell=False,
        consent_dismissed=False,
        consent_label=None,
        text_chars=100,
        html_chars=200,
        card_candidates=1,
        price_hits=0,
        wait_ms=0,
        reason="content_ready",
    )


def _flattened() -> FlattenResult:
    return FlattenResult(
        html="<body><button>Go</button></body>",
        bindings={},
        stats={"interactive": 1},
    )


def _sync_page() -> SimpleNamespace:
    return SimpleNamespace(
        url="https://portal.example/list",
        title=lambda: "List",
        wait_for_load_state=lambda *a, **k: None,
        wait_for_timeout=lambda *a, **k: None,
    )


# ── classification ───────────────────────────────────────────────────────────


def test_navigation_errors_are_transient():
    assert is_transient_navigation_error(NAV_ERROR)
    assert is_transient_navigation_error("Cannot find context with specified id")
    assert is_transient_navigation_error("frame was detached")


def test_closed_target_is_not_transient():
    # A page that is gone is terminal, not something to retry.
    assert not is_transient_navigation_error("Target page, context or browser has been closed")
    assert not is_transient_navigation_error(RuntimeError("selector not found"))


# ── sync wrapper ─────────────────────────────────────────────────────────────


def test_sync_refresh_retries_once_after_navigation_race():
    shadow = ShadowPage(_sync_page(), prefer_webmcp=False)
    calls = {"n": 0}

    def fake_capture(page, mode="auto"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(NAV_ERROR)
        return _flattened()

    with patch("shadow_web.wrapper.capture_page", side_effect=fake_capture), patch(
        "shadow_web.wrapper.prepare_page_for_capture", return_value=_ready()
    ):
        clean_html, xml_map = shadow.refresh()

    assert calls["n"] == 2, "the navigation race must be retried exactly once"
    assert "Go" in clean_html
    assert shadow.action_map, "the retry must produce a real Action Map"


def test_sync_refresh_does_not_retry_real_failures():
    shadow = ShadowPage(_sync_page(), prefer_webmcp=False)
    calls = {"n": 0}

    def fake_capture(page, mode="auto"):
        calls["n"] += 1
        raise RuntimeError("chromium died")

    with patch("shadow_web.wrapper.capture_page", side_effect=fake_capture), patch(
        "shadow_web.wrapper.prepare_page_for_capture", return_value=_ready()
    ):
        with pytest.raises(RuntimeError, match="chromium died"):
            shadow.refresh()

    assert calls["n"] == 1


def test_sync_wait_after_click_waits_for_new_document():
    """After the URL moves, the wait must target the NEW document."""
    waits: list[tuple] = []
    page = SimpleNamespace(
        url="https://portal.example/leave",
        wait_for_load_state=lambda state, timeout=None: waits.append((state, timeout)),
        wait_for_timeout=lambda ms: waits.append(("timeout", ms)),
    )
    shadow = ShadowPage(page, prefer_webmcp=False)
    shadow.last_url = "https://portal.example/employees"  # pre-click URL

    shadow._wait_after_click(5000)

    assert ("load", 5000) in waits, "must wait for the new document to load"
    assert ("domcontentloaded", 5000) in waits


def test_sync_wait_after_click_skips_load_wait_when_url_unchanged():
    waits: list[tuple] = []
    page = SimpleNamespace(
        url="https://portal.example/employees",
        wait_for_load_state=lambda state, timeout=None: waits.append((state, timeout)),
        wait_for_timeout=lambda ms: waits.append(("timeout", ms)),
    )
    shadow = ShadowPage(page, prefer_webmcp=False)
    shadow.last_url = "https://portal.example/employees"

    shadow._wait_after_click(5000)

    assert ("load", 5000) not in waits
    assert ("domcontentloaded", 5000) in waits


# ── async wrapper ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_async_refresh_retries_once_after_navigation_race():
    page = SimpleNamespace(
        url="https://portal.example/list",
        title=AsyncMock(return_value="List"),
        wait_for_load_state=AsyncMock(),
        wait_for_timeout=AsyncMock(),
    )
    shadow = AsyncShadowPage(page, prefer_webmcp=False)
    calls = {"n": 0}

    async def fake_capture(p, mode="auto"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(NAV_ERROR)
        return _flattened()

    with patch("shadow_web.browser_use.acapture_page", side_effect=fake_capture), patch(
        "shadow_web.browser_use.aprepare_page_for_capture",
        new=AsyncMock(return_value=_ready()),
    ):
        clean_html, _ = await shadow.refresh()

    assert calls["n"] == 2
    assert "Go" in clean_html


@pytest.mark.anyio
async def test_async_refresh_does_not_retry_real_failures():
    page = SimpleNamespace(
        url="https://portal.example/list",
        title=AsyncMock(return_value="List"),
        wait_for_load_state=AsyncMock(),
        wait_for_timeout=AsyncMock(),
    )
    shadow = AsyncShadowPage(page, prefer_webmcp=False)
    calls = {"n": 0}

    async def fake_capture(p, mode="auto"):
        calls["n"] += 1
        raise RuntimeError("chromium died")

    with patch("shadow_web.browser_use.acapture_page", side_effect=fake_capture), patch(
        "shadow_web.browser_use.aprepare_page_for_capture",
        new=AsyncMock(return_value=_ready()),
    ):
        with pytest.raises(RuntimeError, match="chromium died"):
            await shadow.refresh()

    assert calls["n"] == 1


@pytest.mark.anyio
async def test_async_wait_after_click_waits_for_new_document():
    waits: list[tuple] = []

    async def record_load(state, timeout=None):
        waits.append((state, timeout))

    async def record_timeout(ms):
        waits.append(("timeout", ms))

    page = SimpleNamespace(
        url="https://portal.example/leave",
        wait_for_load_state=record_load,
        wait_for_timeout=record_timeout,
    )
    shadow = AsyncShadowPage(page, prefer_webmcp=False)
    shadow.last_url = "https://portal.example/employees"

    await shadow._wait_after_click(5000)

    assert ("load", 5000) in waits


def test_perform_action_still_refreshes_once():
    """The retry lives inside refresh(); callers keep their single call."""
    shadow = ShadowPage(MagicMock(), prefer_webmcp=False)
    shadow.action_map = [{"id": "1", "type": "button", "label": "Go"}]
    shadow.bindings = {}
    shadow._execute_selector_action = MagicMock()
    shadow._wait_after_click = MagicMock()
    shadow.refresh = MagicMock()

    shadow._perform_action("1", "click")

    shadow._wait_after_click.assert_called_once_with(5000)
    shadow.refresh.assert_called_once_with()
