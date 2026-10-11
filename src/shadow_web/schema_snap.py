"""SchemaSnap extraction core, re-exported from the ``schemasnap`` package.

This module used to be a fork of ``schemasnap.schema_snap`` and drifted: the
two copies fixed different bugs (nested-table row isolation here, Tailwind and
pseudo-table detection there) and shared the rest, including the defects both
inherited. The implementation now lives in one place, the ``schemasnap``
distribution, and this module is a facade that keeps the import path, the
public function set and the legacy ``source`` vocabulary stable for the rest
of shadow-web.

Source names: the core reports ``native``/``aria``/``tailwind``/``pseudo``,
while consumers here have always seen ``table``/``aria_grid``. The facade
translates, so the core can be upgraded without touching callers.

Anything not re-exported explicitly (private helpers used by tests) is proxied
to the core through module ``__getattr__``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from schemasnap import schema_snap as _core
from schemasnap.schema_snap import (  # noqa: F401  (public surface)
    export_table_csv,
    export_table_json,
    parse_forms,
    parse_lists,
    table_to_csv,
    table_to_records,
)

#: core source name -> legacy name reported by shadow-web
_SOURCE_ALIASES = {"native": "table", "aria": "aria_grid"}


def _localize(table: Dict[str, Any]) -> Dict[str, Any]:
    """Rename ``source`` back to the vocabulary shadow-web callers expect."""
    legacy = _SOURCE_ALIASES.get(table.get("source"))
    if legacy is None:
        return table
    localized = dict(table)
    localized["source"] = legacy
    return localized


def _localize_all(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_localize(table) for table in tables]


def parse_tables(html: str, max_rows: Optional[int] = None) -> List[Dict[str, Any]]:
    """Extract tables from HTML. See ``schemasnap.schema_snap.parse_tables``."""
    return _localize_all(_core.parse_tables(html, max_rows=max_rows))


def parse_page(html: str, max_rows: Optional[int] = None) -> Dict[str, Any]:
    """Extract tables, forms and lists. See ``schemasnap.schema_snap.parse_page``."""
    page = _core.parse_page(html, max_rows=max_rows)
    return {**page, "tables": _localize_all(page.get("tables") or [])}


def __getattr__(name: str) -> Any:
    """Proxy private helpers (and future additions) to the core module."""
    return getattr(_core, name)
