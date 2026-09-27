"""Navigation URL policy: keep agent-driven browsing on the public web.

Threat model
------------
Any page the agent visits is untrusted input, and the agent's navigation target
can be chosen by that page (prompt injection) as well as by the caller.  Without
a policy the browser will happily open ``file:///etc/passwd``, cloud metadata
(``http://169.254.169.254/``), or loopback services, which turns "read a web
page" into local file disclosure and SSRF.

What this module enforces
-------------------------
* Scheme allow-list: ``http``/``https`` only (no ``file:``, ``javascript:``,
  ``data:``, ``blob:``, ``about:``, ``chrome:`` ...).
* Host policy: IP literals must be globally routable.  Loopback, private,
  link-local, unique-local, reserved, multicast and unspecified addresses are
  rejected, including the obfuscated forms browsers accept
  (``http://2130706433/``, ``http://0x7f000001/``, ``http://127.1/``, ``[::1]``,
  IPv4-mapped IPv6).
* Hostname policy: ``localhost``, ``*.localhost``, ``*.local``, ``*.internal``
  and cloud metadata host names are rejected.
* Best-effort DNS check (on by default): the resolved addresses must all be
  globally routable.  This narrows DNS-rebinding, but cannot eliminate it -
  Chromium resolves again at navigation time.  Set
  ``SHADOW_WEB_URL_DNS_CHECK=0`` to skip resolution.

Escape hatch
------------
``SHADOW_WEB_ALLOW_PRIVATE_URLS=1`` disables the whole policy.  It exists for
local development and for tests that intentionally drive a local server; it must
never be set in a deployment that browses untrusted pages.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Host names that must never be reachable from agent-driven navigation.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")

_DNS_TIMEOUT_SEC = 2.0


class UrlNotAllowed(RuntimeError):
    """Raised when a navigation target violates the URL policy."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"Navigation to {url!r} is not allowed: {reason}")
        self.url = url
        self.reason = reason


def allow_private_urls() -> bool:
    """True when the operator explicitly opted out of the URL policy."""
    flag = os.environ.get("SHADOW_WEB_ALLOW_PRIVATE_URLS", "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _dns_check_enabled() -> bool:
    flag = os.environ.get("SHADOW_WEB_URL_DNS_CHECK", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _ipv4_from_host(host: str) -> Optional[ipaddress.IPv4Address]:
    """Parse the IPv4 forms browsers accept (``a``, ``a.b``, ``a.b.c``, ``a.b.c.d``)."""
    parts = host.split(".")
    if not parts or len(parts) > 4:
        return None
    numbers = []
    for part in parts:
        if not part:
            return None
        low = part.lower()
        try:
            if low.startswith("0x"):
                value = int(low, 16)
            elif len(low) > 1 and low.startswith("0"):
                value = int(low, 8)
            else:
                value = int(low, 10)
        except ValueError:
            return None
        if value < 0:
            return None
        numbers.append(value)

    if len(numbers) == 1:
        if numbers[0] > 0xFFFFFFFF:
            return None
        combined = numbers[0]
    elif len(numbers) == 2:
        if numbers[0] > 0xFF or numbers[1] > 0xFFFFFF:
            return None
        combined = (numbers[0] << 24) | numbers[1]
    elif len(numbers) == 3:
        if numbers[0] > 0xFF or numbers[1] > 0xFF or numbers[2] > 0xFFFF:
            return None
        combined = (numbers[0] << 24) | (numbers[1] << 16) | numbers[2]
    else:
        if any(number > 0xFF for number in numbers):
            return None
        combined = (
            (numbers[0] << 24) | (numbers[1] << 16) | (numbers[2] << 8) | numbers[3]
        )
    return ipaddress.IPv4Address(combined)


def _parse_ip_literal(host: str) -> Optional[ipaddress._BaseAddress]:
    """Return the address for an IP literal in any browser-accepted form."""
    candidate = host.strip("[]")
    if not candidate:
        return None
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    return _ipv4_from_host(candidate)


def _ip_is_blocked(address: ipaddress._BaseAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _ip_is_blocked(address.ipv4_mapped)
    # is_global is False for loopback, private, link-local, CGNAT, unique-local,
    # reserved, multicast and unspecified ranges - exactly the set we must block.
    return not address.is_global


def _hostname_is_blocked(hostname: str) -> Optional[str]:
    host = hostname.strip("[]").lower().rstrip(".")
    if not host:
        return "empty host"
    if host in _BLOCKED_HOSTNAMES:
        return f"blocked host name {host!r}"
    for suffix in _BLOCKED_SUFFIXES:
        if host.endswith(suffix):
            return f"blocked host name suffix {suffix!r}"
    return None


def _resolve_host(host: str) -> Tuple[bool, str]:
    """Best-effort resolution.  Returns ``(ok, reason)``; fail-open on errors."""
    result: list = []

    def _worker() -> None:
        try:
            result.append(socket.getaddrinfo(host, None))
        except Exception as exc:  # noqa: BLE001 - any resolver failure is fail-open
            result.append(exc)

    import threading

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(_DNS_TIMEOUT_SEC)
    if not result:
        logger.debug("URL policy: DNS lookup for %s timed out; allowing", host)
        return True, ""
    payload = result[0]
    if isinstance(payload, Exception):
        logger.debug("URL policy: DNS lookup for %s failed (%s); allowing", host, payload)
        return True, ""
    for entry in payload:
        try:
            address = ipaddress.ip_address(entry[4][0])
        except ValueError:
            continue
        if _ip_is_blocked(address):
            return False, f"host {host!r} resolves to non-public address {address}"
    return True, ""


def check_url(url: str, *, resolve_dns: Optional[bool] = None) -> Tuple[bool, str]:
    """Pure policy check.  Returns ``(allowed, reason)`` and never raises."""
    if allow_private_urls():
        return True, ""
    raw = (url or "").strip()
    if not raw:
        return False, "empty URL"

    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        return False, f"unparsable URL ({exc})"

    scheme = (parts.scheme or "").lower()
    if not scheme:
        return False, "URL must include a scheme (http:// or https://)"
    if scheme not in ALLOWED_SCHEMES:
        return False, f"scheme {scheme!r} is not allowed (http/https only)"

    hostname = parts.hostname
    if not hostname:
        return False, "URL has no host"

    blocked_host = _hostname_is_blocked(hostname)
    if blocked_host:
        return False, blocked_host

    literal = _parse_ip_literal(hostname)
    if literal is not None:
        if _ip_is_blocked(literal):
            return False, f"non-public address {literal}"
        return True, ""

    if resolve_dns is None:
        resolve_dns = _dns_check_enabled()
    if resolve_dns:
        return _resolve_host(hostname)
    return True, ""


def validate_navigation_url(url: str, *, resolve_dns: Optional[bool] = None) -> str:
    """Return ``url`` if it passes the policy, else raise :class:`UrlNotAllowed`."""
    allowed, reason = check_url(url, resolve_dns=resolve_dns)
    if not allowed:
        raise UrlNotAllowed(url, reason)
    return url


def assert_navigation_landed(url: str, *, resolve_dns: Optional[bool] = None) -> str:
    """Re-check the *final* URL after navigation/redirect.

    Chromium follows redirects and ``location=`` assignments on its own, so the
    requested URL passing the policy says nothing about where the browser ended
    up.  Call this right after ``goto`` and use the post-navigation page state
    only when it passes.
    """
    allowed, reason = check_url(url, resolve_dns=resolve_dns)
    if not allowed:
        raise UrlNotAllowed(url, f"redirected/landed on a blocked target ({reason})")
    return url
