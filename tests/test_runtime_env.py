"""Tests for runtime env helpers (dotenv + browser policy)."""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

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


def _isolate_repo_root(monkeypatch, tmp_path) -> None:
    """Keep repository-root discovery away from this checkout's real .env."""
    monkeypatch.setattr(
        runtime_env, "__file__", str(tmp_path / "pkg" / "shadow_web" / "runtime_env.py")
    )


def test_dotenv_in_cwd_is_not_loaded(tmp_path, monkeypatch, caplog):
    # A cloned/attacker checkout must not be able to point the LLM at its own
    # endpoint or inject its own API key via cwd/.env.
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=attacker-key\n"
        "SHADOW_WEB_LLM_BASE_URL=https://attacker.example/v1\n",
        encoding="utf-8",
    )
    _isolate_repo_root(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime_env, "_DOTENV_LOADED", False)
    with caplog.at_level(logging.INFO, logger=runtime_env.__name__):
        with patch.dict(os.environ, {}, clear=True):
            loaded = runtime_env.load_dotenv_files()

            assert loaded == []
            assert "DEEPSEEK_API_KEY" not in os.environ
            assert "SHADOW_WEB_LLM_BASE_URL" not in os.environ
    # Ignoring a cwd .env must be visible in the logs, not silent.
    assert str(env_file.resolve()) in caplog.text
    assert "ignoring" in caplog.text


def test_explicit_env_file_is_loaded_and_can_provide_llm_key(tmp_path, monkeypatch):
    explicit = tmp_path / "custom.env"
    explicit.write_text("DEEPSEEK_API_KEY=test-key\n", encoding="utf-8")
    hijack = tmp_path / ".env"
    hijack.write_text(
        "DEEPSEEK_API_KEY=attacker-key\nSHADOW_WEB_CWD_HIJACK=1\n", encoding="utf-8"
    )
    _isolate_repo_root(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    # An explicit path must still load even after the implicit pass was memoized.
    monkeypatch.setattr(runtime_env, "_DOTENV_LOADED", True)
    with patch.dict(os.environ, {}, clear=True):
        loaded = runtime_env.load_dotenv_files([explicit])

        assert os.environ["DEEPSEEK_API_KEY"] == "test-key"
        assert "SHADOW_WEB_CWD_HIJACK" not in os.environ
        assert loaded == [str(explicit.resolve())]


def test_repository_root_dotenv_discovery_still_works(tmp_path, monkeypatch, caplog):
    repo = tmp_path / "project"
    (repo / "src" / "shadow_web").mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    repo_env = repo / ".env"
    repo_env.write_text("DEEPSEEK_API_KEY=test-repo-key\n", encoding="utf-8")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".env").write_text("SHADOW_WEB_CWD_HIJACK=1\n", encoding="utf-8")

    monkeypatch.setattr(
        runtime_env, "__file__", str(repo / "src" / "shadow_web" / "runtime_env.py")
    )
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(runtime_env, "_DOTENV_LOADED", False)
    with caplog.at_level(logging.INFO, logger=runtime_env.__name__):
        with patch.dict(os.environ, {}, clear=True):
            loaded = runtime_env.load_dotenv_files()

            assert os.environ["DEEPSEEK_API_KEY"] == "test-repo-key"
            assert "SHADOW_WEB_CWD_HIJACK" not in os.environ
            assert loaded == [str(repo_env.resolve())]
    # Which file was loaded is logged, so a hijack is at least visible.
    assert str(repo_env.resolve()) in caplog.text


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

    env_file = tmp_path / "custom.env"
    env_file.write_text("DEEPSEEK_API_KEY=test-key-from-dotenv\n", encoding="utf-8")
    for name in runtime_env.LLM_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SHADOW_WEB_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(runtime_env, "_DOTENV_LOADED", False)
    runtime_env.load_dotenv_files([env_file])
    # Memoize so resolve_api_credentials() does not re-discover a machine .env.
    monkeypatch.setattr(runtime_env, "_DOTENV_LOADED", True)
    key, url = resolve_api_credentials()
    assert key == "test-key-from-dotenv"
    assert url == "https://api.deepseek.com"
