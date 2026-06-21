import unittest

from src.shadow_web.diff import (
    build_snapshot,
    compute_page_diff,
    diff_terse,
    generate_diff_xml,
)


class TestPageDiff(unittest.TestCase):
    def setUp(self):
        self.actions_v1 = [
            {"id": "1", "type": "button", "label": "Sign in", "group": "Login Form"},
            {"id": "2", "type": "input[email]", "label": "Email", "placeholder": "Email", "group": "Login Form"},
            {"id": "3", "type": "button", "label": "Search", "group": "Navigation"},
        ]
        self.groups_v1 = [
            {"name": "Login Form", "elements": [self.actions_v1[0], self.actions_v1[1]]},
            {"name": "Navigation", "elements": [self.actions_v1[2]]},
        ]
        self.url = "https://example.com"
        self.title = "Example"

    def _snap(self, actions, groups):
        return build_snapshot(self.url, self.title, "action_map", actions, groups)

    def test_first_snapshot_is_full(self):
        snap = self._snap(self.actions_v1, self.groups_v1)
        diff = compute_page_diff(None, snap)
        self.assertTrue(diff.is_full_snapshot)
        self.assertFalse(diff.has_changes)

    def test_no_changes_returns_empty_delta(self):
        prev = self._snap(self.actions_v1, self.groups_v1)
        curr = self._snap(list(self.actions_v1), list(self.groups_v1))
        diff = compute_page_diff(prev, curr)
        self.assertFalse(diff.is_full_snapshot)
        self.assertFalse(diff.has_changes)
        xml = generate_diff_xml(diff)
        self.assertIn('diff="true"', xml)
        self.assertIn("No action changes since last snapshot", xml)

    def test_appeared_and_disappeared(self):
        prev = self._snap(self.actions_v1, self.groups_v1)
        actions_v2 = [
            self.actions_v1[0],
            {"id": "4", "type": "button", "label": "Buy now", "group": "Checkout"},
        ]
        groups_v2 = [
            {"name": "Login Form", "elements": [actions_v2[0]]},
            {"name": "Checkout", "elements": [actions_v2[1]]},
        ]
        curr = self._snap(actions_v2, groups_v2)
        diff = compute_page_diff(prev, curr)
        self.assertEqual(len(diff.appeared), 1)
        self.assertEqual(diff.appeared[0].sid, "4")
        self.assertEqual(len(diff.disappeared), 2)
        disappeared_ids = {entry.sid for entry in diff.disappeared}
        self.assertEqual(disappeared_ids, {"2", "3"})

    def test_label_change_is_appeared_and_disappeared(self):
        prev = self._snap(self.actions_v1, self.groups_v1)
        actions_v2 = list(self.actions_v1)
        actions_v2[0] = {**actions_v2[0], "label": "Log in"}
        curr = self._snap(actions_v2, self.groups_v1)
        diff = compute_page_diff(prev, curr)
        self.assertEqual(len(diff.appeared), 1)
        self.assertEqual(diff.appeared[0].action["label"], "Log in")
        self.assertEqual(len(diff.disappeared), 1)
        self.assertEqual(diff.disappeared[0].action["label"], "Sign in")

    def test_changed_input_schema(self):
        tool_v1 = [
            {
                "id": "1",
                "type": "webmcp_tool",
                "tool_name": "search",
                "group": "WebMCP Tools",
                "input_schema": {"query": "string"},
            }
        ]
        groups = [{"name": "WebMCP Tools", "elements": tool_v1}]
        prev = build_snapshot(self.url, self.title, "webmcp", tool_v1, groups)
        tool_v2 = [
            {
                **tool_v1[0],
                "input_schema": {"query": "string", "limit": "number"},
            }
        ]
        curr = build_snapshot(self.url, self.title, "webmcp", tool_v2, groups)
        diff = compute_page_diff(prev, curr)
        self.assertEqual(len(diff.changed), 1)
        self.assertEqual(diff.changed[0].sid, "1")

    def test_url_change_resets_to_full(self):
        prev = self._snap(self.actions_v1, self.groups_v1)
        curr = build_snapshot(
            "https://other.com",
            "Other",
            "action_map",
            self.actions_v1,
            self.groups_v1,
        )
        diff = compute_page_diff(prev, curr)
        self.assertTrue(diff.is_full_snapshot)

    def test_diff_terse_no_changes(self):
        prev = self._snap(self.actions_v1, self.groups_v1)
        curr = self._snap(self.actions_v1, self.groups_v1)
        diff = compute_page_diff(prev, curr)
        text = diff_terse(diff)
        self.assertIn("# diff", text)
        self.assertIn("(no changes)", text)

    def test_generate_diff_xml_uses_full_when_baseline_missing(self):
        snap = self._snap(self.actions_v1, self.groups_v1)
        diff = compute_page_diff(None, snap)
        full = "<page>full snapshot</page>"
        xml = generate_diff_xml(diff, full_xml=full)
        self.assertEqual(xml, full)


if __name__ == "__main__":
    unittest.main()
