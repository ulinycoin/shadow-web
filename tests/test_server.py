import os
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("openai")

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

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
    with patch.dict(os.environ, {}, clear=True):
        first = verify_api_key(_request("127.0.0.1"), "Bearer attacker-a")
        second = verify_api_key(_request("127.0.0.1"), "Bearer attacker-b")

    assert first == second == "local"


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
