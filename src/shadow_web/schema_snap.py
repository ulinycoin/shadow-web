"""SchemaSnap — extract structured data schemas from HTML.

Turns HTML tables, forms, and lists into structured JSON-friendly dicts
(columns, types, fields, validation attrs). Pure lxml, no browser, no API keys.

Tables come from two planes, both tagged with `source`:
  - "table"     — real <table> elements (incl. nested-table row isolation)
  - "aria_grid" — div-based grids used by modern admin UIs:
                  role=table/grid > role=row > role=columnheader/cell/gridcell

Core functions:
  - parse_tables(html, max_rows=None) -> [{columns, rows, types, total_rows, source, ...}]
  - parse_forms(html)                 -> [{action, method, fields: [...]}]
  - parse_lists(html)               -> [{items, total, type}]
  - parse_page(html, max_rows=None)   -> {tables, forms, lists}
  - export_table_json(html, ...)      -> [{column: value, ...}, ...]
  - export_table_csv(html, ...)       -> "col1,col2\\nval1,val2\\n..."
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional, Union
from lxml import etree
from lxml import html as lxml_html


# -- Safe parser (shared across all functions, guards against XXE/network) ------

_SAFE_PARSER = lxml_html.HTMLParser(no_network=True)


def _parse_html(html: Optional[str]):
    """Parse HTML string into lxml tree. Returns None for empty input.

    Empty, whitespace-only, comment-only and ``None`` input all return None
    instead of raising, so callers degrade to an empty result.
    """
    if html is None or not html.strip():
        return None
    try:
        return lxml_html.fromstring(html, parser=_SAFE_PARSER)
    except (etree.ParserError, ValueError):
        return None


# -- Text helpers ---------------------------------------------------------------


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _text_or_empty(el) -> str:
    if el is None:
        return ""
    return _clean(el.text_content())


# Inline markers of a hidden subtree. Admin UIs routinely park menus, tooltips
# and sort dropdowns inside a header/body cell with display:none, and
# text_content() would otherwise splice that hidden text into the value
# ("Id" + "Ascending" + "Descending" -> "IdAscendingDescending").
_HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)


def _is_hidden(el) -> bool:
    if el.get("hidden") is not None:
        return True
    if (el.get("aria-hidden") or "").lower() == "true":
        return True
    return bool(_HIDDEN_STYLE_RE.search(el.get("style") or ""))


def _visible_text(el) -> str:
    """Cell text with hidden subtrees dropped and parts joined by a space."""
    if el is None:
        return ""
    parts: List[str] = []

    def walk(node) -> None:
        if node.text:
            parts.append(node.text)
        for child in node.iterchildren():
            if isinstance(child.tag, str) and not _is_hidden(child):
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return _clean(" ".join(parts))


def _li_item_text(li_el) -> str:
    """Text of a single <li>, excluding nested <ul>/<ol> content."""
    parts: List[str] = []
    if li_el.text:
        parts.append(li_el.text)
    for child in li_el.iterchildren():
        if child.tag in ("ul", "ol"):
            break
        parts.append(_text_or_empty(child))
        if child.tail:
            parts.append(child.tail)
    return _clean(" ".join(parts))


def _apply_max_rows(table: Dict[str, Any], max_rows: Optional[int]) -> Dict[str, Any]:
    if max_rows is None or table["total_rows"] <= max_rows:
        return table
    return {
        **table,
        "rows": table["rows"][:max_rows],
        "rows_returned": max_rows,
        "rows_truncated": True,
    }


# -- Table type inference -------------------------------------------------------


def _infer_column_type(values: List[str]) -> str:
    """Guess column data type from a sample of values."""
    non_empty = [v for v in values if v and v != "\u2014" and v != "-"]
    if not non_empty:
        return "string"

    # Sample first 20 non-empty values
    sample = non_empty[:20]
    total = len(sample)

    numeric = 0
    date_like = 0
    email_like = 0
    url_like = 0
    has_currency = False
    has_percent = False
    all_int = True

    for val in sample:
        val = val.strip()

        # Currency / percent flags
        if any(c in val for c in ("$", "\u20ac", "\u00a3")):
            has_currency = True
        if "%" in val:
            has_percent = True

        # Try numeric
        cleaned = val.replace(",", "").replace("$", "").replace("\u20ac", "").replace("\u00a3", "").replace("%", "").strip()
        # Zero-padded digit runs are identifiers, not numbers: employee/SKU/order
        # ids like "0406" or "007" must not be coerced to 406 / 7 downstream.
        if re.fullmatch(r"0\d+", cleaned):
            all_int = False
            continue
        try:
            float(cleaned)
            numeric += 1
            if "." in cleaned:
                all_int = False
        except ValueError:
            all_int = False
            # Date (ISO, US, EU)
            if re.match(r"\d{4}-\d{2}-\d{2}", val) or re.match(r"\d{2}[/-]\d{2}[/-]\d{4}", val):
                date_like += 1
                continue
            # Email
            if "@" in val and "." in val.split("@")[-1]:
                email_like += 1
                continue
            # URL
            if val.startswith("http://") or val.startswith("https://"):
                url_like += 1
                continue

    if numeric / total >= 0.6:
        if has_currency:
            return "currency"
        if has_percent:
            return "percentage"
        if all_int:
            return "integer"
        return "number"
    if date_like / total >= 0.6:
        return "date"
    if email_like / total >= 0.6:
        return "email"
    if url_like / total >= 0.6:
        return "url"
    return "string"


# -- Table extraction -----------------------------------------------------------


def _own_table_rows(table_el) -> List[Any]:
    """Rows owned by this table, excluding rows of nested <table> elements."""
    rows = []
    for tr in table_el.iter("tr"):
        ancestor = tr.getparent()
        nested = False
        while ancestor is not None and ancestor is not table_el:
            if ancestor.tag == "table":
                nested = True
                break
            ancestor = ancestor.getparent()
        if not nested:
            rows.append(tr)
    return rows


def _finalize_table(columns: List[str], rows: List[List[str]], source: str) -> Dict[str, Any]:
    """Shared shape for every extraction plane: normalize columns, infer types.

    `source` records which plane produced the table ("table" or "aria_grid") so a
    value can be traced back to how it was read.
    """
    # -- Normalize column count (max across ALL rows, not just first) --
    max_row_len = max(len(row) for row in rows) if rows else 0
    max_cols = max(max_row_len, len(columns))
    if columns:
        while len(columns) < max_cols:
            columns.append(f"column_{len(columns) + 1}")

    # -- Auto-generate column names if table has none --
    if not columns and rows:
        columns = [f"col_{i + 1}" for i in range(max_cols)]

    # -- Infer types per column --
    types: List[str] = []
    if columns and rows:
        for col_idx in range(len(columns)):
            col_values = [row[col_idx] if col_idx < len(row) else "" for row in rows]
            types.append(_infer_column_type(col_values))
    else:
        types = ["string"] * len(columns)

    return {
        "columns": columns,
        "types": types,
        "rows": rows,
        "total_rows": len(rows),
        "column_count": len(columns),
        "source": source,
    }


def _extract_table(table_el) -> Dict[str, Any]:
    """Parse a single <table> element into column schema + rows."""

    own_rows = _own_table_rows(table_el)

    # -- Extract headers: first own <tr> containing <th> (thead or tbody) --
    columns: List[str] = []
    header_tr = None
    for tr in own_rows:
        th_cells = tr.xpath("./th")
        if th_cells:
            header_tr = tr
            columns = [_visible_text(cell) for cell in th_cells]
            break

    # Legacy fallback: a leading <tr> outside <tbody> names the columns
    if header_tr is None and own_rows:
        first_tr = own_rows[0]
        parent = first_tr.getparent()
        if parent is not None and parent.tag not in ("tbody", "tfoot"):
            columns = [_visible_text(cell) for cell in first_tr.xpath("./td")]

    # -- Extract rows (the header row and any <thead> row are never data) --
    thead = table_el.find("./thead")
    excluded_rows = set(thead.iter("tr")) if thead is not None else set()
    if header_tr is not None:
        excluded_rows.add(header_tr)

    rows: List[List[str]] = []
    for tr in own_rows:
        if tr in excluded_rows:
            continue
        row = []
        for cell in tr.xpath("./td | ./th"):
            row.append(_visible_text(cell))
        if row:
            rows.append(row)

    return _finalize_table(columns, rows, source="table")


# -- ARIA grid extraction -------------------------------------------------------
#
# Modern admin/back-office UIs (Vue, React, Angular) ship div-based grids marked
# up with ARIA roles instead of <table>: role=table/grid wrapping role=row
# children whose role=columnheader / role=cell (or gridcell / rowheader) children
# hold the data. XPath '//table' sees none of it, so every consumer used to have
# to bring its own extractor.

_ARIA_GRID_ROLES = frozenset({"table", "grid"})
_ARIA_ROW_ROLES = frozenset({"row"})
_ARIA_HEADER_ROLES = frozenset({"columnheader"})
_ARIA_CELL_ROLES = frozenset({"cell", "gridcell", "rowheader"})


def _is_aria_grid(el) -> bool:
    return el.get("role") in _ARIA_GRID_ROLES


def _aria_grid_ancestor(el):
    """Nearest ancestor with an ARIA grid role, or None."""
    for ancestor in el.iterancestors():
        if _is_aria_grid(ancestor):
            return ancestor
    return None


def _inside_table(el) -> bool:
    return any(ancestor.tag == "table" for ancestor in el.iterancestors())


def _belongs_to(el, root) -> bool:
    """True when `el` belongs to `root` and not to a nested grid/table/row."""
    for ancestor in el.iterancestors():
        if ancestor is root:
            return True
        if _is_aria_grid(ancestor) or ancestor.tag == "table":
            return False
        if ancestor.get("role") in _ARIA_ROW_ROLES:
            return False
    return False


def _aria_row_cells(row_el) -> List[Any]:
    cells = []
    for cell in row_el.iter():
        if cell is row_el:
            continue
        role = cell.get("role")
        if not (
            role in _ARIA_CELL_ROLES
            or role in _ARIA_HEADER_ROLES
            or cell.tag in ("td", "th")
        ):
            continue
        if not _belongs_to(cell, row_el):
            continue
        cells.append(cell)
    return cells


def _extract_aria_grid(grid_el) -> Dict[str, Any]:
    """Parse one ARIA grid into the same shape as a <table>.

    Header cells are taken from the first row that carries role=columnheader (or
    a real <th>). When a grid has no header row the columns are named col_N
    rather than silently promoting the first data row to a header.
    """
    row_els = [
        el
        for el in grid_el.iter()
        if el is not grid_el and el.get("role") in _ARIA_ROW_ROLES and _belongs_to(el, grid_el)
    ]

    columns: List[str] = []
    header_row = None
    for row_el in row_els:
        cells = _aria_row_cells(row_el)
        if any(c.get("role") in _ARIA_HEADER_ROLES or c.tag == "th" for c in cells):
            header_row = row_el
            columns = [_visible_text(cell) for cell in cells]
            break

    rows: List[List[str]] = []
    for row_el in row_els:
        if row_el is header_row:
            continue
        cells = _aria_row_cells(row_el)
        if not cells:
            continue
        row = [_visible_text(cell) for cell in cells]
        if any(value for value in row):
            rows.append(row)

    return _finalize_table(columns, rows, source="aria_grid")


def _iter_table_roots(tree) -> List[Any]:
    """Table-like roots in document order: every <table> plus ARIA grids.

    An ARIA grid nested inside another grid (or inside a <table>) is skipped:
    its rows belong to the outer container and would otherwise be reported twice.
    Nested <table> elements are still yielded, matching the previous behaviour
    where `_own_table_rows` keeps their rows out of the outer table.
    """
    roots = []
    for el in tree.xpath("//table | //*[@role='table' or @role='grid']"):
        if el.tag == "table":
            roots.append(el)
            continue
        if _inside_table(el) or _aria_grid_ancestor(el) is not None:
            continue
        roots.append(el)
    return roots


def _extract_table_root(el) -> Dict[str, Any]:
    if el.tag == "table":
        return _extract_table(el)
    return _extract_aria_grid(el)


def parse_tables(html: str, max_rows: Optional[int] = None) -> List[Dict[str, Any]]:
    """Extract all tables from HTML as structured data.

    Covers both planes: real <table> elements and div-based ARIA grids
    (role=table/grid + role=row/columnheader/cell) that modern admin UIs ship.
    Each result carries `source` ("table" or "aria_grid") naming the plane.

    Args:
        html: Raw or compressed HTML.
        max_rows: Cap rows per table (None = no limit). When truncated,
            adds rows_truncated=True and rows_returned.

    Returns a list of table objects, each with:
      - columns, types, rows, total_rows, column_count, source
    """
    tree = _parse_html(html)
    if tree is None:
        return []
    return [_apply_max_rows(_extract_table_root(t), max_rows) for t in _iter_table_roots(tree)]


# -- Form parsing ---------------------------------------------------------------


_INPUT_TYPES = {
    "text", "email", "password", "number", "tel", "url",
    "date", "datetime-local", "time", "month", "week",
    "checkbox", "radio", "file", "hidden", "search", "color",
    "range", "submit", "image",
}

_INPUT_ATTRS_TO_KEEP = {
    "name", "type", "placeholder", "required", "pattern",
    "minlength", "maxlength", "min", "max", "step",
    "autocomplete", "readonly", "disabled", "value",
}

_SELECT_ATTRS_TO_KEEP = {
    "name", "required", "multiple", "disabled",
}

_TEXTAREA_ATTRS_TO_KEEP = {
    "name", "placeholder", "required", "rows", "cols",
    "maxlength", "readonly", "disabled",
}


def _parse_field_attrs(el, keep: set) -> Dict[str, Any]:
    field = {}
    for attr in keep:
        val = el.get(attr)
        if val is not None:
            field[attr] = val
    return field


def _extract_label(form_el, field_el) -> str:
    """Try to find a label for a form field. Does NOT mutate the DOM."""
    field_id = field_el.get("id")
    if field_id:
        # Use parameterized XPath to avoid injection
        label_els = form_el.xpath(".//label[@for=$fid]", fid=field_id)
        if label_els:
            text = _text_or_empty(label_els[0])
            if text:
                return text

    # Check if field is wrapped in <label> -- collect text without the field itself
    parent = field_el.getparent()
    while parent is not None:
        if parent.tag == "label":
            parts: List[str] = []
            if parent.text:
                parts.append(parent.text)
            for child in parent.iterchildren():
                tail = (child.tail or "").strip()
                if child != field_el:
                    parts.append(_text_or_empty(child) + " " + tail)
                elif tail:
                    parts.append(tail)
            cleaned = _clean(" ".join(parts))
            if cleaned:
                return cleaned
        parent = parent.getparent()

    # Sibling <label> (e.g. checkbox then <label>Subscribe</label>)
    parent = field_el.getparent()
    if parent is not None:
        for sibling in parent.iterchildren():
            if sibling.tag == "label" and sibling is not field_el:
                text = _text_or_empty(sibling)
                if text:
                    return text

    # Fallback: placeholder or name
    placeholder = _clean(field_el.get("placeholder"))
    if placeholder:
        return placeholder
    name = _clean(field_el.get("name"))
    if name:
        return name.replace("_", " ").replace("-", " ").capitalize()

    return ""


def _extract_form(form_el) -> Dict[str, Any]:
    """Parse a single <form> element into field schema."""
    form: Dict[str, Any] = {
        "action": _clean(form_el.get("action")) or None,
        "method": (form_el.get("method") or "get").upper(),
    }
    fields: List[Dict[str, Any]] = []

    # Parse <input> elements
    for input_el in form_el.iterfind(".//input"):
        field = _parse_field_attrs(input_el, _INPUT_ATTRS_TO_KEEP)
        field["tag"] = "input"
        field_type = input_el.get("type", "text")
        if field_type not in _INPUT_TYPES:
            field_type = "text"
        field["type"] = field_type
        field["label"] = _extract_label(form_el, input_el)
        if "required" in field:
            field["required"] = True
        if "disabled" in field:
            field["disabled"] = True
        if field_type == "submit":
            continue
        fields.append(field)

    # Parse <select> elements
    for select_el in form_el.iterfind(".//select"):
        field = _parse_field_attrs(select_el, _SELECT_ATTRS_TO_KEEP)
        field["tag"] = "select"
        field["type"] = "select"
        field["label"] = _extract_label(form_el, select_el)
        options = []
        for opt in select_el.iterfind(".//option"):
            opt_val = opt.get("value") or ""
            opt_label = _text_or_empty(opt) or opt_val
            options.append({"value": opt_val, "label": opt_label})
        if options:
            field["options"] = options
        if "required" in field:
            field["required"] = True
        if "disabled" in field:
            field["disabled"] = True
        fields.append(field)

    # Parse <textarea> elements
    for ta_el in form_el.iterfind(".//textarea"):
        field = _parse_field_attrs(ta_el, _TEXTAREA_ATTRS_TO_KEEP)
        field["tag"] = "textarea"
        field["type"] = "textarea"
        field["label"] = _extract_label(form_el, ta_el)
        if "required" in field:
            field["required"] = True
        if "disabled" in field:
            field["disabled"] = True
        fields.append(field)

    # Parse submit buttons
    for btn_el in form_el.iterfind(".//button"):
        btn_type = btn_el.get("type", "submit")
        if btn_type == "submit":
            fields.append({
                "tag": "button",
                "type": "submit",
                "label": _text_or_empty(btn_el),
            })

    # Also catch <input type="submit">
    for submit_el in form_el.iterfind('.//input[@type="submit"]'):
        fields.append({
            "tag": "input",
            "type": "submit",
            "label": submit_el.get("value", "Submit").strip(),
        })

    form["fields"] = fields
    form["field_count"] = len(fields)
    return form


def parse_forms(html: str) -> List[Dict[str, Any]]:
    """Extract all forms from HTML as structured field schemas.

    Returns a list of form objects, each with:
      - action: form action URL
      - method: GET/POST
      - fields: list of field objects (name, type, label, required, options, etc.)
      - field_count: number of fields
    """
    tree = _parse_html(html)
    if tree is None:
        return []
    return [_extract_form(f) for f in tree.xpath("//form")]


# -- List parsing ---------------------------------------------------------------


def _parse_lists_from_tree(tree) -> List[Dict[str, Any]]:
    """Extract lists from a pre-parsed lxml tree."""
    results: List[Dict[str, Any]] = []

    # Parse <ul> and <ol> (direct <li> children only)
    for tag, list_type in [("ul", "unordered"), ("ol", "ordered")]:
        for list_el in tree.xpath(f"//{tag}"):
            items = []
            for li in list_el.xpath("./li"):
                text = _li_item_text(li)
                if text:
                    items.append(text)
            if items:
                results.append({
                    "type": list_type,
                    "items": items,
                    "total": len(items),
                })

    # Standalone <select> lists (skip selects inside <form> — covered by parse_forms)
    for select_el in tree.xpath("//select[not(ancestor::form)]"):
        options = []
        for opt in select_el.iterfind(".//option"):
            val = opt.get("value") or ""
            label = _text_or_empty(opt) or val
            options.append({"value": val, "label": label})
        if len(options) > 2:
            results.append({
                "type": "select",
                "items": options,
                "total": len(options),
            })

    return results


def parse_lists(html: str) -> List[Dict[str, Any]]:
    """Extract lists (<ul>, <ol>, <select>) from HTML.

    Returns a list of list objects:
      - type: unordered | ordered | select
      - items: list of item texts (or {value, label} for select)
      - total: item count
    """
    tree = _parse_html(html)
    if tree is None:
        return []
    return _parse_lists_from_tree(tree)


# -- High-level -----------------------------------------------------------------


def parse_page(html: str, max_rows: Optional[int] = None) -> Dict[str, Any]:
    """Extract all structured data (tables, forms, lists) from HTML.

    Args:
        html: Raw or compressed HTML.
        max_rows: Cap rows per table (None = no limit).

    Returns a dict with keys: tables, forms, lists.
    Parses HTML only once internally.
    """
    tree = _parse_html(html)
    if tree is None:
        return {"tables": [], "forms": [], "lists": []}
    return {
        "tables": [_apply_max_rows(_extract_table_root(t), max_rows) for t in _iter_table_roots(tree)],
        "forms": [_extract_form(f) for f in tree.xpath("//form")],
        "lists": _parse_lists_from_tree(tree),
    }


# -- Export (CSV / JSON records) ------------------------------------------------


def _coerce_cell(value: str, col_type: str) -> Union[str, int, float, None]:
    if not value or value in ("\u2014", "-"):
        return None
    if col_type == "integer":
        try:
            return int(value.replace(",", "").strip())
        except ValueError:
            return value
    if col_type in ("number", "currency", "percentage"):
        cleaned = (
            value.replace(",", "")
            .replace("$", "")
            .replace("\u20ac", "")
            .replace("\u00a3", "")
            .replace("%", "")
            .strip()
        )
        try:
            num = float(cleaned)
            return int(num) if num.is_integer() and col_type != "number" else num
        except ValueError:
            return value
    return value


def table_to_records(table: Dict[str, Any], coerce_types: bool = True) -> List[Dict[str, Any]]:
    """Convert a parsed table dict to a list of row objects keyed by column name."""
    columns = table.get("columns") or []
    types = table.get("types") or ["string"] * len(columns)
    records: List[Dict[str, Any]] = []
    for row in table.get("rows") or []:
        record: Dict[str, Any] = {}
        for idx, col in enumerate(columns):
            raw = row[idx] if idx < len(row) else ""
            if coerce_types and idx < len(types):
                record[col] = _coerce_cell(raw, types[idx])
            else:
                record[col] = raw
        records.append(record)
    return records


def table_to_csv(table: Dict[str, Any]) -> str:
    """Convert a parsed table dict to a CSV string (RFC-style quoting)."""
    columns = table.get("columns") or []
    if not columns:
        return ""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in table.get("rows") or []:
        writer.writerow([row[i] if i < len(row) else "" for i in range(len(columns))])
    return buf.getvalue().rstrip("\n")


def _pick_table(tables: List[Dict[str, Any]], table_index: int) -> Dict[str, Any]:
    if not tables:
        raise ValueError("No tables found in HTML")
    if table_index < 0 or table_index >= len(tables):
        raise IndexError(f"table_index {table_index} out of range (found {len(tables)} table(s))")
    return tables[table_index]


def export_table_json(
    html: str,
    table_index: int = 0,
    max_rows: Optional[int] = None,
    coerce_types: bool = True,
) -> List[Dict[str, Any]]:
    """Extract a table as JSON-ready records: [{Name: \"Alice\", Age: 30}, ...]."""
    tables = parse_tables(html, max_rows=max_rows)
    return table_to_records(_pick_table(tables, table_index), coerce_types=coerce_types)


def export_table_csv(
    html: str,
    table_index: int = 0,
    max_rows: Optional[int] = None,
) -> str:
    """Extract a table as CSV: \"Name,Age\\nAlice,30\\n...\"."""
    tables = parse_tables(html, max_rows=max_rows)
    return table_to_csv(_pick_table(tables, table_index))
