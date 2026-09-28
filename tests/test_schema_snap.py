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

    def test_plain_header_row_is_not_exported_as_data(self):
        html = "<table><tr><th>Name</th><th>Age</th></tr><tr><td>Alice</td><td>30</td></tr></table>"
        table = parse_tables(html)[0]
        assert table["columns"] == ["Name", "Age"]
        assert table["rows"] == [["Alice", "30"]]
        assert table["total_rows"] == 1
        assert table["types"] == ["string", "integer"]

    def test_header_inside_tbody_keeps_column_names(self):
        html = """<table><tbody>
          <tr><th>Name</th><th>Age</th></tr>
          <tr><td>Alice</td><td>30</td></tr>
        </tbody></table>"""
        table = parse_tables(html)[0]
        assert table["columns"] == ["Name", "Age"]
        assert table["rows"] == [["Alice", "30"]]
        assert table["total_rows"] == 1

    def test_nested_table_rows_stay_in_nested_table(self):
        html = """<table><tbody><tr>
          <td>Outer</td>
          <td><table><tbody><tr><td>City</td><td>Berlin</td></tr></tbody></table></td>
        </tr></tbody></table>"""
        outer, inner = parse_tables(html)
        assert outer["total_rows"] == 1
        assert outer["rows"] == [["Outer", "City Berlin"]]
        assert ["City", "Berlin"] not in outer["rows"]
        assert inner["total_rows"] == 1
        assert inner["rows"] == [["City", "Berlin"]]

    def test_nested_table_without_tbody_adds_no_outer_rows(self):
        html = '<table><tr><td>Outer<table><tr><td>City</td><td>Berlin</td></tr></table></td></tr></table>'
        outer = parse_tables(html)[0]
        assert outer["total_rows"] == 1
        assert ["City", "Berlin"] not in outer["rows"]


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


class TestDegenerateInput:
    """Empty / whitespace / comment-only / None input must not raise."""

    def test_parse_tables_empty_and_whitespace(self):
        assert parse_tables("") == []
        assert parse_tables("   ") == []

    def test_parse_tables_comment_only(self):
        assert parse_tables("<!-- c -->") == []

    def test_parse_tables_none(self):
        assert parse_tables(None) == []

    def test_parse_page_and_others_none(self):
        assert parse_page(None) == {"tables": [], "forms": [], "lists": []}
        assert parse_forms(None) == []
        assert parse_lists(None) == []

    def test_parse_page_comment_only(self):
        assert parse_page("<!-- c -->") == {"tables": [], "forms": [], "lists": []}


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


# ── Test: ARIA grids (div-based admin UI grids) ──────────────────────────────

ARIA_GRID = """
<div role="table" aria-label="Employees">
  <div role="rowgroup">
    <div role="row">
      <div role="columnheader">Id</div>
      <div role="columnheader">Name</div>
      <div role="columnheader">Status</div>
    </div>
  </div>
  <div role="rowgroup">
    <div role="row">
      <div role="cell">0406</div>
      <div role="cell">Petraitis</div>
      <div role="cell">Active</div>
    </div>
    <div role="row">
      <div role="cell">0295</div>
      <div role="cell">Aaliyah</div>
      <div role="cell">Pending</div>
    </div>
  </div>
</div>
"""

ARIA_GRID_NO_HEADER = """
<div role="grid">
  <div role="row">
    <div role="gridcell">a1</div><div role="gridcell">a2</div>
  </div>
  <div role="row">
    <div role="gridcell">b1</div><div role="gridcell">b2</div>
  </div>
</div>
"""


class TestAriaGrids:
    def test_extracts_columns_and_rows(self):
        table = parse_tables(ARIA_GRID)[0]
        assert table["columns"] == ["Id", "Name", "Status"]
        assert table["rows"] == [["0406", "Petraitis", "Active"], ["0295", "Aaliyah", "Pending"]]
        assert table["total_rows"] == 2
        assert table["column_count"] == 3
        assert table["source"] == "aria_grid"

    def test_regular_table_is_tagged_with_its_own_plane(self):
        assert parse_tables(SIMPLE_TABLE)[0]["source"] == "table"

    def test_grid_without_header_gets_generated_names(self):
        table = parse_tables(ARIA_GRID_NO_HEADER)[0]
        assert table["columns"] == ["col_1", "col_2"]
        assert table["rows"] == [["a1", "a2"], ["b1", "b2"]]

    def test_infers_column_types(self):
        html = """
        <div role="table">
          <div role="row"><div role="columnheader">Qty</div><div role="columnheader">Price</div></div>
          <div role="row"><div role="cell">12</div><div role="cell">$9.99</div></div>
          <div role="row"><div role="cell">3</div><div role="cell">$19.99</div></div>
        </div>
        """
        table = parse_tables(html)[0]
        assert table["types"] == ["integer", "currency"]

    def test_nested_grid_rows_are_not_double_counted(self):
        html = """
        <div role="grid">
          <div role="row"><div role="columnheader">Outer</div></div>
          <div role="row"><div role="cell">outer-1</div></div>
          <div role="row">
            <div role="cell">
              <div role="grid">
                <div role="row"><div role="gridcell">inner-1</div></div>
              </div>
            </div>
          </div>
        </div>
        """
        tables = parse_tables(html)
        assert len(tables) == 1, "a grid nested in a grid belongs to the outer one"
        # The nested grid's text stays inside the outer cell (same as the <table>
        # plane); the guarantee is that no second table object is produced.
        assert [r[0] for r in tables[0]["rows"]] == ["outer-1", "inner-1"]

    def test_table_role_on_real_table_is_counted_once(self):
        html = '<table role="grid"><tr><th>A</th></tr><tr><td>1</td></tr></table>'
        tables = parse_tables(html)
        assert len(tables) == 1
        assert tables[0]["source"] == "table"

    def test_grid_inside_a_real_table_is_left_to_the_table_plane(self):
        html = """
        <table>
          <tr><th>Outer</th></tr>
          <tr><td>
            <div role="grid"><div role="row"><div role="gridcell">inner</div></div></div>
          </td></tr>
        </table>
        """
        tables = parse_tables(html)
        assert [t["source"] for t in tables] == ["table"]

    def test_parse_page_includes_aria_grids(self):
        page = parse_page(ARIA_GRID)
        assert len(page["tables"]) == 1
        assert page["tables"][0]["source"] == "aria_grid"

    def test_max_rows_applies_to_grids(self):
        table = parse_tables(ARIA_GRID, max_rows=1)[0]
        assert table["total_rows"] == 2
        assert table["rows_returned"] == 1
        assert table["rows_truncated"] is True

    def test_export_json_and_csv_work_on_grids(self):
        records = export_table_json(ARIA_GRID)
        assert records[0] == {"Id": "0406", "Name": "Petraitis", "Status": "Active"}
        csv_out = export_table_csv(ARIA_GRID)
        assert csv_out.splitlines()[0] == "Id,Name,Status"
        assert "0295,Aaliyah,Pending" in csv_out

    def test_empty_grid_yields_no_rows(self):
        table = parse_tables('<div role="table"></div>')[0]
        assert table["rows"] == []
        assert table["total_rows"] == 0

    def test_plain_divs_are_not_treated_as_tables(self):
        assert parse_tables("<div><div>just</div><div>layout</div></div>") == []


# ── Test: identifier fidelity (zero-padded ids are not numbers) ──────────────


class TestIdentifierFidelity:
    def test_zero_padded_ids_stay_strings(self):
        html = "<table><tr><th>Id</th></tr><tr><td>0406</td></tr><tr><td>0295</td></tr></table>"
        table = parse_tables(html)[0]
        assert table["types"] == ["string"]
        assert [r[0] for r in table["rows"]] == ["0406", "0295"]
        assert export_table_json(html)[0]["Id"] == "0406"

    def test_real_numbers_still_numeric(self):
        html = "<table><tr><th>Qty</th></tr><tr><td>12</td></tr><tr><td>0</td></tr><tr><td>3</td></tr></table>"
        table = parse_tables(html)[0]
        assert table["types"] == ["integer"]

    def test_decimals_below_one_still_numeric(self):
        html = "<table><tr><th>Rate</th></tr><tr><td>0.25</td></tr><tr><td>0.5</td></tr></table>"
        table = parse_tables(html)[0]
        assert table["types"] == ["number"]


# ── Test: hidden subtrees inside cells (sort menus, tooltips) ────────────────

HEADER_WITH_HIDDEN_MENU = """
<div role="table">
  <div role="row">
    <div role="columnheader">Id<div role="dropdown" style="display: none;">
      <ul role="menu"><li>Ascending</li><li>Descending</li></ul>
    </div></div>
    <div role="columnheader">Name<span hidden>sort</span></div>
  </div>
  <div role="row"><div role="cell">0406</div><div role="cell">Petraitis</div></div>
</div>
"""


class TestHiddenContent:
    def test_hidden_sort_menu_is_not_spliced_into_the_header(self):
        table = parse_tables(HEADER_WITH_HIDDEN_MENU)[0]
        assert table["columns"] == ["Id", "Name"]

    def test_hidden_attribute_excludes_text(self):
        table = parse_tables(HEADER_WITH_HIDDEN_MENU)[0]
        assert "sort" not in " ".join(table["columns"])

    def test_aria_hidden_is_excluded(self):
        html = """
        <div role="table">
          <div role="row"><div role="columnheader">Price</div></div>
          <div role="row"><div role="cell">$10<span aria-hidden="true">per unit</span></div></div>
        </div>
        """
        assert parse_tables(html)[0]["rows"] == [["$10"]]

    def test_hidden_text_excluded_in_plain_tables_too(self):
        html = """
        <table>
          <tr><th>City</th></tr>
          <tr><td>Berlin<div style="display:none">internal note</div></td></tr>
        </table>
        """
        assert parse_tables(html)[0]["rows"] == [["Berlin"]]

    def test_inline_parts_are_joined_with_a_space(self):
        html = "<table><tr><th>Name</th></tr><tr><td><b>Ada</b>Lovelace</td></tr></table>"
        assert parse_tables(html)[0]["rows"] == [["Ada Lovelace"]]
