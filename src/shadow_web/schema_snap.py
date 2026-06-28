"""SchemaSnap — extract structured data schemas from HTML.

Turns HTML tables, forms, and lists into structured JSON-friendly dicts
(columns, types, fields, validation attrs). Pure lxml, no browser, no API keys.

Core functions:
  - parse_tables(html, max_rows=None) -> [{columns, rows, types, total_rows, ...}]
  - parse_forms(html)                 -> [{action, method, fields: [...]}]
  - parse_lists(html)               -> [{items, total, type}]
  - parse_page(html, max_rows=None)   -> {tables, forms, lists}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from lxml import html as lxml_html, etree


# -- Safe parser (shared across all functions, guards against XXE/network) ------

_SAFE_PARSER = lxml_html.HTMLParser(no_network=True)


def _parse_html(html: str):
    """Parse HTML string into lxml tree. Returns None for empty input."""
    if not html or not html.strip():
        return None
    return lxml_html.fromstring(html, parser=_SAFE_PARSER)


# -- Text helpers ---------------------------------------------------------------


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _text_or_empty(el) -> str:
    if el is None:
        return ""
    return _clean(el.text_content())


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


def _extract_table(table_el) -> Dict[str, Any]:
    """Parse a single <table> element into column schema + rows."""

    # -- Extract headers --
    thead = table_el.find(".//thead")
    columns: List[str] = []
    if thead is not None:
        for th in thead.iterfind(".//th"):
            columns.append(_text_or_empty(th))
    else:
        # Fallback: first <tr> that's not in <tbody>
        first_tr = table_el.find(".//tr")
        if first_tr is not None:
            parent = first_tr.getparent()
            if parent is not None and parent.tag != "tbody":
                for cell in first_tr.iterfind("th"):
                    columns.append(_text_or_empty(cell))
                if not columns:
                    for cell in first_tr.iterfind("td"):
                        columns.append(_text_or_empty(cell))

    # -- Extract rows (include <th> in body cells too) --
    rows: List[List[str]] = []
    tbody = table_el.find(".//tbody")
    if tbody is not None:
        tr_elements = list(tbody.iterfind(".//tr"))
    else:
        tr_elements = list(table_el.iterfind(".//tr"))
        if thead is not None:
            header_trs = set(thead.iterfind(".//tr"))
            tr_elements = [tr for tr in tr_elements if tr not in header_trs]

    for tr in tr_elements:
        row = []
        for cell in tr.xpath("./td | ./th"):
            row.append(_text_or_empty(cell))
        if row:
            rows.append(row)

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
    }


def parse_tables(html: str, max_rows: Optional[int] = None) -> List[Dict[str, Any]]:
    """Extract all tables from HTML as structured data.

    Args:
        html: Raw or compressed HTML.
        max_rows: Cap rows per table (None = no limit). When truncated,
            adds rows_truncated=True and rows_returned.

    Returns a list of table objects, each with:
      - columns, types, rows, total_rows, column_count
    """
    tree = _parse_html(html)
    if tree is None:
        return []
    return [_apply_max_rows(_extract_table(t), max_rows) for t in tree.xpath("//table")]


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
        "tables": [_apply_max_rows(_extract_table(t), max_rows) for t in tree.xpath("//table")],
        "forms": [_extract_form(f) for f in tree.xpath("//form")],
        "lists": _parse_lists_from_tree(tree),
    }
