"""FastAPI service for DOM compression and verified selector healing."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, Field

from shadow_web.compressor import process_html
from shadow_web.verified_heal import verify_selector_in_html


def load_env() -> None:
    """Load a repository-local .env without replacing explicit environment values."""
    env_path = os.path.join(os.path.dirname(__file__), "../../.env")
    env_path = os.path.abspath(env_path)
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env()

app = FastAPI(
    title="Shadow Web API",
    description="Stateless DOM compression and verified self-healing selector APIs for AI agents.",
    version="0.3.5",
)

_RATE_BUCKETS: Dict[str, List[float]] = defaultdict(list)
_RATE_LOCK = threading.Lock()
_RATE_LIMIT = int(os.environ.get("SHADOW_WEB_RATE_LIMIT", "100"))
_RATE_WINDOW_SEC = 86400


class CompressRequest(BaseModel):
    html: str = Field(max_length=2_000_000)


class CompressResponse(BaseModel):
    clean_html: str
    action_map: List[Dict[str, Any]]
    groups: List[Dict[str, Any]]


class HealRequest(BaseModel):
    broken_selector: str = Field(min_length=1, max_length=2_000)
    context_html: str = Field(min_length=1, max_length=200_000)
    action_label: str = Field(default="", max_length=2_000)
    action_type: str = Field(min_length=1, max_length=200)
    verify: bool = True


class HealResponse(BaseModel):
    selector: str
    verified: bool = False
    source: str = "llm"


def _check_rate_limit(key: str) -> None:
    if _RATE_LIMIT <= 0:
        return
    now = time.time()
    with _RATE_LOCK:
        bucket = [ts for ts in _RATE_BUCKETS[key] if now - ts < _RATE_WINDOW_SEC]
        if len(bucket) >= _RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        bucket.append(now)
        _RATE_BUCKETS[key] = bucket


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def verify_api_key(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> str:
    """Require configured bearer keys for every non-local request."""
    allowed_raw = os.environ.get("SHADOW_WEB_API_KEYS", "").strip()
    if not allowed_raw:
        environment = os.environ.get("SHADOW_WEB_ENV", "development").lower()
        if environment in {"production", "prod"} or not _is_local_request(request):
            raise HTTPException(
                status_code=503,
                detail="API authentication is not configured",
            )
        return "local"

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid API key")

    allowed = [key.strip() for key in allowed_raw.split(",") if key.strip()]
    if not any(secrets.compare_digest(token.strip(), key) for key in allowed):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token.strip()


def get_llm_settings() -> Optional[dict[str, str]]:
    """Select one provider without ever sending its key to another provider."""
    requested = os.environ.get("LLM_PROVIDER", "").strip().lower()
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if requested not in {"", "deepseek", "openai"}:
        raise RuntimeError("LLM_PROVIDER must be 'deepseek' or 'openai'")

    provider = requested or ("deepseek" if deepseek_key else "openai" if openai_key else "")
    if provider == "deepseek" and deepseek_key:
        return {
            "provider": "deepseek",
            "api_key": deepseek_key,
            "base_url": os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
            "model": os.environ.get("DEEPSEEK_MODEL")
            or os.environ.get("LLM_MODEL")
            or "deepseek-chat",
        }
    if provider == "openai" and openai_key:
        return {
            "provider": "openai",
            "api_key": openai_key,
            "base_url": os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
            "model": os.environ.get("OPENAI_MODEL")
            or os.environ.get("LLM_MODEL")
            or "gpt-4o-mini",
        }
    return None


def get_llm_client() -> Optional[OpenAI]:
    settings = get_llm_settings()
    if not settings:
        return None
    return OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])


def _llm_heal_selector(client: OpenAI, req: HealRequest, model: str) -> str:
    system_prompt = (
        "You repair CSS selectors after website changes. Return one valid standard CSS selector "
        "compatible with document.querySelectorAll. Do not use :contains, :has-text, text search, "
        "markdown, quotes, or explanations."
    )
    user_prompt = f"""
The original CSS selector '{req.broken_selector}' is broken.
Target type: '{req.action_type}'.
Target label, placeholder, or text: '{req.action_label}'.

Parent HTML:
```html
{req.context_html}
```

Return the most specific reliable standard CSS selector only.
"""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=100,
    )
    content = response.choices[0].message.content or ""
    selector = re.sub(r"^(```css|```html|```)\s*", "", content.strip())
    selector = re.sub(r"\s*```$", "", selector).strip()
    if not selector:
        raise RuntimeError("LLM returned an empty selector")
    return selector


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.post("/v1/compress", response_model=CompressResponse)
async def compress_html_endpoint(
    req: CompressRequest,
    api_key: str = Depends(verify_api_key),
) -> CompressResponse:
    _check_rate_limit(api_key)
    try:
        clean_html, action_map, groups = await asyncio.to_thread(process_html, req.html)
        return CompressResponse(clean_html=clean_html, action_map=action_map, groups=groups)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to process HTML: {exc}") from exc


@app.post("/v1/heal", response_model=HealResponse)
async def heal_selector_endpoint(
    req: HealRequest,
    api_key: str = Depends(verify_api_key),
) -> HealResponse:
    _check_rate_limit(api_key)
    settings = get_llm_settings()
    if not settings:
        raise HTTPException(status_code=503, detail="LLM API key is not configured")

    client = get_llm_client()
    if client is None:
        raise HTTPException(status_code=503, detail="LLM API key is not configured")
    try:
        selector = await asyncio.to_thread(
            _llm_heal_selector,
            client,
            req,
            settings["model"],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM healing request failed: {exc}") from exc

    verified = False
    if req.verify:
        verified = await asyncio.to_thread(
            verify_selector_in_html,
            req.context_html,
            selector,
            req.action_label,
            req.action_type,
        )
        if not verified:
            raise HTTPException(
                status_code=422,
                detail=f"Selector failed Playwright verification: {selector}",
            )

    return HealResponse(selector=selector, verified=verified, source="llm")


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError(
            "Server support requires the server extra: pip install 'shadow-web[server]'"
        ) from exc
    uvicorn.run(
        "shadow_web.server:app",
        host=os.environ.get("SHADOW_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("SHADOW_WEB_PORT", "8000")),
    )
