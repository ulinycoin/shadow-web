import asyncio
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from openai import OpenAI

from shadow_web.compressor import process_html
from shadow_web.verified_heal import verify_selector_in_html

# Lightweight manual .env loader
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), "../.env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()

load_env()

app = FastAPI(
    title="Shadow Web API",
    description="Stateless DOM compression and verified self-healing selector APIs for AI Agents.",
    version="2.0.0",
)

# --- Rate limit (in-memory, per API key) ---
_RATE_BUCKETS: Dict[str, List[float]] = defaultdict(list)
_RATE_LIMIT = int(os.environ.get("SHADOW_WEB_RATE_LIMIT", "100"))
_RATE_WINDOW_SEC = 86400  # 24h


def _check_rate_limit(key: str) -> None:
    if _RATE_LIMIT <= 0:
        return
    now = time.time()
    bucket = _RATE_BUCKETS[key]
    _RATE_BUCKETS[key] = [ts for ts in bucket if now - ts < _RATE_WINDOW_SEC]
    if len(_RATE_BUCKETS[key]) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _RATE_BUCKETS[key].append(now)


# API Schemas
class CompressRequest(BaseModel):
    html: str

class CompressResponse(BaseModel):
    clean_html: str
    action_map: List[Dict[str, Any]]
    groups: List[Dict[str, Any]]

class HealRequest(BaseModel):
    broken_selector: str
    context_html: str
    action_label: str
    action_type: str
    verify: bool = True

class HealResponse(BaseModel):
    selector: str
    verified: bool = False
    source: str = "llm"


def verify_api_key(authorization: Optional[str] = Header(None)) -> str:
    """Optional API key auth. Empty key allowed for local dev."""
    allowed = os.environ.get("SHADOW_WEB_API_KEYS", "").strip()
    if not allowed:
        return authorization or "local"

    keys = {k.strip() for k in allowed.split(",") if k.strip()}
    token = (authorization or "").replace("Bearer ", "").strip()
    if not token or token not in keys:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token


def get_llm_client():
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    api_base = os.environ.get("DEEPSEEK_API_BASE") or "https://api.deepseek.com"
    if not api_key:
        return None
    return OpenAI(api_key=api_key, base_url=api_base)


def _mock_selector(req: HealRequest) -> str:
    tag = req.action_type.split("[")[0]
    classes = re.findall(r'class="([^"]+)"', req.context_html)
    if classes:
        return f"{tag}.{classes[0].split()[0]}"
    return tag


def _llm_heal_selector(client: OpenAI, req: HealRequest) -> str:
    system_prompt = (
        "You are an expert web crawler repair system. Your task is to identify the corrected "
        "CSS selector for an element that has changed its attributes due to a website design update. "
        "The selector must be a valid standard CSS selector compatible with document.querySelectorAll "
        "(do NOT use non-standard extensions like :contains, :has-text, or text search; rely only on standard CSS like tags, classes, IDs, and attributes). "
        "Respond ONLY with the raw CSS selector string. No markdown block, no explanation, no quotes."
    )
    user_prompt = f"""
The original CSS selector '{req.broken_selector}' is now broken (element not found).
We need to target a '{req.action_type}' element that has the label/placeholder/text: '{req.action_label}'.

Here is the HTML snippet of the parent container containing the new version of this element:
```html
{req.context_html}
```

Find the most specific and reliable standard CSS selector that targets this element in the HTML context.
Provide ONLY the CSS selector (e.g., 'button.checkout-submit' or 'input[name="email"]'). Do not wrap in markdown tags. Do not use pseudo-classes like :contains or text matching.
"""
    model = os.environ.get("LLM_MODEL") or "deepseek-chat"
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=100,
    )
    healed_selector = response.choices[0].message.content.strip()
    healed_selector = re.sub(r"^(```css|```html|```)\s*", "", healed_selector)
    healed_selector = re.sub(r"\s*```$", "", healed_selector)
    return healed_selector


@app.post("/v1/compress", response_model=CompressResponse)
async def compress_html_endpoint(
    req: CompressRequest,
    api_key: str = Depends(verify_api_key),
):
    _check_rate_limit(api_key)
    try:
        clean_html, action_map, groups = process_html(req.html)
        return CompressResponse(clean_html=clean_html, action_map=action_map, groups=groups)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to process HTML: {str(e)}")


@app.post("/v1/heal", response_model=HealResponse)
async def heal_selector_endpoint(
    req: HealRequest,
    api_key: str = Depends(verify_api_key),
):
    """
    LLM heal with optional Playwright verification on ``context_html``.

    When ``verify=True``, the server loads context HTML in headless Chromium
    and rejects selectors that do not resolve to a visible matching element.
    """
    _check_rate_limit(api_key)
    client = get_llm_client()

    if not client:
        print("[Shadow Web Server] WARNING: No API key configured. Using mock selector.")
        selector = _mock_selector(req)
        source = "mock"
    else:
        try:
            selector = _llm_heal_selector(client, req)
            source = "llm"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LLM healing request failed: {str(e)}")

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
    else:
        verified = True

    return HealResponse(selector=selector, verified=verified, source=source)
