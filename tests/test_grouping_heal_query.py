import unittest

from src.shadow_web.grouping import group_action_map
from src.shadow_web.heal_local import HealCache, fuzzy_ratio, rank_candidates, score_candidate
from src.shadow_web.query import query_actions
from lxml import html


class TestGrouping(unittest.TestCase):
    def test_form_grouping(self):
        raw = """
        <body>
          <form action="/login">
            <input type="email" placeholder="Email" data-sid="1"/>
            <input type="password" placeholder="Password" data-sid="2"/>
            <button type="submit" data-sid="3">Sign in</button>
          </form>
        </body>
        """
        tree = html.fromstring(raw)
        action_map = [
            {"id": "1", "type": "input[email]", "label": "Email"},
            {"id": "2", "type": "input[password]", "label": "Password"},
            {"id": "3", "type": "button", "label": "Sign in"},
        ]
        groups = group_action_map(tree, action_map)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "Login Form")
        self.assertEqual(len(groups[0]["elements"]), 3)


class TestHealLocal(unittest.TestCase):
    def test_fuzzy_ratio_exact(self):
        self.assertEqual(fuzzy_ratio("Sign in", "Sign in"), 1.0)

    def test_score_candidate_with_stable_attrs(self):
        score = score_candidate(
            "Checkout",
            "Checkout now",
            has_testid=True,
            has_id=False,
        )
        self.assertGreaterEqual(score, 0.85)

    def test_rank_candidates_uses_python_scoring(self):
        ranked = rank_candidates(
            "Sign in",
            [
                {"selector": "button.a", "label": "Sign in", "has_testid": False, "has_id": True},
                {"selector": "button.b", "label": "Cancel", "has_testid": False, "has_id": False},
            ],
        )
        self.assertEqual(ranked[0]["selector"], "button.a")
        self.assertGreater(ranked[0]["score"], ranked[1]["score"] if len(ranked) > 1 else 0)

    def test_heal_cache_roundtrip(self):
        import tempfile
        import os

        path = os.path.join(tempfile.mkdtemp(), "heal_cache.json")
        cache = HealCache(path=path)
        cache.set("https://shop.example/checkout", "Pay", "button", "button.pay")
        got = cache.get("https://shop.example/checkout", "Pay", "button")
        self.assertEqual(got, "button.pay")
        cache.invalidate("https://shop.example/checkout", "Pay", "button")
        self.assertIsNone(cache.get("https://shop.example/checkout", "Pay", "button"))


class TestQuery(unittest.TestCase):
    def setUp(self):
        self.actions = [
            {"id": "1", "type": "button", "label": "Sign in", "group": "Login Form"},
            {"id": "2", "type": "input[email]", "label": "Email", "group": "Login Form"},
            {"id": "3", "type": "button", "label": "Search", "group": "Navigation"},
        ]

    def test_query_by_type(self):
        out = query_actions(self.actions, "type:button")
        self.assertEqual(len(out), 2)

    def test_query_by_intent(self):
        out = query_actions(self.actions, "intent:login")
        self.assertTrue(all(a["group"] == "Login Form" for a in out))

    def test_query_by_id_list(self):
        out = query_actions(self.actions, "id:1,3")
        self.assertEqual([a["id"] for a in out], ["1", "3"])


if __name__ == "__main__":
    unittest.main()
