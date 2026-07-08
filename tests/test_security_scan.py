"""Unit tests for security_scan rule engine."""

from __future__ import annotations

from shadow_web.schema_snap import parse_forms
from shadow_web.security_scan import (
    analyze_forms,
    analyze_http_headers,
    analyze_links,
    analyze_surface,
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
