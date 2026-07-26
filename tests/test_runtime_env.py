"""Tests for runtime env helpers (dotenv + browser policy)."""

from __future__ import annotations

import os

import shadow_web.runtime_env as runtime_env


def test_load_dotenv_does_not_overwrite(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SHADOW_WEB_TEST_KEY=from_file\nOTHER=1\n", encoding="utf-8")
    monkeypatch.delenv("SHADOW_WEB_TEST_KEY", raising=False)
    monkeypatch.setenv("OTHER", "keep")
    runtime_env._DOTENV_LOADED = False
    loaded = runtime_env.load_dotenv_files([env_file])
    assert str(env_file.resolve()) in loaded
    assert os.environ["SHADOW_WEB_TEST_KEY"] == "from_file"
    assert os.environ["OTHER"] == "keep"


def test_allow_chromium_fallback_default_off(monkeypatch):
    monkeypatch.delenv("SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK", raising=False)
    assert runtime_env.allow_chromium_fallback() is False
    monkeypatch.setenv("SHADOW_WEB_ALLOW_CHROMIUM_FALLBACK", "1")
    assert runtime_env.allow_chromium_fallback() is True


def test_missing_llm_key_message_mentions_hermes():
    msg = runtime_env.missing_llm_key_message()
    assert "--api-key" in msg
    assert "Hermes" in msg or "child" in msg


def test_resolve_api_credentials_loads_dotenv(tmp_path, monkeypatch):
    from shadow_web.agent.models import resolve_api_credentials

    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-test-from-dotenv\n", encoding="utf-8")
    for name in runtime_env.LLM_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    runtime_env._DOTENV_LOADED = False
    monkeypatch.chdir(tmp_path)
    key, url = resolve_api_credentials()
    assert key == "sk-test-from-dotenv"
    assert url == "https://api.deepseek.com"
