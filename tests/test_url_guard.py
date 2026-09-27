"""Tests for the navigation URL policy (SSRF / local-file guard)."""

from __future__ import annotations

import pytest

from shadow_web.url_guard import (
    UrlNotAllowed,
    assert_navigation_landed,
    check_url,
    validate_navigation_url,
)

BLOCKED_SCHEMES = [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "file:///etc/hosts",
    "data:text/html,<h1>x</h1>",
    "blob:https://example.com/1234",
    "about:blank",
    "chrome://settings",
    "ftp://example.com/file",
    "//example.com/no-scheme",
]

BLOCKED_ADDRESSES = [
    "http://127.0.0.1/",
    "http://127.0.0.1:8000/v1/compress",
    "http://user:pass@127.0.0.1/",
    "http://[::1]/",
    "http://[::1]:8080/",
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://0.0.0.0/",
    "http://[fd00::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://2130706433/",
    "http://0x7f000001/",
    "http://127.1/",
    "http://0177.0.0.1/",
]

BLOCKED_HOSTNAMES = [
    "http://localhost/",
    "http://LOCALHOST:8000/",
    "http://foo.localhost/",
    "http://svc.internal/",
    "http://printer.local/",
    "http://metadata.google.internal/computeMetadata/v1/",
]

ALLOWED = [
    "https://example.com/",
    "https://en.wikipedia.org/wiki/Web_scraping",
    "http://93.184.216.34/",
    "https://shop.example.co.uk/products/item?id=42#variant",
]


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    """Keep the policy checks hermetic: no resolver traffic in unit tests."""
    monkeypatch.setenv("SHADOW_WEB_URL_DNS_CHECK", "0")
    monkeypatch.delenv("SHADOW_WEB_ALLOW_PRIVATE_URLS", raising=False)


@pytest.mark.parametrize("url", BLOCKED_SCHEMES)
def test_non_http_schemes_are_rejected(url):
    allowed, reason = check_url(url, resolve_dns=False)
    assert not allowed, url
    assert reason


@pytest.mark.parametrize("url", BLOCKED_ADDRESSES)
def test_non_public_addresses_are_rejected(url):
    allowed, reason = check_url(url, resolve_dns=False)
    assert not allowed, url
    assert reason


@pytest.mark.parametrize("url", BLOCKED_HOSTNAMES)
def test_internal_hostnames_are_rejected(url):
    allowed, reason = check_url(url, resolve_dns=False)
    assert not allowed, url
    assert reason


@pytest.mark.parametrize("url", ALLOWED)
def test_public_urls_are_allowed(url):
    assert check_url(url, resolve_dns=False) == (True, "")
    assert validate_navigation_url(url, resolve_dns=False) == url


def test_validate_raises_with_url_and_reason():
    with pytest.raises(UrlNotAllowed) as excinfo:
        validate_navigation_url("file:///etc/passwd", resolve_dns=False)
    assert excinfo.value.url == "file:///etc/passwd"
    assert excinfo.value.reason


def test_empty_url_is_rejected():
    allowed, reason = check_url("", resolve_dns=False)
    assert not allowed
    assert "empty" in reason


def test_redirect_target_is_rechecked():
    """A benign start URL that lands on a blocked target must be refused."""
    assert assert_navigation_landed("https://example.com/", resolve_dns=False)
    with pytest.raises(UrlNotAllowed) as excinfo:
        assert_navigation_landed("file:///etc/passwd", resolve_dns=False)
    assert "landed on a blocked target" in excinfo.value.reason
    with pytest.raises(UrlNotAllowed):
        assert_navigation_landed("http://169.254.169.254/", resolve_dns=False)


def test_escape_hatch_allows_private_targets(monkeypatch):
    monkeypatch.setenv("SHADOW_WEB_ALLOW_PRIVATE_URLS", "1")
    assert check_url("file:///etc/passwd", resolve_dns=False) == (True, "")
    assert check_url("http://127.0.0.1:8000/", resolve_dns=False) == (True, "")


def test_dns_check_blocks_hostname_resolving_to_loopback(monkeypatch):
    """When resolution is enabled, a public name pointing at loopback is refused."""
    import shadow_web.url_guard as guard

    monkeypatch.setattr(
        guard,
        "_resolve_host",
        lambda host: (False, f"host {host!r} resolves to non-public address 127.0.0.1"),
    )
    allowed, reason = check_url("https://rebind.example.com/", resolve_dns=True)
    assert not allowed
    assert "non-public address" in reason


def test_dns_check_fails_open_when_resolution_fails(monkeypatch):
    import shadow_web.url_guard as guard

    monkeypatch.setattr(guard, "_resolve_host", lambda host: (True, ""))
    assert check_url("https://unresolvable.example.com/", resolve_dns=True) == (True, "")
