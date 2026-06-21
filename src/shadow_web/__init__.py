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
from .verified_heal import verify_selector_on_page, verify_selector_in_html, averify_selector_on_page
from .wrapper import ShadowPage
