# Integration with browser-use (Shadow Web)

This example demonstrates how to use **Shadow Web** (`ShadowTools`) inside **browser-use**, the most popular framework for AI web browsing.

---

## Why use this integration?

1. **Save up to 80-90% of tokens:** The agent receives a compact **terse Action Map** by default (full grouped XML available via `format="xml"`).
2. **Self-Healing Selectors:** The agent interacts with elements using their unique `data-sid` (binding identifiers). If the page layout changes, `ShadowTools` transparently heals broken selectors locally (fuzzy match) or via the cloud API, without interrupting the agent's execution.
3. **Shadow DOM & Iframe Support:** Allows `browser-use` to see and interact with elements inside open Shadow Roots and same-origin iframes.

---

## Quick Start

### 1. Install Dependencies

Install `shadow-web` in editable mode, along with `browser-use` and `langchain-openai`:

```bash
pip install -e ".[browser-use]"
# or: pip install "shadow-web[browser-use]"
pip install langchain-openai
```

### 2. Configure Environment

Set up your API keys for the LLM and the healing service in your `.env` file or export them in your terminal:

```bash
export OPENAI_API_KEY="your-openai-key"
# If you are using the self-healing API:
export SHADOW_WEB_HEAL_URL="http://localhost:8000/v1/heal"
```

### 3. Run the Demo

Run the example script:

```bash
python examples/browser_use/demo.py
```

---

## How It Works

In the [demo.py](./demo.py) example, integration takes only a few lines:

```python
from browser_use import Agent
from shadow_web import ShadowTools

# Initialize optimized tools (terse by default; use default_format="xml" for full grouped XML)
tools = ShadowTools()

# Pass them to the agent
agent = Agent(
    task="Go to news.ycombinator.com and search for 'Agent' using our custom shadow tools.",
    llm=llm,
    tools=tools
)
```

`ShadowTools` automatically:
* Disables `browser-use`'s default `click_element` and `input_text` actions (which cannot interact with Shadow DOM or read the compressed tree).
* Registers `get_xml_action_map` (supports `query` + `format`), `click_shadow_element`, and `fill_shadow_element` instead.
* Under the hood, binds `AsyncShadowPage` to active browser session tabs for DOM compression and local fuzzy self-healing.
