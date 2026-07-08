"""Attack surface rule engine for Shadow Web security scans."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse, urljoin

from shadow_web.schema_snap import parse_forms

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")

SECURITY_HEADER_KEYS = (
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
    "access-control-allow-origin",
)

_SENSITIVE_PATH = re.compile(
    r"/(?:admin|api|debug|backup|config|wp-admin|phpmyadmin|\.env|actuator)(?:/|$)",
    re.I,
)

_SENSITIVE_LABEL = re.compile(
    r"\b(admin panel|delete all|reset password|debug mode|export all data)\b",
    re.I,
)

_SESSION_COOKIE = re.compile(
    r"(?i)(session|sess|auth|token|jwt|sid|login|csrf|xsrf|remember|phpssid|connect\.sid)"
)


@dataclass
class Finding:
    severity: str
    rule_id: str
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _netloc(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_http_url(url: str) -> bool:
    return url.lower().startswith("http://")


def _normalize_path(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.rule_id, finding.title, finding.detail)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def analyze_forms(page_url: str, forms: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []

    for idx, form in enumerate(forms, start=1):
        method = (form.get("method") or "GET").upper()
        action = form.get("action") or page_url
        fields = form.get("fields") or []
        password_fields = [f for f in fields if f.get("type") == "password"]
        hidden_fields = [f for f in fields if f.get("type") == "hidden"]
        file_fields = [f for f in fields if f.get("type") == "file"]

        form_ctx = {"form_index": idx, "method": method, "action": action}

        if password_fields and method == "GET":
            findings.append(
                Finding(
                    severity="critical",
                    rule_id="FORM_PASSWORD_GET",
                    title="Password field submitted via GET",
                    detail="Credentials may leak via URL, logs, and Referer headers.",
                    evidence={**form_ctx, "fields": [f.get("name") or f.get("label") for f in password_fields]},
                )
            )

        if action and _is_http_url(action):
            findings.append(
                Finding(
                    severity="high",
                    rule_id="FORM_INSECURE_ACTION",
                    title="Form posts to insecure HTTP URL",
                    detail=f"Form action uses HTTP: {action}",
                    evidence=form_ctx,
                )
            )

        for pf in password_fields:
            if "minlength" not in pf and "pattern" not in pf:
                findings.append(
                    Finding(
                        severity="medium",
                        rule_id="FORM_WEAK_PASSWORD",
                        title="Password field lacks client-side length/pattern constraints",
                        detail="No minlength or pattern on password input (client hint only).",
                        evidence={
                            **form_ctx,
                            "field": pf.get("name") or pf.get("label") or "password",
                        },
                    )
                )

        if len(hidden_fields) > 3:
            findings.append(
                Finding(
                    severity="low",
                    rule_id="FORM_MANY_HIDDEN",
                    title="Form has many hidden fields",
                    detail=f"{len(hidden_fields)} hidden inputs — verify server-side validation.",
                    evidence={**form_ctx, "hidden_count": len(hidden_fields)},
                )
            )

        if file_fields:
            findings.append(
                Finding(
                    severity="medium",
                    rule_id="FORM_FILE_UPLOAD",
                    title="Public file upload control detected",
                    detail="Review upload limits, MIME validation, and auth requirements.",
                    evidence={
                        **form_ctx,
                        "fields": [f.get("name") or f.get("label") for f in file_fields],
                    },
                )
            )

    return findings


def analyze_links(page_url: str, action_map: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    page_host = _netloc(page_url)
    http_links: list[str] = []
    external_links: list[str] = []
    sensitive_links: list[dict[str, str]] = []

    for action in action_map:
        href = (action.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        if href.startswith("javascript:"):
            findings.append(
                Finding(
                    severity="low",
                    rule_id="LINK_JAVASCRIPT",
                    title="javascript: link in attack surface",
                    detail=f"Link label: {(action.get('label') or '')[:80]}",
                    evidence={"href": href[:120], "label": action.get("label", "")},
                )
            )
            continue

        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue

        if _is_http_url(absolute):
            http_links.append(absolute)

        link_host = parsed.netloc.lower()
        if page_host and link_host and link_host != page_host:
            external_links.append(absolute)

        path = parsed.path or "/"
        label = action.get("label") or ""
        if _SENSITIVE_PATH.search(path) or _SENSITIVE_LABEL.search(label):
            sensitive_links.append({"url": absolute, "label": label[:100]})

    if _is_http_url(page_url):
        findings.append(
            Finding(
                severity="high",
                rule_id="PAGE_HTTP",
                title="Page served over HTTP",
                detail="Login forms and cookies on HTTP are vulnerable to interception.",
                evidence={"url": page_url},
            )
        )

    if http_links:
        unique_http = sorted(set(http_links))[:10]
        findings.append(
            Finding(
                severity="high",
                rule_id="LINK_HTTP_RESOURCE",
                title="Insecure HTTP links on page",
                detail=f"{len(set(http_links))} unique http:// link(s) detected.",
                evidence={"sample": unique_http},
            )
        )

    if external_links:
        unique_external = sorted(set(external_links))
        findings.append(
            Finding(
                severity="info",
                rule_id="LINK_EXTERNAL",
                title="External outbound links",
                detail=f"{len(unique_external)} external link(s) — review trust and rel=noopener.",
                evidence={"count": len(unique_external), "sample": unique_external[:15]},
            )
        )

    for item in sensitive_links[:10]:
        findings.append(
            Finding(
                severity="medium",
                rule_id="LINK_SENSITIVE_PATH",
                title="Link to potentially sensitive path",
                detail=f"Surface exposes {item['url']}",
                evidence=item,
            )
        )

    return findings


def analyze_page_meta(
    page_url: str,
    *,
    page_class: str,
    page_class_reason: str,
    action_count: int,
    capture_stats: dict[str, Any] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    stats = capture_stats or {}

    if page_class == "Anti-bot":
        findings.append(
            Finding(
                severity="info",
                rule_id="PAGE_ANTIBOT",
                title="Anti-bot protection detected",
                detail=page_class_reason or "Automated scan may be incomplete.",
                evidence={"url": page_url},
            )
        )
    elif page_class == "Auth-gated":
        findings.append(
            Finding(
                severity="info",
                rule_id="PAGE_AUTH_GATED",
                title="Authentication-gated page",
                detail=page_class_reason or "Redirected to login or sign-in.",
                evidence={"url": page_url},
            )
        )
    elif page_class == "SPA" and action_count == 0:
        findings.append(
            Finding(
                severity="low",
                rule_id="PAGE_SPA_EMPTY",
                title="SPA with empty interactive surface",
                detail="Dynamic content may hide forms/links from this pass.",
                evidence={"url": page_url, "reason": page_class_reason},
            )
        )

    if page_class in ("Shadow DOM", "Closed Shadow"):
        findings.append(
            Finding(
                severity="info",
                rule_id="PAGE_SHADOW_DOM",
                title="Shadow DOM present",
                detail=page_class_reason,
                evidence={"shadow_hosts": stats.get("shadow_hosts", 0)},
            )
        )

    cross_origin = stats.get("cross_origin_iframes", 0)
    if cross_origin or page_class == "Iframe-heavy":
        findings.append(
            Finding(
                severity="info",
                rule_id="PAGE_CROSS_ORIGIN_IFRAME",
                title="Cross-origin iframe content",
                detail="Embedded third-party UI may contain untrusted attack surface.",
                evidence={"cross_origin_iframes": cross_origin},
            )
        )

    return findings


def normalize_header_map(headers: dict[str, Any]) -> dict[str, str]:
    """Lower-case header names for consistent lookups."""
    return {str(k).lower(): str(v) for k, v in headers.items()}


def summarize_security_headers(headers: dict[str, str]) -> dict[str, Optional[str]]:
    """Security-relevant headers for reports (truncated CSP)."""
    csp = headers.get("content-security-policy", "")
    if len(csp) > 240:
        csp = csp[:240] + "..."
    return {
        "strict-transport-security": headers.get("strict-transport-security"),
        "content-security-policy": csp or None,
        "x-frame-options": headers.get("x-frame-options"),
        "x-content-type-options": headers.get("x-content-type-options"),
        "referrer-policy": headers.get("referrer-policy"),
        "permissions-policy": headers.get("permissions-policy"),
        "access-control-allow-origin": headers.get("access-control-allow-origin"),
    }


def fetch_http_headers(url: str, *, timeout: float = 15.0) -> tuple[dict[str, str], str, Optional[str]]:
    """Fetch response headers via HEAD (GET fallback). Returns (headers, final_url, error)."""
    import requests

    try:
        response = requests.head(url, allow_redirects=True, timeout=timeout)
        if response.status_code >= 400 or not response.headers:
            response = requests.get(url, allow_redirects=True, timeout=timeout, stream=True)
            response.close()
        return normalize_header_map(dict(response.headers)), str(response.url), None
    except Exception as exc:
        return {}, url, str(exc)


def probe_http_redirect(https_url: str, *, timeout: float = 10.0) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Check whether plain HTTP redirects to HTTPS. Returns (status, location, error)."""
    import requests

    parsed = urlparse(https_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return None, None, "not_https_url"

    http_url = f"http://{parsed.netloc}{parsed.path or '/'}"
    try:
        response = requests.get(http_url, allow_redirects=False, timeout=timeout)
        location = response.headers.get("Location") or response.headers.get("location")
        return response.status_code, location, None
    except Exception as exc:
        return None, None, str(exc)


def analyze_http_headers(
    page_url: str,
    headers: dict[str, str],
    *,
    http_probe_status: Optional[int] = None,
    http_probe_location: Optional[str] = None,
) -> list[Finding]:
    """Rule engine for HTTP security headers."""
    findings: list[Finding] = []
    parsed = urlparse(page_url)
    is_https = parsed.scheme == "https"
    header_summary = summarize_security_headers(headers)

    if is_https and not headers.get("strict-transport-security"):
        findings.append(
            Finding(
                severity="high",
                rule_id="HEADER_MISSING_HSTS",
                title="Missing Strict-Transport-Security",
                detail="HTTPS response has no HSTS header.",
                evidence={"url": page_url, "headers": header_summary},
            )
        )
    elif is_https and headers.get("strict-transport-security"):
        hsts = headers["strict-transport-security"].lower()
        if "max-age=" in hsts:
            try:
                max_age = int(hsts.split("max-age=", 1)[1].split(";", 1)[0].strip())
                if max_age < 31_536_000:
                    findings.append(
                        Finding(
                            severity="low",
                            rule_id="HEADER_SHORT_HSTS",
                            title="HSTS max-age under one year",
                            detail=f"max-age={max_age} (< 31536000 recommended).",
                            evidence={"max_age": max_age},
                        )
                    )
            except ValueError:
                pass

    if not headers.get("content-security-policy"):
        findings.append(
            Finding(
                severity="medium",
                rule_id="HEADER_MISSING_CSP",
                title="Missing Content-Security-Policy",
                detail="No CSP header on response.",
                evidence={"url": page_url, "headers": header_summary},
            )
        )
    else:
        csp = headers["content-security-policy"].lower()
        if "'unsafe-inline'" in csp or "'unsafe-eval'" in csp:
            findings.append(
                Finding(
                    severity="low",
                    rule_id="HEADER_CSP_UNSAFE",
                    title="CSP allows unsafe-inline or unsafe-eval",
                    detail="Inline script policy weakens XSS defenses.",
                    evidence={"csp_excerpt": headers["content-security-policy"][:200]},
                )
            )
        if "frame-ancestors" not in csp and not headers.get("x-frame-options"):
            findings.append(
                Finding(
                    severity="medium",
                    rule_id="HEADER_MISSING_CLICKJACKING_PROTECTION",
                    title="No clickjacking protection",
                    detail="Missing X-Frame-Options and CSP frame-ancestors.",
                    evidence={"url": page_url},
                )
            )

    if headers.get("x-frame-options") and headers["x-frame-options"].upper() not in ("DENY", "SAMEORIGIN"):
        findings.append(
            Finding(
                severity="low",
                rule_id="HEADER_WEAK_XFO",
                title="Non-standard X-Frame-Options value",
                detail=f"Value: {headers['x-frame-options']}",
                evidence={"x-frame-options": headers["x-frame-options"]},
            )
        )

    if not headers.get("x-content-type-options"):
        findings.append(
            Finding(
                severity="low",
                rule_id="HEADER_MISSING_NOSNIFF",
                title="Missing X-Content-Type-Options",
                detail="Consider nosniff to reduce MIME confusion attacks.",
                evidence={"url": page_url},
            )
        )
    elif headers["x-content-type-options"].lower() != "nosniff":
        findings.append(
            Finding(
                severity="low",
                rule_id="HEADER_WEAK_NOSNIFF",
                title="X-Content-Type-Options is not nosniff",
                detail=f"Value: {headers['x-content-type-options']}",
                evidence={"x-content-type-options": headers["x-content-type-options"]},
            )
        )

    acao = headers.get("access-control-allow-origin")
    if acao == "*":
        findings.append(
            Finding(
                severity="info",
                rule_id="HEADER_CORS_WILDCARD",
                title="Access-Control-Allow-Origin: *",
                detail="Wildcard CORS on this response — verify API endpoints separately.",
                evidence={"access-control-allow-origin": acao},
            )
        )

    if is_https and http_probe_status is not None:
        if http_probe_status in (301, 302, 307, 308):
            loc = (http_probe_location or "").lower()
            if not loc.startswith("https://"):
                findings.append(
                    Finding(
                        severity="high",
                        rule_id="HEADER_HTTP_WEAK_REDIRECT",
                        title="HTTP does not redirect to HTTPS",
                        detail=f"HTTP probe returned {http_probe_status} → {http_probe_location}",
                        evidence={"status": http_probe_status, "location": http_probe_location},
                    )
                )
        elif http_probe_status == 200:
            findings.append(
                Finding(
                    severity="high",
                    rule_id="HEADER_HTTP_AVAILABLE",
                    title="HTTP serves content without redirect",
                    detail="Plain HTTP returned 200 — credentials may be sent in cleartext.",
                    evidence={"status": http_probe_status},
                )
            )

    if not is_https:
        findings.append(
            Finding(
                severity="high",
                rule_id="PAGE_HTTP",
                title="Page served over HTTP",
                detail="No TLS on this URL — HSTS and secure cookies cannot apply.",
                evidence={"url": page_url},
            )
        )

    return findings


def _cookie_host_matches(cookie_domain: str, page_host: str) -> bool:
    cd = cookie_domain.lstrip(".").lower()
    ph = page_host.lower()
    if not cd or not ph:
        return True
    return ph == cd or ph.endswith("." + cd)


def _is_session_like_cookie(name: str) -> bool:
    lowered = name.lower()
    if lowered.startswith("__host-") or lowered.startswith("__secure-"):
        return True
    return bool(_SESSION_COOKIE.search(name))


def normalize_playwright_cookie(cookie: dict[str, Any]) -> dict[str, Any]:
    """Normalize Playwright cookie dict for analysis."""
    same_site = cookie.get("sameSite") or cookie.get("samesite") or ""
    return {
        "name": str(cookie.get("name", "")),
        "domain": str(cookie.get("domain", "")),
        "path": str(cookie.get("path", "/")),
        "secure": bool(cookie.get("secure", False)),
        "httpOnly": bool(cookie.get("httpOnly", cookie.get("httponly", False))),
        "sameSite": str(same_site) if same_site else "",
        "expires": cookie.get("expires"),
    }


def summarize_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cookie flags for reports (values redacted)."""
    summary: list[dict[str, Any]] = []
    for cookie in cookies[:25]:
        norm = normalize_playwright_cookie(cookie)
        expires = norm.get("expires")
        summary.append(
            {
                "name": norm["name"],
                "domain": norm["domain"],
                "path": norm["path"],
                "secure": norm["secure"],
                "httpOnly": norm["httpOnly"],
                "sameSite": norm["sameSite"] or None,
                "session": expires in (-1, None),
            }
        )
    if len(cookies) > 25:
        summary.append({"truncated": len(cookies) - 25})
    return summary


def analyze_cookies(page_url: str, cookies: list[dict[str, Any]]) -> list[Finding]:
    """Rule engine for cookie Secure / HttpOnly / SameSite flags."""
    findings: list[Finding] = []
    if not cookies:
        return findings

    parsed = urlparse(page_url)
    is_https = parsed.scheme == "https"
    page_host = _netloc(page_url)

    for raw in cookies:
        cookie = normalize_playwright_cookie(raw)
        name = cookie["name"]
        if not name:
            continue

        ctx = {
            "name": name,
            "domain": cookie["domain"],
            "path": cookie["path"],
            "secure": cookie["secure"],
            "httpOnly": cookie["httpOnly"],
            "sameSite": cookie["sameSite"] or None,
        }
        session_like = _is_session_like_cookie(name)
        same_site = cookie["sameSite"].lower() if cookie["sameSite"] else ""

        if is_https and not cookie["secure"]:
            findings.append(
                Finding(
                    severity="high" if session_like else "medium",
                    rule_id="COOKIE_MISSING_SECURE",
                    title="Cookie missing Secure flag on HTTPS",
                    detail=f"Cookie `{name}` sent without Secure on TLS page.",
                    evidence=ctx,
                )
            )

        if session_like and not cookie["httpOnly"]:
            findings.append(
                Finding(
                    severity="high",
                    rule_id="COOKIE_MISSING_HTTPONLY",
                    title="Session-like cookie accessible to JavaScript",
                    detail=f"Cookie `{name}` lacks HttpOnly — XSS can exfiltrate it.",
                    evidence=ctx,
                )
            )

        if same_site == "none" and not cookie["secure"]:
            findings.append(
                Finding(
                    severity="high",
                    rule_id="COOKIE_SAMESITE_NONE_INSECURE",
                    title="SameSite=None without Secure",
                    detail=f"Cookie `{name}` uses SameSite=None but Secure is not set.",
                    evidence=ctx,
                )
            )

        if not same_site:
            findings.append(
                Finding(
                    severity="low",
                    rule_id="COOKIE_MISSING_SAMESITE",
                    title="Cookie has no explicit SameSite",
                    detail=f"Cookie `{name}` relies on browser default (usually Lax).",
                    evidence=ctx,
                )
            )

        lowered = name.lower()
        if lowered.startswith("__host-"):
            if cookie["domain"] or cookie["path"] != "/":
                findings.append(
                    Finding(
                        severity="medium",
                        rule_id="COOKIE_HOST_PREFIX_VIOLATION",
                        title="__Host- cookie prefix requirements violated",
                        detail=f"Cookie `{name}` must have Path=/ and no Domain attribute.",
                        evidence=ctx,
                    )
                )
            if not cookie["secure"]:
                findings.append(
                    Finding(
                        severity="high",
                        rule_id="COOKIE_HOST_PREFIX_INSECURE",
                        title="__Host- cookie without Secure",
                        detail=f"Cookie `{name}` requires Secure flag.",
                        evidence=ctx,
                    )
                )

        if lowered.startswith("__secure-") and not cookie["secure"]:
            findings.append(
                Finding(
                    severity="high",
                    rule_id="COOKIE_SECURE_PREFIX_INSECURE",
                    title="__Secure- cookie without Secure flag",
                    detail=f"Cookie `{name}` violates __Secure- prefix rules.",
                    evidence=ctx,
                )
            )

        if page_host and cookie["domain"] and not _cookie_host_matches(cookie["domain"], page_host):
            findings.append(
                Finding(
                    severity="info",
                    rule_id="COOKIE_THIRD_PARTY",
                    title="Third-party cookie on page",
                    detail=f"Cookie `{name}` domain `{cookie['domain']}` differs from page host.",
                    evidence=ctx,
                )
            )

    return findings


def analyze_surface(
    page_url: str,
    *,
    title: str = "",
    page_class: str = "Static",
    page_class_reason: str = "",
    action_count: int = 0,
    action_map: list[dict[str, Any]] | None = None,
    clean_html: str = "",
    capture_stats: dict[str, Any] | None = None,
    http_headers: dict[str, str] | None = None,
    http_probe_status: Optional[int] = None,
    http_probe_location: Optional[str] = None,
    cookies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run all security rules on a single scanned page."""
    forms = parse_forms(clean_html) if clean_html else []
    actions = action_map or []

    findings: list[Finding] = []
    if http_headers is not None:
        findings.extend(
            analyze_http_headers(
                page_url,
                http_headers,
                http_probe_status=http_probe_status,
                http_probe_location=http_probe_location,
            )
        )
    if cookies is not None:
        findings.extend(analyze_cookies(page_url, cookies))
    findings.extend(analyze_forms(page_url, forms))
    findings.extend(analyze_links(page_url, actions))
    findings.extend(
        analyze_page_meta(
            page_url,
            page_class=page_class,
            page_class_reason=page_class_reason,
            action_count=action_count,
            capture_stats=capture_stats,
        )
    )

    findings.sort(key=lambda f: SEVERITY_ORDER.index(f.severity) if f.severity in SEVERITY_ORDER else 99)
    findings = _dedupe_findings(findings)

    counts = {level: 0 for level in SEVERITY_ORDER}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    payload: dict[str, Any] = {
        "url": page_url,
        "title": title,
        "page_class": page_class,
        "page_class_reason": page_class_reason,
        "action_count": action_count,
        "form_count": len(forms),
        "finding_counts": counts,
        "findings": [f.to_dict() for f in findings],
    }
    if http_headers is not None:
        payload["http_headers"] = summarize_security_headers(http_headers)
    if cookies is not None:
        payload["cookie_count"] = len(cookies)
        payload["cookies"] = summarize_cookies(cookies)
    return payload


def extract_same_domain_links(page_url: str, action_map: list[dict[str, Any]], *, max_links: int = 30) -> list[str]:
    """Collect same-domain absolute URLs for shallow crawling."""
    host = _netloc(page_url)
    if not host:
        return []

    found: list[str] = []
    seen: set[str] = set()

    for action in action_map:
        href = (action.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.lower() != host:
            continue
        normalized = f"{parsed.scheme}://{parsed.netloc}{_normalize_path(absolute)}"
        if normalized in seen:
            continue
        seen.add(normalized)
        found.append(normalized)
        if len(found) >= max_links:
            break

    return found


def summarize_report(pages: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {level: 0 for level in SEVERITY_ORDER}
    for page in pages:
        if "error" in page:
            continue
        for level, count in page.get("finding_counts", {}).items():
            totals[level] = totals.get(level, 0) + count

    ranked = []
    for page in pages:
        if "error" in page:
            continue
        score = (
            page.get("finding_counts", {}).get("critical", 0) * 100
            + page.get("finding_counts", {}).get("high", 0) * 25
            + page.get("finding_counts", {}).get("medium", 0) * 5
        )
        ranked.append({"url": page["url"], "risk_score": score, "finding_counts": page.get("finding_counts", {})})

    ranked.sort(key=lambda x: x["risk_score"], reverse=True)

    return {
        "pages_scanned": len([p for p in pages if "error" not in p]),
        "pages_failed": len([p for p in pages if "error" in p]),
        "finding_totals": totals,
        "highest_risk_pages": ranked[:5],
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Attack Surface Security Scan",
        "",
        f"**Generated:** {report.get('generated_at', '')}",
        f"**Scope:** automated surface mapping (not penetration testing)",
        "",
        "## Executive summary",
        "",
        f"- Pages scanned: **{summary.get('pages_scanned', 0)}**",
        f"- Pages failed: **{summary.get('pages_failed', 0)}**",
        f"- Findings: critical **{summary.get('finding_totals', {}).get('critical', 0)}**, "
        f"high **{summary.get('finding_totals', {}).get('high', 0)}**, "
        f"medium **{summary.get('finding_totals', {}).get('medium', 0)}**, "
        f"low **{summary.get('finding_totals', {}).get('low', 0)}**, "
        f"info **{summary.get('finding_totals', {}).get('info', 0)}**",
        "",
    ]

    highest = summary.get("highest_risk_pages") or []
    if highest:
        lines.append("### Highest-risk pages")
        lines.append("")
        for item in highest:
            if item.get("risk_score", 0) <= 0:
                continue
            lines.append(f"- `{item['url']}` — score {item['risk_score']}")
        lines.append("")

    lines.extend(["## Findings by page", ""])

    for page in report.get("pages", []):
        if "error" in page:
            lines.append(f"### {page.get('url', '?')} — ERROR")
            lines.append("")
            lines.append(f"`{page['error']}`")
            lines.append("")
            continue

        lines.append(f"### {page.get('title') or page.get('url')}")
        lines.append("")
        lines.append(f"- URL: `{page.get('url')}`")
        lines.append(f"- page_class: **{page.get('page_class')}** — {page.get('page_class_reason', '')}")
        lines.append(f"- action_count: {page.get('action_count')}, forms: {page.get('form_count')}")
        if page.get("http_headers"):
            lines.append("- **HTTP headers:**")
            for key, value in page["http_headers"].items():
                if value:
                    lines.append(f"  - `{key}`: {value}")
        cookie_count = page.get("cookie_count", 0)
        if cookie_count or page.get("cookies"):
            lines.append(f"- **Cookies:** {cookie_count}")
            for cookie in page.get("cookies") or []:
                if "truncated" in cookie:
                    lines.append(f"  - … and {cookie['truncated']} more")
                    continue
                flags = []
                if cookie.get("secure"):
                    flags.append("Secure")
                if cookie.get("httpOnly"):
                    flags.append("HttpOnly")
                if cookie.get("sameSite"):
                    flags.append(f"SameSite={cookie['sameSite']}")
                flag_text = ", ".join(flags) if flags else "no security flags"
                lines.append(f"  - `{cookie.get('name')}` ({cookie.get('domain')}): {flag_text}")
        lines.append("")

        findings = page.get("findings") or []
        if not findings:
            lines.append("_No rule findings on this page._")
            lines.append("")
            continue

        for finding in findings:
            sev = finding.get("severity", "info").upper()
            lines.append(f"- **[{sev}]** {finding.get('title')} (`{finding.get('rule_id')}`)")
            lines.append(f"  {finding.get('detail')}")
        lines.append("")

    lines.extend(
        [
            "## Limitations",
            "",
            "- Does not test XSS, SQLi, auth bypass, or active CORS exploitation.",
            "- Cookie audit covers flags from browser context (Secure, HttpOnly, SameSite); not pentest.",
            "- Use only on systems you are authorized to scan.",
            "",
        ]
    )

    return "\n".join(lines)
