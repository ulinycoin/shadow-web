import unittest

from src.shadow_web.verified_heal import verify_selector_in_html

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        _browser = p.chromium.launch(headless=True)
        _browser.close()
    _PLAYWRIGHT_READY = True
except Exception:
    _PLAYWRIGHT_READY = False


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
