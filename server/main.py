import os
import re
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from openai import OpenAI

from shadow_web.compressor import process_html

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

# Load local environment variables
load_env()

app = FastAPI(
    title="Shadow Web API",
    description="Stateless DOM compression and self-healing selector APIs for AI Agents.",
    version="1.0.0"
)

# API Schemas
class CompressRequest(BaseModel):
    html: str

class CompressResponse(BaseModel):
    clean_html: str
    action_map: List[Dict[str, Any]]

class HealRequest(BaseModel):
    broken_selector: str
    context_html: str
    action_label: str
    action_type: str

class HealResponse(BaseModel):
    selector: str

# Auth dependency (optional placeholder for monetization)
def verify_api_key(authorization: Optional[str] = Header(None)):
    # In production, look up authorization token in database
    # For local/testing, we allow empty token or any token
    return True

# Initialize OpenAI-compatible client for DeepSeek
def get_llm_client():
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    api_base = os.environ.get("DEEPSEEK_API_BASE") or "https://api.deepseek.com"
    
    if not api_key:
        # Fallback to local testing mockup if no keys provided
        return None
        
    return OpenAI(api_key=api_key, base_url=api_base)

@app.post("/v1/compress", response_model=CompressResponse, dependencies=[Depends(verify_api_key)])
async def compress_html_endpoint(req: CompressRequest):
    """Stateless endpoint that accepts raw HTML and returns clean HTML and an Action Map."""
    try:
        clean_html, action_map = process_html(req.html)
        return CompressResponse(clean_html=clean_html, action_map=action_map)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to process HTML: {str(e)}")

@app.post("/v1/heal", response_model=HealResponse, dependencies=[Depends(verify_api_key)])
async def heal_selector_endpoint(req: HealRequest):
    """
    Queries an LLM to find the repaired CSS selector based on context HTML 
    and element attributes when the original selector breaks.
    """
    client = get_llm_client()
    
    # System & User prompts for self-healing
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

    if not client:
        # Mockup response for local testing if API key is not configured
        print("[Shadow Web Server] WARNING: No API key configured. Returning best-guess selector from context HTML.")
        tag = req.action_type.split('[')[0]
        classes = re.findall(r'class="([^"]+)"', req.context_html)
        if classes:
            first_class = classes[0].split()[0]
            mock_selector = f"{tag}.{first_class}"
        else:
            mock_selector = tag
        return HealResponse(selector=mock_selector)

    try:
        # Use deepseek-chat (or alternative model specified via env)
        model = os.environ.get("LLM_MODEL") or "deepseek-chat"
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0,
            max_tokens=100
        )
        
        healed_selector = response.choices[0].message.content.strip()
        # Clean up possible Markdown ticks ```css ... ``` returned by LLM
        healed_selector = re.sub(r'^(```css|```html|```)\s*', '', healed_selector)
        healed_selector = re.sub(r'\s*```$', '', healed_selector)
        
        return HealResponse(selector=healed_selector)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM healing request failed: {str(e)}")
