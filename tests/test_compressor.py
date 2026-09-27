import unittest
from src.shadow_web.compressor import process_html, generate_xml_map, generate_grouped_xml_map
# query/diff reuse compressor._xml_safe; keep their XML generators covered here too
from src.shadow_web.query import QueryResult

class TestShadowWebCompressor(unittest.TestCase):
    def setUp(self):
        self.sample_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Sample Store</title>
            <style>body { background: #fff; }</style>
            <script>console.log("hello");</script>
        </head>
        <body>
            <header class="bg-gray-100 p-4">
                <h1>Welcome to our store</h1>
            </header>
            <main>
                <div class="product-card" data-id="92" style="border: 1px solid;">
                    <h2>Dog Toy</h2>
                    <p>Price: <span>$9.99</span></p>
                    <button class="btn btn-primary" onclick="buy(92)" aria-label="Buy Dog Toy Now">Add to Cart</button>
                    <a href="/products/92" class="text-blue-500">More Info</a>
                </div>
                <form action="/subscribe" method="POST">
                    <input type="email" placeholder="Enter your email" name="user_email" />
                    <button type="submit">Subscribe</button>
                </form>
            </main>
        </body>
        </html>
        """

    def test_strip_dom_removes_unwanted_tags(self):
        clean_html, _, _ = process_html(self.sample_html)
        self.assertNotIn("<style>", clean_html)
        self.assertNotIn("console.log", clean_html)
        # header and footer are kept — they contain navigation and semantic context
        self.assertIn("<header>", clean_html)
        self.assertIn("Welcome to our store", clean_html)

    def test_strip_dom_keeps_crucial_attributes_and_removes_noise(self):
        clean_html, _, _ = process_html(self.sample_html)
        # Class and style attributes should be stripped
        self.assertNotIn("product-card", clean_html)
        self.assertNotIn("border: 1px solid", clean_html)
        
        # Kept attributes
        self.assertIn('data-sid="1"', clean_html) # Added by builder
        self.assertIn('href="/products/92"', clean_html)
        self.assertIn('type="email"', clean_html)
        self.assertIn('placeholder="Enter your email"', clean_html)
        self.assertIn('name="user_email"', clean_html)

    def test_action_map_generation(self):
        _, action_map, groups = process_html(self.sample_html)
        
        # We expect 4 interactive elements:
        # 1. button (Add to Cart)
        # 2. a (More Info)
        # 3. input (Subscribe Email)
        # 4. button (Subscribe submit)
        self.assertEqual(len(action_map), 4)
        
        # First item (button)
        self.assertEqual(action_map[0]["id"], "1")
        self.assertEqual(action_map[0]["type"], "button")
        self.assertEqual(action_map[0]["label"], "Buy Dog Toy Now") # Prefer aria-label
        
        # Second item (link)
        self.assertEqual(action_map[1]["id"], "2")
        self.assertEqual(action_map[1]["type"], "a")
        self.assertEqual(action_map[1]["label"], "More Info")
        self.assertEqual(action_map[1]["href"], "/products/92")

        # Third item (input)
        self.assertEqual(action_map[2]["id"], "3")
        self.assertEqual(action_map[2]["type"], "input[email]")
        self.assertEqual(action_map[2]["placeholder"], "Enter your email")
        self.assertIn("group", action_map[2])

    def test_grouped_xml_generation(self):
        _, _, groups = process_html(self.sample_html)
        xml_map = generate_grouped_xml_map("https://example.com", "Test Store", groups)
        self.assertIn("<group ", xml_map)
        self.assertIn('<action id="1"', xml_map)

    def test_xml_generation(self):
        _, action_map, _ = process_html(self.sample_html)
        xml_map = generate_xml_map("https://example.com", "Test Store", action_map)
        
        self.assertIn('<page url="https://example.com" title="Test Store">', xml_map)
        self.assertIn('<action id="1" type="button" label="Buy Dog Toy Now" group="Main"/>', xml_map)
        self.assertIn('<action id="3" type="input[email]"', xml_map)
        self.assertIn('group="Login Form"', xml_map)


class TestDegenerateInput(unittest.TestCase):
    def test_empty_whitespace_comment_and_none_input(self):
        for value in ("", "   ", "<!-- comment -->", None):
            self.assertEqual(process_html(value), ("", [], []))


class TestFieldLimits(unittest.TestCase):
    def test_oversized_page_controlled_fields_are_clipped(self):
        html = (
            '<a href="/%s" data-sw-bind="%s">Link</a>'
            '<input placeholder="%s">'
        ) % ("h" * 500, "b" * 500, "p" * 500)
        _, action_map, _ = process_html(html)
        link, field = action_map
        self.assertEqual(len(link["href"]), 300)
        self.assertEqual(len(link["bind_id"]), 100)
        self.assertEqual(len(field["placeholder"]), 200)
        # fields must survive, only clipped
        self.assertTrue(link["href"].startswith("/hhh"))
        self.assertTrue(field["placeholder"].startswith("ppp"))


class TestXmlSafety(unittest.TestCase):
    HOSTILE_HTML = '<button aria-label="Buy\x01 now\x0b">Go</button>'

    def test_compressors_strip_illegal_control_characters(self):
        _, action_map, groups = process_html(self.HOSTILE_HTML)
        xml_map = generate_xml_map("https://e\x01xample.com", "Ti\x01tle", action_map)
        grouped = generate_grouped_xml_map("https://e\x01xample.com", "Ti\x01tle", groups)
        for output in (xml_map, grouped):
            self.assertNotIn("\x01", output)
            self.assertNotIn("\x0b", output)
            self.assertIn("Buy now", output)
            self.assertIn('title="Title"', output)

    def test_query_xml_strips_illegal_control_characters(self):
        result = QueryResult(
            query="la\x01bel",
            matches=[{"id": "1", "type": "button", "label": "A\x01B", "group": "G\x01roup"}],
            total=1,
        )
        xml_out = result.xml("https://e\x01xample.com", "Ti\x01tle")
        self.assertNotIn("\x01", xml_out)
        self.assertIn('label="AB"', xml_out)
        self.assertIn('name="Group"', xml_out)


if __name__ == "__main__":
    unittest.main()
