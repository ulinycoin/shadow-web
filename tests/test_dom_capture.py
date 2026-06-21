import unittest
from src.shadow_web.compressor import process_html
from src.shadow_web.dom_capture import binding_path_json


class TestDomCapturePipeline(unittest.TestCase):
    """Tests flattened HTML fixtures through the compressor (no browser required)."""

    def test_flattened_shadow_dom_fixture_preserves_bind_ids(self):
        """Simulates output from in-browser flatten with shadow content inlined."""
        flattened_html = """
        <body>
          <my-widget>
            <button data-sw-bind="sw-1" aria-label="Buy inside shadow">Buy</button>
            <a data-sw-bind="sw-2" href="/info">Details</a>
          </my-widget>
          <div data-sw-iframe="cross-origin" data-sw-src="https://other.example/embed">
            [iframe: https://other.example/embed]
          </div>
        </body>
        """

        clean_html, action_map = process_html(flattened_html)[:2]

        self.assertIn('data-sw-bind="sw-1"', clean_html)
        self.assertIn('data-sid="1"', clean_html)
        self.assertEqual(len(action_map), 2)
        self.assertEqual(action_map[0]["bind_id"], "sw-1")
        self.assertEqual(action_map[0]["label"], "Buy inside shadow")
        self.assertEqual(action_map[1]["bind_id"], "sw-2")
        self.assertIn("data-sw-iframe", flattened_html)

    def test_binding_path_json_roundtrip(self):
        binding = {
            "path": [{"t": "body"}, {"t": "child", "i": 0}, {"t": "shadow"}, {"t": "child", "i": 1}],
            "tag": "button",
        }
        encoded = binding_path_json(binding)
        self.assertIn('"shadow"', encoded)
        self.assertIn('"i":1', encoded)


if __name__ == "__main__":
    unittest.main()
