"""Tests for SchemaSnap — table, form, and list extraction from HTML."""

from __future__ import annotations

from shadow_web.schema_snap import (
    parse_tables,
    parse_forms,
    parse_lists,
    parse_page,
    export_table_json,
    export_table_csv,
    table_to_csv,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

SIMPLE_TABLE = """<table>
  <thead>
    <tr><th>Name</th><th>Age</th><th>Email</th></tr>
  </thead>
  <tbody>
    <tr><td>Alice</td><td>30</td><td>alice@test.com</td></tr>
    <tr><td>Bob</td><td>25</td><td>bob@test.com</td></tr>
    <tr><td>Charlie</td><td>35</td><td>charlie@test.com</td></tr>
  </tbody>
</table>"""

TABLE_WITH_NUMBERS = """<table>
  <thead>
    <tr><th>Product</th><th>Price</th><th>Discount</th><th>Stock</th></tr>
  </thead>
  <tbody>
    <tr><td>Widget A</td><td>$19.99</td><td>10%</td><td>150</td></tr>
    <tr><td>Widget B</td><td>$29.99</td><td>5%</td><td>83</td></tr>
    <tr><td>Widget C</td><td>$9.99</td><td>—</td><td>1,200</td></tr>
  </tbody>
</table>"""

TABLE_WITH_DATES = """<table>
  <thead>
    <tr><th>Date</th><th>Event</th></tr>
  </thead>
  <tbody>
    <tr><td>2026-06-01</td><td>Release v2.0</td></tr>
    <tr><td>2026-07-15</td><td>Conference</td></tr>
    <tr><td>2026-12-31</td><td>Deadline</td></tr>
  </tbody>
</table>"""

TABLE_NO_HEADER = """<table>
  <tbody>
    <tr><td>Red</td><td>#FF0000</td><td>Primary</td></tr>
    <tr><td>Blue</td><td>#0000FF</td><td>Primary</td></tr>
    <tr><td>Green</td><td>#00FF00</td><td>Secondary</td></tr>
  </tbody>
</table>"""

SIMPLE_FORM = """<form action="/login" method="POST">
  <label for="email">Email Address</label>
  <input type="email" id="email" name="email" required placeholder="you@example.com">

  <label for="password">Password</label>
  <input type="password" id="password" name="password" required minlength="8">

  <button type="submit">Sign In</button>
</form>"""

FORM_WITH_SELECT = """<form action="/register" method="POST">
  <label for="name">Full Name</label>
  <input type="text" id="name" name="name" required>

  <label for="country">Country</label>
  <select id="country" name="country" required>
    <option value="">Select country...</option>
    <option value="US">United States</option>
    <option value="UK">United Kingdom</option>
    <option value="DE">Germany</option>
    <option value="FR">France</option>
  </select>

  <label for="bio">Bio</label>
  <textarea id="bio" name="bio" placeholder="Tell us about yourself" rows="4"></textarea>

  <input type="checkbox" name="newsletter" value="yes">
  <label for="newsletter">Subscribe to newsletter</label>

  <button type="submit">Register</button>
</form>"""

UNORDERED_LIST = """<ul>
  <li>Apples</li>
  <li>Bananas</li>
  <li>Cherries</li>
</ul>"""

ORDERED_LIST = """<ol>
  <li>First step</li>
  <li>Second step</li>
  <li>Third step</li>
  <li>Fourth step</li>
</ol>"""

SELECT_MULTI = """<select name="hobbies">
  <option value="reading">Reading</option>
  <option value="hiking">Hiking</option>
  <option value="coding">Coding</option>
  <option value="gaming">Gaming</option>
</select>"""

MIXED_PAGE = SIMPLE_TABLE + SIMPLE_FORM + UNORDERED_LIST


# ── Test: Table Parsing ─────────────────────────────────────────────────────


class TestParseTables:
    def test_simple_table(self):
        result = parse_tables(SIMPLE_TABLE)
        assert len(result) == 1
        table = result[0]
        assert table["columns"] == ["Name", "Age", "Email"]
        assert table["column_count"] == 3
        assert table["total_rows"] == 3
        assert table["rows"][0] == ["Alice", "30", "alice@test.com"]
        assert table["rows"][1] == ["Bob", "25", "bob@test.com"]
        assert table["rows"][2] == ["Charlie", "35", "charlie@test.com"]

    def test_type_inference_string(self):
        table = parse_tables(SIMPLE_TABLE)[0]
        assert table["types"][0] == "string"  # Name
        assert table["types"][1] == "integer"  # Age

    def test_type_inference_email(self):
        table = parse_tables(SIMPLE_TABLE)[0]
        assert table["types"][2] == "email"  # Email

    def test_type_inference_currency(self):
        table = parse_tables(TABLE_WITH_NUMBERS)[0]
        assert table["types"][0] == "string"  # Product
        assert table["types"][1] == "currency"  # Price ($)
        assert table["types"][2] == "percentage"  # Discount (%)
        assert table["types"][3] == "integer"  # Stock

    def test_type_inference_date(self):
        table = parse_tables(TABLE_WITH_DATES)[0]
        assert table["types"][0] == "date"  # Date
        assert table["types"][1] == "string"  # Event

    def test_table_no_header(self):
        result = parse_tables(TABLE_NO_HEADER)
        assert len(result) == 1
        table = result[0]
        # Columns should be auto-generated
        assert table["columns"] == ["col_1", "col_2", "col_3"]
        assert table["total_rows"] == 3

    def test_empty_html(self):
        result = parse_tables("<html><body>No tables here</body></html>")
        assert result == []

    def test_multiple_tables(self):
        two_tables = SIMPLE_TABLE + TABLE_WITH_NUMBERS
        result = parse_tables(two_tables)
        assert len(result) == 2
        assert result[0]["columns"] == ["Name", "Age", "Email"]
        assert result[1]["columns"] == ["Product", "Price", "Discount", "Stock"]


# ── Test: Form Parsing ──────────────────────────────────────────────────────


class TestParseForms:
    def test_simple_form(self):
        result = parse_forms(SIMPLE_FORM)
        assert len(result) == 1
        form = result[0]
        assert form["action"] == "/login"
        assert form["method"] == "POST"
        assert form["field_count"] == 3  # email + password + submit button

    def test_form_fields(self):
        form = parse_forms(SIMPLE_FORM)[0]
        fields = form["fields"]

        # Email field
        email_field = fields[0]
        assert email_field["tag"] == "input"
        assert email_field["type"] == "email"
        assert email_field["name"] == "email"
        assert email_field["required"] is True
        assert email_field["placeholder"] == "you@example.com"

        # Password field
        pass_field = fields[1]
        assert pass_field["tag"] == "input"
        assert pass_field["type"] == "password"
        assert pass_field["required"] is True
        assert pass_field["minlength"] == "8"

        # Submit button
        submit_field = fields[2]
        assert submit_field["tag"] == "button"
        assert submit_field["type"] == "submit"
        assert submit_field["label"] == "Sign In"

    def test_form_with_select(self):
        form = parse_forms(FORM_WITH_SELECT)[0]
        fields = form["fields"]

        # Find select field
        select_fields = [f for f in fields if f["tag"] == "select"]
        assert len(select_fields) == 1
        select_field = select_fields[0]
        assert select_field["name"] == "country"
        assert select_field["required"] is True
        assert len(select_field["options"]) == 5  # 4 countries + placeholder
        assert select_field["options"][1] == {"value": "US", "label": "United States"}
        assert select_field["options"][4] == {"value": "FR", "label": "France"}

    def test_form_with_textarea(self):
        form = parse_forms(FORM_WITH_SELECT)[0]
        textarea_fields = [f for f in form["fields"] if f["tag"] == "textarea"]
        assert len(textarea_fields) == 1
        ta = textarea_fields[0]
        assert ta["name"] == "bio"
        assert ta["placeholder"] == "Tell us about yourself"
        assert ta["rows"] == "4"

    def test_form_with_checkbox(self):
        form = parse_forms(FORM_WITH_SELECT)[0]
        checkbox_fields = [f for f in form["fields"] if f["type"] == "checkbox"]
        assert len(checkbox_fields) == 1
        cb = checkbox_fields[0]
        assert cb["name"] == "newsletter"
        assert cb["value"] == "yes"

    def test_empty_form(self):
        result = parse_forms("<html><body>No forms</body></html>")
        assert result == []


# ── Test: List Parsing ──────────────────────────────────────────────────────


class TestParseLists:
    def test_unordered_list(self):
        result = parse_lists(UNORDERED_LIST)
        assert len(result) == 1
        lst = result[0]
        assert lst["type"] == "unordered"
        assert lst["items"] == ["Apples", "Bananas", "Cherries"]
        assert lst["total"] == 3

    def test_ordered_list(self):
        result = parse_lists(ORDERED_LIST)
        assert len(result) == 1
        lst = result[0]
        assert lst["type"] == "ordered"
        assert lst["items"] == ["First step", "Second step", "Third step", "Fourth step"]
        assert lst["total"] == 4

    def test_select_as_list(self):
        result = parse_lists(SELECT_MULTI)
        # Should not include selects with ≤2 options
        assert len(result) == 1
        lst = result[0]
        assert lst["type"] == "select"
        assert len(lst["items"]) == 4
        assert lst["total"] == 4

    def test_empty_list(self):
        result = parse_lists("<html><body>No lists</body></html>")
        assert result == []


# ── Test: Page-level ─────────────────────────────────────────────────────────


class TestParsePage:
    def test_parse_all(self):
        result = parse_page(MIXED_PAGE)
        assert "tables" in result
        assert "forms" in result
        assert "lists" in result
        assert len(result["tables"]) == 1
        assert len(result["forms"]) == 1
        assert len(result["lists"]) == 1

    def test_empty_page(self):
        result = parse_page("<html><head></head><body></body></html>")
        assert result == {"tables": [], "forms": [], "lists": []}


# ── Test: Edge cases / regressions ──────────────────────────────────────────


class TestEdgeCases:
    def test_no_duplicate_submit_input(self):
        html = '<form><input type="submit" value="Go"></form>'
        fields = parse_forms(html)[0]["fields"]
        submits = [f for f in fields if f.get("type") == "submit"]
        assert len(submits) == 1
        assert submits[0]["label"] == "Go"

    def test_nested_list_direct_children(self):
        html = '<ul><li>Parent<ul><li>Child</li></ul></li></ul>'
        result = parse_lists(html)
        assert len(result) == 2
        assert result[0]["items"] == ["Parent"]
        assert result[1]["items"] == ["Child"]

    def test_form_select_not_in_lists(self):
        html = """<form><select name="c">
          <option value="">x</option><option>a</option><option>b</option><option>c</option>
        </select></form>"""
        page = parse_page(html)
        assert len([f for f in page["forms"][0]["fields"] if f["tag"] == "select"]) == 1
        assert page["lists"] == []

    def test_checkbox_sibling_label(self):
        html = """<form>
          <input type="checkbox" name="newsletter" value="yes">
          <label>Subscribe to newsletter</label>
        </form>"""
        cb = [f for f in parse_forms(html)[0]["fields"] if f["type"] == "checkbox"][0]
        assert cb["label"] == "Subscribe to newsletter"

    def test_max_rows_truncation(self):
        rows = "".join(f"<tr><td>{i}</td></tr>" for i in range(100))
        html = f"<table><tbody>{rows}</tbody></table>"
        table = parse_tables(html, max_rows=10)[0]
        assert table["total_rows"] == 100
        assert len(table["rows"]) == 10
        assert table["rows_truncated"] is True
        assert table["rows_returned"] == 10


class TestExport:
    def test_export_json_records(self):
        records = export_table_json(SIMPLE_TABLE)
        assert records == [
            {"Name": "Alice", "Age": 30, "Email": "alice@test.com"},
            {"Name": "Bob", "Age": 25, "Email": "bob@test.com"},
            {"Name": "Charlie", "Age": 35, "Email": "charlie@test.com"},
        ]

    def test_export_csv(self):
        csv_out = export_table_csv(SIMPLE_TABLE)
        assert csv_out.splitlines()[0] == "Name,Age,Email"
        assert "Alice,30,alice@test.com" in csv_out

    def test_export_csv_escaped_commas(self):
        html = "<table><tr><th>A</th><th>B</th></tr><tr><td>hello, world</td><td>ok</td></tr></table>"
        csv_out = export_table_csv(html)
        assert '"hello, world"' in csv_out

    def test_export_table_index(self):
        two = SIMPLE_TABLE + TABLE_WITH_NUMBERS
        records = export_table_json(two, table_index=1)
        assert records[0]["Product"] == "Widget A"
        assert records[0]["Price"] == 19.99

    def test_export_no_tables(self):
        import pytest
        with pytest.raises(ValueError, match="No tables"):
            export_table_json("<html><body></body></html>")
