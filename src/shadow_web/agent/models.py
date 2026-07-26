"""OpenAI-compatible chat + tools adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


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
    """Resolve API key and optional base_url from args / environment."""
    from shadow_web.runtime_env import load_dotenv_files, missing_llm_key_message

    load_dotenv_files()
    key = api_key or (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("SHADOW_WEB_LLM_API_KEY")
    )
    if not key:
        raise RuntimeError(missing_llm_key_message())

    url = base_url or os.environ.get("SHADOW_WEB_LLM_BASE_URL")
    if url is None and os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        url = "https://api.deepseek.com"
    if url is None and os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        url = "https://openrouter.ai/api/v1"
    return key, url


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
        kwargs: Dict[str, Any] = {"api_key": key}
        if url:
            kwargs["base_url"] = url
        self._client = OpenAI(**kwargs)

    def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Dict[str, Any]],
    ) -> ModelResponse:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[m.to_api() for m in messages],
            tools=list(tools) if tools else None,
            tool_choice="auto" if tools else None,
            temperature=self.temperature,
        )
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
