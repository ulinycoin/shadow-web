from .compressor import process_html, generate_xml_map, generate_grouped_xml_map
from .dom_capture import capture_flattened_dom, FlattenResult, interact_by_binding
from .grouping import group_action_map
from .heal_local import HealCache, local_heal, HealResult
from .query import query_actions, shadow_grep, QueryResult
from .webmcp import (
    WebMcpSnapshot,
    WebMcpTool,
    detect_webmcp,
    execute_webmcp_tool,
    generate_webmcp_xml_map,
    webmcp_tools_to_action_map,
    webmcp_tools_terse,
)
from .diff import (
    PageDiff,
    PageSnapshot,
    build_snapshot,
    compute_page_diff,
    diff_terse,
    generate_diff_xml,
)
from .a11y_capture import (
    CaptureMode,
    capture_page,
    acapture_page,
    capture_a11y_interactive,
    merge_dom_and_a11y,
)
from .schema_snap import (
    parse_tables,
    parse_forms,
    parse_lists,
    parse_page,
    table_to_records,
    table_to_csv,
    export_table_json,
    export_table_csv,
)
from .form_fill import (
    build_form_fill_plan,
    execute_form_fill_plan,
    execute_form_fill_plan_async,
    execute_form_fill_plan_multi_step,
    execute_form_fill_plan_multi_step_async,
    link_form_to_actions,
    plan_from_dict,
    plan_from_session,
    validate_profile,
    FormFillPlan,
)
from .verified_heal import verify_selector_on_page, verify_selector_in_html, averify_selector_on_page
from .wrapper import ShadowPage
from typing import Any

# Lazy loading of browser_use adapter to avoid eager import errors when browser-use is not installed
_LAZY_EXPORTS = {
    "AsyncShadowPage": ".browser_use",
    "ShadowTools": ".browser_use",
    "HAS_BROWSER_USE": ".browser_use",
}

def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        import importlib
        module_name = _LAZY_EXPORTS[name]
        # Resolve relative import
        module = importlib.import_module(module_name, __package__)
        val = getattr(module, name)
        # Cache it in globals to avoid looking it up again
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "process_html",
    "generate_xml_map",
    "generate_grouped_xml_map",
    "capture_flattened_dom",
    "FlattenResult",
    "interact_by_binding",
    "group_action_map",
    "HealCache",
    "local_heal",
    "HealResult",
    "query_actions",
    "shadow_grep",
    "QueryResult",
    "WebMcpSnapshot",
    "WebMcpTool",
    "detect_webmcp",
    "execute_webmcp_tool",
    "generate_webmcp_xml_map",
    "webmcp_tools_to_action_map",
    "webmcp_tools_terse",
    "PageDiff",
    "PageSnapshot",
    "build_snapshot",
    "compute_page_diff",
    "diff_terse",
    "generate_diff_xml",
    "CaptureMode",
    "capture_page",
    "acapture_page",
    "capture_a11y_interactive",
    "merge_dom_and_a11y",
    "parse_tables",
    "parse_forms",
    "parse_lists",
    "parse_page",
    "table_to_records",
    "table_to_csv",
    "export_table_json",
    "export_table_csv",
    "build_form_fill_plan",
    "execute_form_fill_plan",
    "execute_form_fill_plan_async",
    "execute_form_fill_plan_multi_step",
    "execute_form_fill_plan_multi_step_async",
    "link_form_to_actions",
    "plan_from_dict",
    "plan_from_session",
    "validate_profile",
    "FormFillPlan",
    "verify_selector_on_page",
    "verify_selector_in_html",
    "averify_selector_on_page",
    "ShadowPage",
    "AsyncShadowPage",
    "ShadowTools",
    "HAS_BROWSER_USE",
]
