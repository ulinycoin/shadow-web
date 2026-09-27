"""OpenAI-compatible chat + tools adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


def _env_int(name: str, default: int) -> int:
    """Defensive env parsing: a malformed value must never break the adapter."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass
class ChatMessage:
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def to_api(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls is not None:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            msg["name"] = self.name
        return msg


@dataclass
class ModelResponse:
    content: Optional[str]
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    raw: Any = None


def resolve_api_credentials(
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Resolve API key and optional base_url from args / environment.

    When explicit args are given they always win. Without them, each
    provider is resolved as an *atomic* (key, default_url) pair so a
    DeepSeek key never lands on the OpenAI endpoint and vice versa.
    """
    from shadow_web.runtime_env import load_dotenv_files, missing_llm_key_message

    load_dotenv_files()

    # Explicit args → no inference
    if base_url or api_key:
        key = api_key or (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("SHADOW_WEB_LLM_API_KEY")
        )
        if not key:
            raise RuntimeError(missing_llm_key_message())
        url = base_url or os.environ.get("SHADOW_WEB_LLM_BASE_URL")
        return key, url

    # No explicit args: resolve provider + default URL as an atomic pair.
    # Order: DeepSeek → OpenAI → OpenRouter → SHADOW_WEB_LLM (generic).
    # SHADOW_WEB_LLM_BASE_URL, when set, overrides the provider default —
    # so a DeepSeek key can safely hit a compatible proxy.
    providers: list[tuple[str, Optional[str]]] = [
        ("DEEPSEEK_API_KEY", "https://api.deepseek.com"),
        ("OPENAI_API_KEY", None),  # OpenAI SDK default
        ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    ]
    custom_url = os.environ.get("SHADOW_WEB_LLM_BASE_URL")
    for env_key, default_url in providers:
        key = os.environ.get(env_key)
        if key:
            return key, custom_url if custom_url else default_url

    # Last resort: SHADOW_WEB_LLM_API_KEY + optional SHADOW_WEB_LLM_BASE_URL
    key = os.environ.get("SHADOW_WEB_LLM_API_KEY")
    if key:
        return key, custom_url

    raise RuntimeError(missing_llm_key_message())


class OpenAICompatibleAdapter:
    """Thin wrapper around the OpenAI SDK (works with DeepSeek / OpenRouter)."""

    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
    ):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                'Install the agent extra: pip install "shadow-web[agent]"'
            ) from exc

        key, url = resolve_api_credentials(api_key=api_key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        # Bound every HTTP call: the SDK default is minutes per attempt with
        # retries, which would stall an agent run (and its browser) for a long
        # time, and the per-call budget check only runs afterwards.
        timeout_s = _env_float("SHADOW_WEB_LLM_TIMEOUT", 60.0)
        max_retries = _env_int("SHADOW_WEB_LLM_MAX_RETRIES", 1)
        self.max_tokens = _env_int("SHADOW_WEB_LLM_MAX_TOKENS", 4096)
        kwargs: Dict[str, Any] = {
            "api_key": key,
            "timeout": timeout_s,
            "max_retries": max_retries,
        }
        if url:
            kwargs["base_url"] = url
        self._client = OpenAI(**kwargs)

    def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Dict[str, Any]],
    ) -> ModelResponse:
        request: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_api() for m in messages],
            "tools": list(tools) if tools else None,
            "tool_choice": "auto" if tools else None,
            "temperature": self.temperature,
        }
        # Cap a single completion so one verbose answer cannot blow the run budget.
        if self.max_tokens > 0:
            request["max_tokens"] = self.max_tokens
        response = self._client.chat.completions.create(**request)
        choice = response.choices[0].message
        tool_calls: List[Dict[str, Any]] = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                )
        usage: Dict[str, int] = {}
        if response.usage:
            usage = {
                "prompt_tokens": int(response.usage.prompt_tokens or 0),
                "completion_tokens": int(response.usage.completion_tokens or 0),
                "total_tokens": int(response.usage.total_tokens or 0),
            }
        return ModelResponse(
            content=choice.content,
            tool_calls=tool_calls,
            usage=usage,
            raw=response,
        )
