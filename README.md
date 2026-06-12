# Shadow Web: Shift-Left Web SDK & API for AI Agents

Shadow Web is an open-source, lightweight web interaction suite designed explicitly for LLM/AI Agents. It strips 90%+ of HTML bloat (Tailwind CSS, SVG paths, track scripts) on the client side, generates a concise interactive **Action Map**, and exposes high-margin microservices for reliability (Self-Healing Selectors) and low-barrier use cases (Compression API).

## Why Shadow Web?

1. **Massive Token Savings:** Reduces raw HTML payload size by up to 33x on complex web pages (e.g., GitHub, Amazon), saving up to 90% in LLM input token costs.
2. **Action Map Abstraction:** Translates visual DOM elements into logical XML actions, allowing LLMs to interact with pages using simple IDs instead of managing complex CSS selectors.
3. **No Infrastructure Overhead (MIT SDK):** The DOM parsing and Action Map generation run locally in the agent's environment. Zero browser execution or proxy costs for the SDK developers.

---

## Architectural Blueprint

```
                     +-----------------------------------+
                     |           Client Machine          |
                     |                                   |
                     |  [Playwright] --> raw HTML        |
                     |         |                         |
                     |         v                         |
                     |  [shadow-web SDK (lxml)]          |
                     |   - DOM Striping                  |
                     |   - data-sid Injection            |
                     |   - Action Map Builder            |
                     |         |                         |
                     |         +----> XML Payload        |
                     |         |      (to local LLM)     |
                     +---------|-------------------------+
                               |
                   IF SELECTOR | (e.g. data-sid="2" is broken)
                   IS BROKEN   v
                     +-----------------------------------+
                     |        Shadow Web Cloud API       |
                     |                                   |
                     |  POST /v1/heal                    |
                     |  - Cheap DeepSeek Reasoning       |
                     |  - Returns repaired CSS selector  |
                     +-----------------------------------+
```

---

## Folder Structure

* `src/shadow_web/`: Local Python SDK.
  * `compressor.py`: Core DOM parsing, stripping, and Action Map XML builder.
  * `wrapper.py`: Playwright integration wrapper that intercepts pages and enables execution via `data-sid` elements.
* `server/`: FastAPI microservices backend.
  * `/v1/compress`: Stateless HTML compressor for environments without the Python SDK.
  * `/v1/heal`: AI-powered CSS selector recovery utilizing cheap LLMs.
* `tests/`: Automated unit tests validating compression and wrappers.

---

## Installation & Setup

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the FastAPI Local Server:**
   ```bash
   uvicorn server.main:app --reload --port 8000
   ```

3. **Run Unit Tests:**
   ```bash
   python3 -m unittest discover -s tests
   ```

---

## Core Usage Examples

### 1. Stripping DOM & Generating Action Maps (Local)

```python
from shadow_web.compressor import process_html, generate_xml_map

raw_html = "<html><body><div class='p-4'><a href='/buy'>Buy Now</a></div></body></html>"
clean_html, action_map = process_html(raw_html)

print(clean_html)
# Output: <body><div><a href="/buy" data-sid="1">Buy Now</a></div></body>

print(action_map)
# Output: [{'id': '1', 'type': 'a', 'label': 'Buy Now', 'href': '/buy'}]
```

### 2. Self-Healing Selector API (Cloud)

If a selector changes on the target site, the SDK catches the error, takes a small HTML context snapshot around the element, and requests a fix:

```bash
curl -X POST http://localhost:8000/v1/heal \
  -H "Content-Type: application/json" \
  -d '{
    "broken_selector": "button[data-sid=\"2\"]",
    "context_html": "<div><button class=\"new-checkout-btn\">Checkout</button></div>",
    "action_label": "Checkout",
    "action_type": "button"
  }'
```

Returns:
```json
{
  "selector": "button.new-checkout-btn"
}
```

---

## License

MIT License. Free for development and commercial use.
