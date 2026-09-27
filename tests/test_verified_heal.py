import unittest

import pytest

from src.shadow_web.heal_local import fuzzy_ratio
from src.shadow_web.verified_heal import (
    LABEL_MATCH_THRESHOLD,
    _atag_matches,
    _tag_matches,
    averify_selector_on_page,
    verify_selector_in_html,
    verify_selector_on_page,
)

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        _browser = p.chromium.launch(headless=True)
        _browser.close()
    _PLAYWRIGHT_READY = True
except Exception:
    _PLAYWRIGHT_READY = False


class _FakeTarget:
    """Minimal Playwright element: evaluate() answers label and tag reads."""

    def __init__(
        self,
        tag: str = "button",
        label: str = "",
        *,
        count: int = 1,
        visible: bool = True,
        tag_error: bool = False,
    ):
        self._tag = tag
        self._label = label
        self._count = count
        self._visible = visible
        self._tag_error = tag_error

    def count(self) -> int:
        return self._count

    @property
    def first(self):
        return self

    def is_visible(self) -> bool:
        return self._visible

    def evaluate(self, script):
        if "tagName" in script:
            if self._tag_error:
                raise RuntimeError("element detached")
            return self._tag
        return self._label


class _FakePage:
    def __init__(self, target):
        self._target = target

    def locator(self, selector):
        return self._target


class _AsyncFakeTarget(_FakeTarget):
    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible

    async def evaluate(self, script):
        return super().evaluate(script)


class _AsyncFakePage:
    def __init__(self, target):
        self._target = target

    def locator(self, selector):
        return self._target


class TestVerificationFailsClosed(unittest.TestCase):
    """Wrong-but-present elements must not verify as the healed target."""

    def test_threshold_rejects_near_miss_label(self):
        # fuzzy_ratio("login", "logout") == 0.545: the old 0.5 threshold
        # accepted "Logout" as the healed "Login".
        self.assertGreaterEqual(LABEL_MATCH_THRESHOLD, 0.8)
        self.assertLess(fuzzy_ratio("login", "logout"), LABEL_MATCH_THRESHOLD)
        page = _FakePage(_FakeTarget(label="Logout"))
        self.assertFalse(verify_selector_on_page(page, "button.login", "Login", "button"))

    def test_exact_label_still_verifies(self):
        page = _FakePage(_FakeTarget(label="Login"))
        self.assertTrue(verify_selector_on_page(page, "button.login", "Login", "button"))

    def test_empty_label_falls_back_to_type_check(self):
        page = _FakePage(_FakeTarget(tag="button", label=""))
        self.assertTrue(verify_selector_on_page(page, "button.x", "", "button"))

    def test_unreadable_tag_with_empty_label_fails_closed(self):
        page = _FakePage(_FakeTarget(label="", tag_error=True))
        self.assertFalse(verify_selector_on_page(page, "button.x", "", "button"))

    def test_tag_matches_fails_closed_on_exception(self):
        self.assertFalse(_tag_matches(_FakeTarget(tag_error=True), "button"))

    def test_tag_matches_still_accepts_matching_tag(self):
        self.assertTrue(_tag_matches(_FakeTarget(tag="button"), "button"))


@pytest.mark.anyio
async def test_atag_matches_fails_closed_on_exception():
    assert not await _atag_matches(_AsyncFakeTarget(tag_error=True), "button")


@pytest.mark.anyio
async def test_averify_rejects_near_miss_label():
    page = _AsyncFakePage(_AsyncFakeTarget(label="Logout"))
    assert not await averify_selector_on_page(page, "button.login", "Login", "button")


@unittest.skipUnless(_PLAYWRIGHT_READY, "playwright chromium not installed")
class TestVerifiedHeal(unittest.TestCase):
    def test_verify_selector_in_html_accepts_matching_button(self):
        html = """
        <div id="container">
            <button class="new-action-trigger-btn">Submit Order</button>
        </div>
        """
        ok = verify_selector_in_html(
            html,
            "button.new-action-trigger-btn",
            "Submit Order",
            "button",
        )
        self.assertTrue(ok)

    def test_verify_selector_rejects_missing_selector(self):
        html = "<button>Submit Order</button>"
        ok = verify_selector_in_html(html, "button.missing", "Submit Order", "button")
        self.assertFalse(ok)

    def test_verify_selector_rejects_label_mismatch(self):
        html = '<button class="pay">Pay now</button>'
        ok = verify_selector_in_html(html, "button.pay", "Sign in", "button")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
