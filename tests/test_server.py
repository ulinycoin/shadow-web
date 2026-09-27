import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("openai")

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

import shadow_web
import shadow_web.runtime_env as runtime_env
import shadow_web.server as server_module
from shadow_web.server import (
    CompressRequest,
    HealRequest,
    get_llm_settings,
    verify_api_key,
)


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/compress",
            "headers": [],
            "client": (host, 12345),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_openai_key_uses_openai_endpoint_and_model():
    with patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "openai-secret"},
        clear=True,
    ):
        settings = get_llm_settings()

    assert settings == {
        "provider": "openai",
        "api_key": "openai-secret",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    }


def test_deepseek_key_uses_deepseek_endpoint_and_model():
    with patch.dict(
        os.environ,
        {"DEEPSEEK_API_KEY": "deepseek-secret"},
        clear=True,
    ):
        settings = get_llm_settings()

    assert settings == {
        "provider": "deepseek",
        "api_key": "deepseek-secret",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    }


def test_production_rejects_missing_api_key_configuration():
    with patch.dict(os.environ, {"SHADOW_WEB_ENV": "production"}, clear=True):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(_request("127.0.0.1"), None)

    assert exc_info.value.status_code == 503


def test_remote_request_rejects_missing_api_key_configuration():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(_request("203.0.113.10"), "Bearer attacker-bucket")

    assert exc_info.value.status_code == 503


def test_local_development_uses_one_fixed_rate_bucket():
    with patch.dict(
        os.environ,
        {"SHADOW_WEB_ALLOW_UNAUTHENTICATED_LOCAL": "1"},
        clear=True,
    ):
        first = verify_api_key(_request("127.0.0.1"), "Bearer attacker-a")
        second = verify_api_key(_request("127.0.0.1"), "Bearer attacker-b")

    assert first == second == "local"


def test_local_development_without_explicit_flag_refuses_unauthenticated():
    # Client IP is not a security boundary: without the explicit opt-in flag,
    # even a loopback request must be refused when no API key is configured.
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(_request("127.0.0.1"), None)

    assert exc_info.value.status_code == 503


def test_configured_bearer_key_is_required():
    with patch.dict(os.environ, {"SHADOW_WEB_API_KEYS": "alpha,beta"}, clear=True):
        assert verify_api_key(_request("203.0.113.10"), "Bearer beta") == "beta"
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(_request("203.0.113.10"), "Bearer wrong")

    assert exc_info.value.status_code == 401


def test_request_models_limit_expensive_payloads():
    with pytest.raises(ValidationError):
        CompressRequest(html="x" * 2_000_001)
    with pytest.raises(ValidationError):
        HealRequest(
            broken_selector="#old",
            context_html="x" * 200_001,
            action_type="button",
        )


def test_server_has_no_second_dotenv_loader():
    # M14: server.load_env() duplicated (and diverged from) runtime_env.
    assert not hasattr(server_module, "load_env")
    assert server_module.load_dotenv_files is runtime_env.load_dotenv_files


def test_server_import_uses_shared_env_discovery(tmp_path):
    """Importing the server loads the repo-root .env, not a cwd .env (B3/M14).

    Runs in a subprocess against a copied package so repository-root discovery
    is unambiguous and independent of this checkout's real .env.
    """
    repo = tmp_path / "project"
    (repo / "src").mkdir(parents=True)
    shutil.copytree(
        Path(shadow_web.__file__).resolve().parent,
        repo / "src" / "shadow_web",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (repo / ".env").write_text(
        'export DEEPSEEK_API_KEY="test-repo-key"\n'
        "SHADOW_WEB_LLM_BASE_URL=https://repo.example/v1\n",
        encoding="utf-8",
    )
    attacker = tmp_path / "cloned-checkout"
    attacker.mkdir()
    (attacker / ".env").write_text(
        "DEEPSEEK_API_KEY=attacker-key\n"
        "SHADOW_WEB_LLM_BASE_URL=https://attacker.example/v1\n",
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            *runtime_env.LLM_KEY_NAMES,
            "SHADOW_WEB_LLM_BASE_URL",
            "DEEPSEEK_API_BASE",
            "OPENAI_API_BASE",
        }
    }
    env["PYTHONPATH"] = str(repo / "src")
    script = (
        "import os, shadow_web.server;"
        "print(os.environ.get('DEEPSEEK_API_KEY', ''));"
        "print(os.environ.get('SHADOW_WEB_LLM_BASE_URL', ''))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(attacker),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["test-repo-key", "https://repo.example/v1"]


# ── Endpoint hardening: body size, error text, heal concurrency ──────


def _endpoint_client(monkeypatch):
    """A TestClient in the keyless local mode used by tests."""
    from fastapi.testclient import TestClient

    import shadow_web.server as srv

    monkeypatch.setenv("SHADOW_WEB_ALLOW_UNAUTHENTICATED_LOCAL", "1")
    monkeypatch.delenv("SHADOW_WEB_API_KEYS", raising=False)
    return srv, TestClient(srv.app)


def test_oversized_body_is_rejected_before_buffering(monkeypatch):
    srv, client = _endpoint_client(monkeypatch)
    monkeypatch.setattr(srv, "_MAX_REQUEST_BYTES", 200)

    response = client.post("/v1/compress", json={"html": "x" * 2_000})

    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()


def test_small_body_still_reaches_the_endpoint(monkeypatch):
    srv, client = _endpoint_client(monkeypatch)
    monkeypatch.setattr(srv, "_MAX_REQUEST_BYTES", 1_000_000)

    response = client.post("/v1/compress", json={"html": "<p>hello</p>"})

    assert response.status_code == 200
    assert "hello" in response.json()["clean_html"]


def test_process_failure_does_not_leak_internal_exception_text(monkeypatch):
    srv, client = _endpoint_client(monkeypatch)

    def boom(_html):
        raise RuntimeError("internal detail: /opt/secret/path and api_key=abc")

    monkeypatch.setattr(srv, "process_html", boom)

    response = client.post("/v1/compress", json={"html": "<p>x</p>"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "internal detail" not in detail
    assert "secret" not in detail


def test_heal_endpoint_fails_fast_when_saturated(monkeypatch):
    import asyncio as _asyncio

    srv, client = _endpoint_client(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    # No permits available: the handler must answer 429 instead of queueing.
    monkeypatch.setattr(srv, "_heal_slots", _asyncio.Semaphore(0))

    response = client.post(
        "/v1/heal",
        json={
            "broken_selector": "#old",
            "context_html": "<div><button>Pay</button></div>",
            "action_label": "Pay",
            "action_type": "button",
        },
    )

    assert response.status_code == 429
    assert "concurrent" in response.json()["detail"].lower()


def test_heal_llm_failure_does_not_leak_provider_error(monkeypatch):
    srv, client = _endpoint_client(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(srv, "get_llm_client", lambda: object())

    def boom(*_args, **_kwargs):
        raise RuntimeError("provider said: invalid key sk-live-abcdef123456")

    monkeypatch.setattr(srv, "_llm_heal_selector", boom)

    response = client.post(
        "/v1/heal",
        json={
            "broken_selector": "#old",
            "context_html": "<div><button>Pay</button></div>",
            "action_label": "Pay",
            "action_type": "button",
            "verify": False,
        },
    )

    assert response.status_code == 502
    assert "sk-live" not in response.text
    assert "provider said" not in response.text


def test_heal_client_is_bounded(monkeypatch):
    import shadow_web.server as srv

    captured: dict = {}

    class _StubClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # server.py does `from openai import OpenAI`, so the module attribute is
    # what the endpoint actually calls.
    monkeypatch.setattr(srv, "OpenAI", _StubClient)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SHADOW_WEB_HEAL_TIMEOUT", "12")

    srv.get_llm_client()

    assert captured["timeout"] == 12.0
    assert captured["max_retries"] == 1
