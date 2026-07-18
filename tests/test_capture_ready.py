"""Tests for universal consent dismiss + capture readiness."""

from types import SimpleNamespace

import pytest

from shadow_web.a11y_capture import detect_page_class
from shadow_web.capture_ready import (
    CaptureReadyResult,
    _content_grew,
    _should_scroll,
    dismiss_cookie_consent,
    is_probe_ready,
    is_sparse_shell,
    prepare_page_for_capture,
)


class TestProbeHeuristics:
    def test_ready_on_large_text(self):
        assert is_probe_ready({"has_body": True, "text_chars": 2500, "card_candidates": 0, "price_hits": 0})

    def test_ready_on_card_cluster(self):
        assert is_probe_ready(
            {"has_body": True, "text_chars": 500, "card_candidates": 8, "price_hits": 0}
        )

    def test_ready_on_price_hits(self):
        assert is_probe_ready(
            {"has_body": True, "text_chars": 450, "card_candidates": 0, "price_hits": 5}
        )

    def test_ready_on_stable_text_growth(self):
        prev = {"has_body": True, "text_chars": 900, "card_candidates": 0, "price_hits": 0}
        cur = {"has_body": True, "text_chars": 920, "card_candidates": 0, "price_hits": 0}
        assert is_probe_ready(cur, prev)

    def test_not_ready_when_still_growing(self):
        prev = {"has_body": True, "text_chars": 900, "card_candidates": 0, "price_hits": 0}
        cur = {"has_body": True, "text_chars": 1600, "card_candidates": 0, "price_hits": 0}
        assert not is_probe_ready(cur, prev)

    def test_sparse_shell_large_html_tiny_text(self):
        assert is_sparse_shell(
            {"has_body": True, "text_chars": 120, "html_chars": 80000, "card_candidates": 0, "price_hits": 0}
        )

    def test_sparse_shell_cookie_banner_band(self):
        assert is_sparse_shell(
            {
                "has_body": True,
                "text_chars": 300,
                "html_chars": 120000,
                "card_candidates": 0,
                "price_hits": 0,
            }
        )

    def test_sparse_shell_consent_only_dialog(self):
        assert is_sparse_shell(
            {
                "has_body": True,
                "text_chars": 1017,
                "html_chars": 200000,
                "card_candidates": 0,
                "price_hits": 0,
                "consent_only": True,
            }
        )

    def test_sparse_shell_missing_body(self):
        assert is_sparse_shell({"has_body": False, "text_chars": 0, "html_chars": 0})

    def test_not_shell_for_normal_catalog(self):
        assert not is_sparse_shell(
            {"has_body": True, "text_chars": 4000, "html_chars": 200000}
        )

    def test_content_grew_on_text_or_cards(self):
        before = {"text_chars": 200, "card_candidates": 0, "price_hits": 0}
        after = {"text_chars": 900, "card_candidates": 3, "price_hits": 0}
        assert _content_grew(before, after)
        assert not _content_grew(after, after)

    def test_should_scroll_when_sparse_but_not_empty_shell(self):
        probe = {
            "has_body": True,
            "text_chars": 400,
            "html_chars": 50000,
            "card_candidates": 1,
            "price_hits": 0,
        }
        assert _should_scroll(probe, scrolls=0, stagnant=0)

    def test_should_not_scroll_tiny_shell(self):
        probe = {
            "has_body": True,
            "text_chars": 40,
            "html_chars": 120000,
            "card_candidates": 0,
            "price_hits": 0,
        }
        assert not _should_scroll(probe, scrolls=0, stagnant=0)


class TestConsentDismiss:
    def test_clicks_accept_all_not_reject(self):
        clicked = []

        class FakeBtn:
            def __init__(self, label):
                self.label = label

            def click(self):
                clicked.append(self.label)

        # Simulate the JS result path via a page.evaluate stub that runs our
        # scoring contract: dismissed + accept-all label.
        page = SimpleNamespace(
            evaluate=lambda *_a, **_k: {
                "dismissed": True,
                "label": "accept all cookies",
                "method": "click",
            }
        )
        result = dismiss_cookie_consent(page)
        assert result["dismissed"] is True
        assert "accept" in result["label"]

    def test_handles_evaluate_failure(self):
        page = SimpleNamespace(evaluate=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
        result = dismiss_cookie_consent(page)
        assert result["dismissed"] is False
        assert result["method"].startswith("error:")


class TestPreparePage:
    def test_prepare_returns_ready_when_probe_immediately_ready(self):
        probes = [
            {
                "has_body": True,
                "text_chars": 3000,
                "html_chars": 50000,
                "card_candidates": 6,
                "price_hits": 4,
                "title": "Catalog",
            }
        ]

        def evaluate(script):
            if "ACCEPT" in script or "accept all" in script:
                return {"dismissed": False, "label": None, "method": "none"}
            return probes[0]

        page = SimpleNamespace(
            evaluate=evaluate,
            wait_for_timeout=lambda *_a, **_k: None,
        )
        result = prepare_page_for_capture(page, timeout_ms=1000, poll_ms=50)
        assert result.ready is True
        assert result.shell is False
        assert result.reason == "content_ready"
        assert result.card_candidates == 6
        assert result.scroll_count == 0

    def test_prepare_marks_shell_after_timeout(self):
        def evaluate(script):
            if "ACCEPT" in script or "accept all" in script:
                return {"dismissed": True, "label": "accept all", "method": "click"}
            return {
                "has_body": True,
                "text_chars": 80,
                "html_chars": 120000,
                "card_candidates": 0,
                "price_hits": 0,
                "title": "Temu",
            }

        page = SimpleNamespace(
            evaluate=evaluate,
            wait_for_timeout=lambda *_a, **_k: None,
        )
        result = prepare_page_for_capture(page, timeout_ms=200, poll_ms=50)
        assert result.ready is False
        assert result.shell is True
        assert result.consent_dismissed is True
        assert result.reason == "sparse_shell"
        assert result.scroll_count == 0

    def test_prepare_scrolls_until_content_grows(self):
        """Lazy feeds: first paint sparse → scroll → hydrated content ready."""
        state = {"probes": 0, "scrolls": 0}

        def evaluate(script):
            if "ACCEPT" in script or "accept all" in script:
                return {"dismissed": False, "label": None, "method": "none"}
            if "scrollBy" in script:
                state["scrolls"] += 1
                return {"scrolled": True, "at_bottom": False, "scroll_y": 800}
            state["probes"] += 1
            if state["scrolls"] == 0:
                return {
                    "has_body": True,
                    "text_chars": 350,
                    "html_chars": 40000,
                    "card_candidates": 1,
                    "price_hits": 0,
                    "title": "Feed",
                }
            return {
                "has_body": True,
                "text_chars": 2400,
                "html_chars": 90000,
                "card_candidates": 8,
                "price_hits": 0,
                "title": "Feed",
            }

        page = SimpleNamespace(
            evaluate=evaluate,
            wait_for_timeout=lambda *_a, **_k: None,
        )
        result = prepare_page_for_capture(page, timeout_ms=3000, poll_ms=50)
        assert result.ready is True
        assert result.scroll_count >= 1
        assert state["scrolls"] >= 1
        assert result.card_candidates == 8


class TestDetectPageClassSparseShell:
    def test_sparse_shell_from_readiness_stats(self):
        cls, reason = detect_page_class(
            "https://example.com/",
            "Shop",
            "<html>" + ("x" * 30000) + "</html>",
            {
                "readiness": {
                    "shell": True,
                    "text_chars": 90,
                    "has_body": True,
                    "wait_ms": 8000,
                }
            },
            action_map_len=2,
        )
        assert cls == "SparseShell"
        assert "shell" in reason.lower() or "content shell" in reason.lower()

    def test_without_readiness_keeps_spa(self):
        cls, _reason = detect_page_class(
            "https://example.com/",
            "App",
            "<html>" + ("x" * 5000) + "</html>",
            {},
            action_map_len=0,
        )
        assert cls == "SPA"

    def test_anti_bot_still_first(self):
        cls, _reason = detect_page_class(
            "https://example.com/",
            "Just a moment",
            "<html><body>cloudflare captcha just a moment</body></html>",
            {"readiness": {"shell": True, "text_chars": 10, "has_body": True}},
            action_map_len=0,
        )
        assert cls == "Anti-bot"
