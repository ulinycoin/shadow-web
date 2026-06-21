import unittest

from src.shadow_web.query import (
    QueryResult,
    parse_query,
    query_actions,
    shadow_grep,
)


class TestShadowGrep(unittest.TestCase):
    def setUp(self):
        self.actions = [
            {"id": "1", "type": "button", "label": "Sign in", "group": "Login Form"},
            {"id": "2", "type": "input[email]", "label": "Email", "placeholder": "Email", "group": "Login Form"},
            {"id": "3", "type": "button", "label": "Search", "group": "Navigation"},
            {"id": "4", "type": "a", "label": "Buy now", "href": "/checkout", "group": "Checkout"},
            {"id": "5", "type": "input[text]", "label": "", "placeholder": "Promo code", "group": "Checkout"},
        ]
        self.groups = [
            {"name": "Login Form", "elements": [self.actions[0], self.actions[1]]},
            {"name": "Navigation", "elements": [self.actions[2]]},
            {"name": "Checkout", "elements": [self.actions[3], self.actions[4]]},
        ]

    def test_empty_query_returns_all(self):
        result = shadow_grep(self.actions, "")
        self.assertEqual(result.count, 5)
        self.assertEqual(result.total, 5)

    def test_combined_filters_and_semantics(self):
        result = shadow_grep(self.actions, "type:button intent:login", groups=self.groups)
        self.assertEqual([a["id"] for a in result.matches], ["1"])

    def test_semicolon_and_combined(self):
        result = shadow_grep(self.actions, "group:Checkout; type:input")
        self.assertEqual([a["id"] for a in result.matches], ["5"])

    def test_label_regex(self):
        result = shadow_grep(self.actions, "label~/buy/i")
        self.assertEqual([a["id"] for a in result.matches], ["4"])

    def test_placeholder_substring(self):
        result = shadow_grep(self.actions, "placeholder~promo")
        self.assertEqual([a["id"] for a in result.matches], ["5"])

    def test_href_filter(self):
        result = shadow_grep(self.actions, "href:/checkout")
        self.assertEqual([a["id"] for a in result.matches], ["4"])

    def test_intent_buy(self):
        result = shadow_grep(self.actions, "intent:buy")
        self.assertEqual([a["id"] for a in result.matches], ["4"])

    def test_unknown_intent_returns_empty(self):
        result = shadow_grep(self.actions, "intent:nonexistent")
        self.assertEqual(result.count, 0)

    def test_group_name_text_search(self):
        result = shadow_grep(self.actions, "checkout", groups=self.groups)
        self.assertEqual({a["id"] for a in result.matches}, {"4", "5"})

    def test_terse_format(self):
        result = shadow_grep(self.actions, "id:1")
        text = result.terse()
        self.assertIn("@1 button Sign in", text)
        self.assertIn("1/5", text)

    def test_xml_format(self):
        result = shadow_grep(self.actions, "group:Login Form", groups=self.groups)
        xml = result.xml(url="https://example.com", title="Test")
        self.assertIn('<group name="Login Form">', xml)
        self.assertIn('<action id="1"', xml)

    def test_query_actions_backward_compat(self):
        out = query_actions(self.actions, "type:button")
        self.assertEqual(len(out), 2)

    def test_parse_query_multiple_clauses(self):
        clauses = parse_query("type:button intent:login label~/sign/i")
        kinds = [c.kind for c in clauses]
        self.assertEqual(kinds, ["type", "intent", "label"])

    def test_no_matches_terse(self):
        result = shadow_grep(self.actions, "intent:extract")
        self.assertIn("(no matches)", result.terse())


if __name__ == "__main__":
    unittest.main()
