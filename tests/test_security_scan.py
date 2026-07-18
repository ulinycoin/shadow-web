"""Unit tests for security_scan rule engine."""

from __future__ import annotations

from shadow_web.schema_snap import parse_forms
from shadow_web.security_scan import (
    analyze_cookies,
    analyze_forms,
    analyze_http_headers,
    analyze_links,
    analyze_surface,
    check_mixed_content,
    check_sri,
    extract_same_domain_links,
    normalize_header_map,
    render_markdown_report,
    summarize_report,
)

INSECURE_LOGIN_FORM = """
<form action="http://legacy.example.com/login" method="get">
  <input type="email" name="email" required>
  <input type="password" name="password">
  <button type="submit">Sign in</button>
</form>
"""

HTTP_PAGE_WITH_LINKS = """
<a href="http://cdn.example.com/app.js">CDN</a>
<a href="https://other.example.com/page">External</a>
<a href="/admin/dashboard">Admin</a>
"""


def test_form_password_get_is_critical():
    findings = analyze_forms("https://app.example.com/login", parse_forms(INSECURE_LOGIN_FORM))
    assert any(f.rule_id == "FORM_PASSWORD_GET" and f.severity == "critical" for f in findings)


def test_form_insecure_action_is_high():
    findings = analyze_forms("https://app.example.com/login", parse_forms(INSECURE_LOGIN_FORM))
    assert any(f.rule_id == "FORM_INSECURE_ACTION" and f.severity == "high" for f in findings)


def test_http_links_flagged():
    action_map = [
        {"id": "1", "type": "a", "label": "CDN asset", "href": "http://cdn.example.com/app.js"},
        {"id": "2", "type": "a", "label": "External docs", "href": "https://other.example.com/page"},
        {"id": "3", "type": "a", "label": "Admin dashboard", "href": "/admin/dashboard"},
    ]
    findings = analyze_links("https://app.example.com/", action_map)
    assert any(f.rule_id == "LINK_HTTP_RESOURCE" for f in findings)
    assert any(f.rule_id == "LINK_SENSITIVE_PATH" for f in findings)
    assert any(f.rule_id == "LINK_EXTERNAL" for f in findings)


def test_analyze_surface_integration():
    result = analyze_surface(
        "https://app.example.com/login",
        title="Login",
        clean_html=INSECURE_LOGIN_FORM,
        action_map=[],
    )
    assert result["form_count"] == 1
    assert result["finding_counts"]["critical"] >= 1


def test_extract_same_domain_links():
    action_map = [
        {"id": "1", "type": "a", "label": "Pricing", "href": "/pricing"},
        {"id": "2", "type": "a", "label": "Other", "href": "https://evil.example.org/x"},
        {"id": "3", "type": "a", "label": "Home", "href": "https://app.example.com/"},
    ]
    links = extract_same_domain_links("https://app.example.com/", action_map)
    assert "https://app.example.com/pricing" in links
    assert all("evil.example.org" not in link for link in links)


def test_markdown_report_renders():
    pages = [
        {
            "url": "https://app.example.com/",
            "title": "App",
            "page_class": "Static",
            "page_class_reason": "ok",
            "action_count": 2,
            "form_count": 0,
            "finding_counts": {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0},
            "findings": [
                {
                    "severity": "high",
                    "rule_id": "LINK_HTTP_RESOURCE",
                    "title": "Insecure HTTP links",
                    "detail": "sample",
                    "evidence": {},
                }
            ],
        }
    ]
    report = {
        "generated_at": "2026-01-01T00:00:00Z",
        "pages": pages,
        "summary": summarize_report(pages),
    }
    md = render_markdown_report(report)
    assert "Attack Surface Security Scan" in md
    assert "LINK_HTTP_RESOURCE" in md


LOCALPDF_HEADERS = normalize_header_map({
    "strict-transport-security": "max-age=63072000",
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'; script-src 'self' 'unsafe-inline'",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "access-control-allow-origin": "*",
})


def test_analyze_http_headers_localpdf_like():
    findings = analyze_http_headers(
        "https://localpdf.online/",
        LOCALPDF_HEADERS,
        http_probe_status=301,
        http_probe_location="https://localpdf.online/",
    )
    rules = {f.rule_id for f in findings}
    assert "HEADER_MISSING_HSTS" not in rules
    assert "HEADER_MISSING_CSP" not in rules
    assert "HEADER_CORS_WILDCARD" in rules
    assert "HEADER_CSP_UNSAFE" in rules


def test_analyze_http_headers_missing_basics():
    findings = analyze_http_headers("https://insecure.example.com/", {})
    rules = {f.rule_id for f in findings}
    assert "HEADER_MISSING_HSTS" in rules
    assert "HEADER_MISSING_CSP" in rules
    assert "HEADER_MISSING_NOSNIFF" in rules


def test_analyze_surface_with_headers():
    result = analyze_surface(
        "https://localpdf.online/",
        clean_html="",
        action_map=[],
        http_headers=LOCALPDF_HEADERS,
        http_probe_status=301,
        http_probe_location="https://localpdf.online/",
    )
    assert result["http_headers"]["strict-transport-security"] is not None
    assert "HEADER_MISSING_HSTS" not in {f["rule_id"] for f in result["findings"]}


def test_analyze_cookies_insecure_session():
    cookies = [
        {
            "name": "session_id",
            "domain": "app.example.com",
            "path": "/",
            "secure": False,
            "httpOnly": False,
            "sameSite": "",
            "expires": -1,
        }
    ]
    findings = analyze_cookies("https://app.example.com/", cookies)
    rules = {f.rule_id for f in findings}
    assert "COOKIE_MISSING_SECURE" in rules
    assert "COOKIE_MISSING_HTTPONLY" in rules
    assert "COOKIE_MISSING_SAMESITE" in rules


def test_analyze_cookies_samesite_none_requires_secure():
    cookies = [
        {
            "name": "tracking",
            "domain": "app.example.com",
            "path": "/",
            "secure": False,
            "httpOnly": False,
            "sameSite": "None",
        }
    ]
    findings = analyze_cookies("https://app.example.com/", cookies)
    assert any(f.rule_id == "COOKIE_SAMESITE_NONE_INSECURE" for f in findings)


def test_analyze_cookies_third_party_info():
    cookies = [
        {
            "name": "_ga",
            "domain": ".google.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "Lax",
        }
    ]
    findings = analyze_cookies("https://app.example.com/", cookies)
    assert any(f.rule_id == "COOKIE_THIRD_PARTY" for f in findings)


def test_analyze_surface_with_cookies():
    result = analyze_surface(
        "https://app.example.com/",
        clean_html="",
        action_map=[],
        cookies=[
            {
                "name": "auth_token",
                "domain": "app.example.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Strict",
            }
        ],
    )
    assert result["cookie_count"] == 1
    assert result["cookies"][0]["name"] == "auth_token"
    assert "COOKIE_MISSING_SECURE" not in {f["rule_id"] for f in result["findings"]}


# ── SRI tests ────────────────────────────────────────────────────────

SRI_HTML_WITH_MISSING = """\
<html><head>
<script src="https://cdn.example.com/app.js"></script>
<script src="https://cdn.example.com/lib.js" integrity="sha384-abc123"></script>
<link rel="stylesheet" href="https://fonts.example.com/font.css">
<link rel="stylesheet" href="https://cdn.example.com/theme.css" integrity="sha384-def456">
</head></html>
"""

SRI_HTML_ALL_COVERED = """\
<html><head>
<script src="https://cdn.example.com/app.js" integrity="sha384-abc123"></script>
<link rel="stylesheet" href="https://cdn.example.com/theme.css" integrity="sha384-def456">
</head></html>
"""


def test_sri_missing_integrity():
    findings = check_sri(SRI_HTML_WITH_MISSING)
    sri_rules = [f for f in findings if f.rule_id == "SRI_MISSING"]
    assert len(sri_rules) == 2
    urls = {f.evidence["url"] for f in sri_rules}
    assert "https://cdn.example.com/app.js" in urls
    assert "https://fonts.example.com/font.css" in urls


def test_sri_all_covered():
    findings = check_sri(SRI_HTML_ALL_COVERED)
    assert not any(f.rule_id == "SRI_MISSING" for f in findings)


def test_sri_empty_html():
    assert check_sri("") == []


# ── Mixed content tests ──────────────────────────────────────────────

MIXED_HTML = """\
<html><body>
<script src="http://cdn.example.com/app.js"></script>
<script src="https://safe.example.com/lib.js"></script>
<iframe src="http://tracker.example.com/frame"></iframe>
<img src="http://static.example.com/pic.jpg">
<img src="https://safe.example.com/pic.jpg">
<link rel="stylesheet" href="http://fonts.example.com/font.css">
<video src="http://media.example.com/clip.mp4"></video>
<object data="http://plugin.example.com/widget"></object>
</body></html>
"""


def test_mixed_content_on_https():
    findings = check_mixed_content("https://example.com/page", MIXED_HTML)
    assert len(findings) == 6  # script, iframe, img, link, video, object
    rules = {f.rule_id for f in findings}
    assert "MIXED_ACTIVE_SCRIPT" in rules
    assert "MIXED_ACTIVE_IFRAME" in rules
    assert "MIXED_ACTIVE_STYLESHEET" in rules
    assert "MIXED_PASSIVE_IMAGE" in rules
    assert "MIXED_PASSIVE_VIDEO" in rules
    assert "MIXED_PASSIVE_OBJECT" in rules


def test_mixed_content_on_http_returns_nothing():
    findings = check_mixed_content("http://example.com/page", MIXED_HTML)
    assert findings == []


def test_mixed_content_none_found():
    safe_html = """\
<html><body>
<script src="https://cdn.example.com/app.js"></script>
<img src="https://static.example.com/pic.jpg">
</body></html>
"""
    findings = check_mixed_content("https://example.com/page", safe_html)
    assert findings == []


def test_mixed_content_integration():
    result = analyze_surface(
        "https://example.com/login",
        title="Login",
        clean_html=MIXED_HTML,
        action_map=[],
    )
    mixed_rules = {f["rule_id"] for f in result["findings"]}
    assert "MIXED_ACTIVE_SCRIPT" in mixed_rules
    assert "MIXED_PASSIVE_IMAGE" in mixed_rules


# ── CORS preflight (unit-level: logic only, no HTTP) ─────────────────

def test_cors_in_surface_skipped_when_no_url():
    """CORS probe is not called when cors_probe_url is omitted."""
    result = analyze_surface(
        "https://example.com/",
        clean_html="",
        action_map=[],
    )
    assert "CORS_ORIGIN_ECHO" not in {f["rule_id"] for f in result["findings"]}
    assert "CORS_WILDCARD_WITH_CREDENTIALS" not in {f["rule_id"] for f in result["findings"]}
