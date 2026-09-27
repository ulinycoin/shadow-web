"""Tests for redaction of page context sent to a remote heal endpoint."""

from __future__ import annotations

import pytest

from shadow_web.heal_context import (
    REDACTED,
    minimal_context,
    redact_for_llm,
    share_page_context,
)


def test_textarea_contents_are_removed():
    html = '<div><textarea name="message">my secret draft</textarea></div>'
    out = redact_for_llm(html)
    assert "my secret draft" not in out
    assert REDACTED in out


def test_input_values_are_removed():
    html = (
        '<form><input type="text" name="card" value="4111111111111111">'
        '<input type="password" name="pw" value="hunter2">'
        '<input type="hidden" name="csrf" value="token-abc"></form>'
    )
    out = redact_for_llm(html)
    assert "4111111111111111" not in out
    assert "hunter2" not in out
    assert "token-abc" not in out
    assert out.count(REDACTED) >= 3


def test_checkbox_values_survive():
    html = '<input type="checkbox" name="agree" value="yes">'
    out = redact_for_llm(html)
    assert 'value="yes"' in out


def test_structural_attributes_survive():
    """The LLM needs ids/classes/labels to build a selector."""
    html = '<div class="cart-row" id="row-7"><button aria-label="Remove item">x</button></div>'
    out = redact_for_llm(html)
    assert 'class="cart-row"' in out
    assert 'id="row-7"' in out
    assert 'aria-label="Remove item"' in out


def test_data_secret_attributes_are_redacted():
    html = '<div data-token="abc123" data-value="xyz">ok</div>'
    out = redact_for_llm(html)
    assert "abc123" not in out
    assert "xyz" not in out


def test_output_is_bounded():
    out = redact_for_llm("<div>" + "A" * 10_000 + "</div>", max_chars=500)
    assert len(out) <= 500


def test_malformed_markup_falls_back_to_regex_scrub():
    raw = '<div><textarea>secret draft</textarea><input value="hidden value"'
    out = redact_for_llm(raw)
    assert "secret draft" not in out
    assert "hidden value" not in out


def test_empty_input_returns_empty_string():
    assert redact_for_llm("") == ""


def test_share_flag_defaults_on_and_can_be_disabled(monkeypatch):
    monkeypatch.delenv("SHADOW_WEB_HEAL_SHARE_CONTEXT", raising=False)
    assert share_page_context() is True
    monkeypatch.setenv("SHADOW_WEB_HEAL_SHARE_CONTEXT", "0")
    assert share_page_context() is False


def test_minimal_context_strips_markup_characters():
    ctx = minimal_context("</div><script>bad()</script>", "input[text]")
    assert "<script>" not in ctx
    assert ctx.startswith("<input>")
    assert ctx.endswith("</input>")


@pytest.mark.parametrize("label", ["Pay now", "Купить"])
def test_minimal_context_keeps_short_labels(label):
    assert label in minimal_context(label, "button")
