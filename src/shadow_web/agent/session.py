"""Sync Playwright / Camoufox browser session for the exec agent."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal, Optional

from shadow_web.runtime_env import allow_chromium_fallback, camoufox_unavailable_message
from shadow_web.wrapper import ShadowPage

logger = logging.getLogger(__name__)

BrowserEngine = Literal["camoufox", "chromium"]

# Session names become directory names under ~/.shadow-web/sessions, so they are
# restricted to a conservative character set (an unrestricted name such as
# "../../../tmp/x" would create arbitrary directories, and an absolute path
# would replace the whole prefix).
_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def sanitize_session_name(name: Optional[str]) -> Optional[str]:
    """Validate a persistent-profile name.  Empty/None means "no profile"."""
    if name is None:
        return None
    cleaned = str(name).strip()
    if not cleaned:
        return None
    if not _SESSION_NAME_RE.match(cleaned) or cleaned in {".", ".."}:
        raise ValueError(
            f"invalid session name {name!r}: expected [A-Za-z0-9._-]{{1,64}} "
            "(it is used as a directory under ~/.shadow-web/sessions)"
        )
    return cleaned


def normalize_browser_engine(value: Optional[str]) -> BrowserEngine:
    """Validate CLI/API browser engine name."""
    name = (value or "camoufox").strip().lower()
    if name not in ("camoufox", "chromium"):
        raise ValueError("browser must be 'camoufox' or 'chromium'")
    return name  # type: ignore[return-value]


class BrowserSession:
    """Owns Playwright lifecycle and a ShadowPage wrapper."""

    def __init__(
        self,
        *,
        headed: bool = False,
        session_name: Optional[str] = None,
        heal_api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        browser: str = "camoufox",
        allow_chromium_fallback_flag: Optional[bool] = None,
    ):
        self.headed = headed
        self.session_name = sanitize_session_name(session_name)
        self.heal_api_url = heal_api_url
        self.api_key = api_key
        self.requested_browser = normalize_browser_engine(browser)
        self.allow_chromium_fallback = (
            allow_chromium_fallback()
            if allow_chromium_fallback_flag is None
            else bool(allow_chromium_fallback_flag)
        )
        self.browser_type: str = self.requested_browser
        self.browser_fallback: bool = False
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self.page: Any = None
        self.shadow: Optional[ShadowPage] = None

    def _session_dir(self) -> Path:
        assert self.session_name
        base = Path.home() / ".shadow-web" / "sessions"
        profile = base / self.session_name
        # Defence in depth: even if a caller bypassed sanitize_session_name, the
        # profile must stay inside the sessions directory.
        resolved = profile.resolve()
        if base.resolve() not in resolved.parents and resolved != base.resolve():
            raise ValueError(
                f"session path {resolved} escapes {base} - refusing to use it"
            )
        profile.mkdir(parents=True, exist_ok=True)
        return profile

    def _attach_shadow(self) -> ShadowPage:
        # Camoufox/Firefox has no CDP — force DOM-only capture (a11y path is Chromium).
        capture_mode = "dom" if self.browser_type == "camoufox" else "auto"
        self.shadow = ShadowPage(
            self.page,
            heal_api_url=self.heal_api_url,
            api_key=self.api_key,
            capture_mode=capture_mode,
        )
        return self.shadow

    def _start_chromium(self, headless: bool) -> ShadowPage:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        if self.session_name:
            profile = self._session_dir()
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=headless,
                locale="en-US",
            )
            self.page = (
                self._context.pages[0] if self._context.pages else self._context.new_page()
            )
        else:
            self._browser = self._pw.chromium.launch(headless=headless)
            self._context = self._browser.new_context(locale="en-US")
            self.page = self._context.new_page()
        self.browser_type = "chromium"
        logger.info("Browser: chromium")
        return self._attach_shadow()

    def _start_camoufox(self, headless: bool) -> ShadowPage:
        from camoufox.sync_api import NewBrowser
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        if self.session_name:
            profile = self._session_dir()
            # NewBrowser(persistent_context=True) returns a BrowserContext.
            self._context = NewBrowser(
                self._pw,
                headless=headless,
                persistent_context=True,
                user_data_dir=str(profile),
            )
            self.page = (
                self._context.pages[0] if self._context.pages else self._context.new_page()
            )
        else:
            self._browser = NewBrowser(self._pw, headless=headless)
            self._context = self._browser.new_context(locale="en-US")
            self.page = self._context.new_page()
        self.browser_type = "camoufox"
        logger.info("Browser: camoufox (Firefox)")
        return self._attach_shadow()

    def start(self) -> ShadowPage:
        headless = not self.headed
        if self.requested_browser == "camoufox":
            try:
                self.browser_fallback = False
                return self._start_camoufox(headless)
            except Exception as exc:
                if not self.allow_chromium_fallback:
                    self.close()
                    raise RuntimeError(camoufox_unavailable_message(exc)) from exc
                logger.warning(
                    "Camoufox unavailable (%s), falling back to Chromium "
                    "(SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK=1)",
                    exc,
                )
                self.close()
                shadow = self._start_chromium(headless)
                self.browser_fallback = True
                return shadow
        self.browser_fallback = False
        return self._start_chromium(headless)

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception as exc:
            logger.warning("[BrowserSession] context close failed: %s", exc)
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception as exc:
            logger.warning("[BrowserSession] browser close failed: %s", exc)
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception as exc:
            logger.warning("[BrowserSession] playwright stop failed: %s", exc)
        self._context = None
        self._browser = None
        self._pw = None
        self.page = None
        self.shadow = None

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
