import unittest

from src.shadow_web.a11y_capture import (
    A11yNode,
    build_a11y_supplement,
    labels_from_dom_html,
    merge_dom_and_a11y,
    needs_a11y_supplement,
    parse_ax_nodes,
)
from src.shadow_web.dom_capture import FlattenResult


class TestA11yCapture(unittest.TestCase):
    def test_parse_ax_nodes_skips_ignored_and_non_interactive(self):
        raw = [
            {"nodeId": "1", "ignored": True, "role": {"value": "button"}, "backendDOMNodeId": 1},
            {"nodeId": "2", "role": {"value": "StaticText"}, "name": {"value": "Hello"}, "backendDOMNodeId": 2},
            {
                "nodeId": "3",
                "role": {"value": "button"},
                "name": {"value": "Pay now"},
                "backendDOMNodeId": 99,
            },
        ]
        nodes = parse_ax_nodes(raw)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].name, "Pay now")
        self.assertEqual(nodes[0].backend_node_id, 99)

    def test_build_supplement_skips_covered_labels(self):
        nodes = [
            A11yNode(role="button", name="Sign in", backend_node_id=1),
            A11yNode(role="button", name="Hidden checkout", backend_node_id=2),
        ]
        html, bindings = build_a11y_supplement(
            nodes,
            existing_labels=["Sign in"],
        )
        self.assertIn("Hidden checkout", html)
        self.assertNotIn('aria-label="Sign in"', html)
        self.assertEqual(len(bindings), 1)

    def test_merge_auto_adds_supplement_when_shadow_hosts_and_uncovered(self):
        dom = FlattenResult(
            html='<body><host><button data-sw-bind="sw-1">Open</button></host></body>',
            bindings={"sw-1": {"path": [{"t": "body"}], "tag": "button"}},
            stats={"shadow_hosts": 1, "interactive": 1},
        )
        a11y_nodes = [
            A11yNode(role="button", name="Open", backend_node_id=1),
            A11yNode(role="button", name="Closed shadow action", backend_node_id=2),
        ]
        from src.shadow_web.a11y_capture import A11yCaptureResult

        a11y = A11yCaptureResult(nodes=a11y_nodes)
        merged = merge_dom_and_a11y(dom, a11y, mode="auto")
        self.assertIn("Closed shadow action", merged.html)
        self.assertEqual(merged.stats.get("a11y_supplement_nodes"), 1)

    def test_needs_a11y_supplement_false_without_shadow_hosts(self):
        dom = FlattenResult(html="<body></body>", stats={"shadow_hosts": 0})
        from src.shadow_web.a11y_capture import A11yCaptureResult

        a11y = A11yCaptureResult(
            nodes=[A11yNode(role="button", name="X", backend_node_id=1)]
        )
        self.assertFalse(needs_a11y_supplement(dom, a11y, mode="auto"))

    def test_labels_from_dom_html(self):
        html = '<body><button data-sw-bind="sw-1" aria-label="Buy">Buy</button></body>'
        labels = labels_from_dom_html(html)
        self.assertEqual(labels, ["Buy"])


if __name__ == "__main__":
    unittest.main()
