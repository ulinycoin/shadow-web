import unittest

from shadow_web.mcp.server import _extract_search_results


class TestWebSearchExtraction(unittest.TestCase):
    def test_filters_nav_and_internal_links(self):
        action_map = [
            {"id": "1", "label": "Settings", "href": "https://search.brave.com/settings"},
            {"id": "2", "label": "Skip to content", "href": "https://example.com/page"},
            {"id": "3", "label": "Playwright Python docs tutorial", "href": "https://playwright.dev/python/docs/intro"},
            {"id": "4", "label": "Playwright Python docs tutorial", "href": "https://playwright.dev/python/docs/intro"},
            {"id": "5", "label": "Short", "href": "https://example.com/short"},
        ]

        results = _extract_search_results(action_map, ("brave.com",))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://playwright.dev/python/docs/intro")
        self.assertEqual(results[0]["sid"], "3")


if __name__ == "__main__":
    unittest.main()
