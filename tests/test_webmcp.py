import unittest
from unittest.mock import MagicMock

from src.shadow_web.webmcp import (
    WebMcpTool,
    detect_webmcp,
    execute_webmcp_tool,
    generate_webmcp_xml_map,
    webmcp_tools_to_action_map,
    webmcp_tools_terse,
)


class TestWebMcpBridge(unittest.TestCase):
    def test_detect_webmcp_unavailable(self):
        page = MagicMock()
        page.evaluate.return_value = {
            "available": False,
            "tools": [],
            "api": None,
            "error": None,
        }
        snapshot = detect_webmcp(page)
        self.assertFalse(snapshot.available)
        self.assertEqual(snapshot.count, 0)

    def test_detect_webmcp_tools(self):
        page = MagicMock()
        page.evaluate.return_value = {
            "available": True,
            "api": "document",
            "tools": [
                {
                    "name": "search_products",
                    "description": "Search catalog",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                }
            ],
        }
        snapshot = detect_webmcp(page)
        self.assertTrue(snapshot.available)
        self.assertEqual(snapshot.tools[0].name, "search_products")

    def test_tools_to_action_map(self):
        tools = [WebMcpTool("checkout", "Complete checkout")]
        action_map = webmcp_tools_to_action_map(tools)
        self.assertEqual(action_map[0]["type"], "webmcp_tool")
        self.assertEqual(action_map[0]["tool_name"], "checkout")
        self.assertEqual(action_map[0]["id"], "1")

    def test_generate_webmcp_xml_map(self):
        tools = [WebMcpTool("search_products", "Search catalog")]
        xml = generate_webmcp_xml_map("https://shop.example", "Shop", tools)
        self.assertIn('mode="webmcp"', xml)
        self.assertIn('tool_name="search_products"', xml)

    def test_execute_webmcp_tool(self):
        page = MagicMock()
        page.evaluate.return_value = {"ok": True, "result": {"items": []}}
        result = execute_webmcp_tool(page, "search_products", {"query": "dog"})
        self.assertEqual(result, {"items": []})

    def test_execute_webmcp_tool_failure(self):
        page = MagicMock()
        page.evaluate.return_value = {"ok": False, "error": "tool_not_found"}
        with self.assertRaises(RuntimeError):
            execute_webmcp_tool(page, "missing_tool")

    def test_webmcp_tools_terse(self):
        text = webmcp_tools_terse([WebMcpTool("search_products", "Search catalog")])
        self.assertIn("@1 tool:search_products", text)


if __name__ == "__main__":
    unittest.main()
