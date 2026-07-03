# Search Engine Bypass Strategies for AI Agents

When automating searches on Google/Yandex directly through a headless browser, you will quickly run into aggressive protection (CAPTCHA, Cloudflare, IP blocks). Search engines instantly detect automation signals (e.g., the `navigator.webdriver` property).

Below are 4 effective architectural approaches to solving this problem when working with `shadow-web` and Playwright.

---

## Option 1. Switch to DuckDuckGo HTML / Lite (Recommended for scraping)

The standard DuckDuckGo version requires JavaScript, but they have official "light" versions with no JS and no heavy bot protection. They return clean HTML that compresses perfectly with `shadow-web`.

### Search URLs:
* **DuckDuckGo HTML**: `https://html.duckduckgo.com/html/?q=QUERY`
* **DuckDuckGo Lite**: `https://lite.duckduckgo.com/lite/`

### Usage example:

```python
# Navigate directly to DuckDuckGo HTML search results
query = "Ferrari site:ss.com"
url = f"https://html.duckduckgo.com/html/?q={query}"

await page.goto(url)
clean_html, xml_map = await shadow.refresh()

# The agent can now read compressed XML results — no CAPTCHA, no JS
```

---

## Option 2. Use a Search API (Most reliable for production)

Instead of scraping search results through a browser, the agent can use dedicated APIs that return structured JSON. This saves enormous amounts of tokens, time, and works with 100% reliability.

### Recommended services:
1. **Tavily API** — built specifically for LLM agents (returns clean text and filtered answers).
2. **Brave Search API** — free tier and very cheap pricing, excellent Google alternative.
3. **SerpAPI / Serper.dev** — proxy real Google/Google Shopping/Yandex results without blocks.

### Tavily integration example:

```python
from tavily import TavilyClient

tavily = TavilyClient(api_key="your_tavily_key")
response = tavily.search(query="Ferrari site:ss.com", search_depth="basic")

for result in response['results']:
    print(f"Title: {result['title']}\nURL: {result['url']}\nSnippet: {result['content']}\n")
```

---

## Option 3. Basic Stealth Configuration in Playwright

If you absolutely must search through Google/Yandex in the browser, you need to hide automation signals.

Add the following configuration when initializing the browser context (e.g., in `src/shadow_web/mcp/server.py`):

```python
async def _ensure_browser():
    from playwright.async_api import async_playwright

    if "playwright" not in _session:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        
        # Realistic context configuration
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            locale="en-US",
            timezone_id="Europe/Riga",
        )
        
        # Hide navigator.webdriver (basic check on most sites)
        await context.add_init_script("delete navigator.__proto__.webdriver;")
        
        page = await context.new_page()
        _session["playwright"] = pw
        _session["browser"] = browser
        _session["context"] = context
        _session["page"] = page
```

---

## Option 4. Use `playwright-stealth` Package

For more advanced evasion of detection systems (Cloudflare, Akamai, PerimeterX), use the `playwright-stealth` wrapper. It spoofs plugin fingerprints, codecs, GPU, and WebGL parameters.

### Installation:
```bash
pip install playwright-stealth
```

### Integration:

```python
from playwright_stealth import stealth_async

context = await browser.new_context()
page = await context.new_page()

# Apply full stealth masking to the page
await stealth_async(page)

# Now you can navigate to bot-protected sites
await page.goto("https://google.com")
```
