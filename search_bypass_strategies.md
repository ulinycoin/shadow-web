# Стратегии обхода блокировок поисковых систем для AI-агентов

При автоматизации поиска в Google/Yandex напрямую через headless-браузер вы быстро столкнетесь с жесткой защитой (CAPTCHA, Cloudflare, блокировки IP). Поисковые системы мгновенно выявляют сигнатуры автоматизации (например, свойство `navigator.webdriver`).

Ниже приведены 4 эффективных архитектурных сценария решения этой проблемы при работе с `shadow-web` и Playwright.

---

## Вариант 1. Переход на DuckDuckGo HTML / Lite (Рекомендуемый для парсинга)

Обычная версия DuckDuckGo требует выполнения JS, но у них есть официальные «легкие» версии без JS и тяжелых систем защиты от ботов. Они отдают чистый HTML, который идеально сжимается с помощью `shadow-web`.

### Ссылки для поиска:
* **DuckDuckGo HTML**: `https://html.duckduckgo.com/html/?q=ЗАПРОС`
* **DuckDuckGo Lite**: `https://lite.duckduckgo.com/lite/`

### Пример использования в коде:

```python
# Переход сразу на страницу результатов DuckDuckGo HTML
query = "Ferrari site:ss.com"
url = f"https://html.duckduckgo.com/html/?q={query}"

await page.goto(url)
clean_html, xml_map = await shadow.refresh()

# Теперь агент может прочитать сжатый XML-список результатов без CAPTCHA и JS
```

---

## Вариант 2. Использование Search API (Самый надежный способ для production)

Вместо парсинга поисковой выдачи через браузер, агент может использовать специализированные API, которые возвращают структурированный JSON с результатами поиска. Это экономит огромное количество токенов, времени и работает со 100% стабильностью.

### Рекомендуемые сервисы:
1. **Tavily API** — создан специально для LLM-агентов (возвращает чистый текст и отфильтрованные ответы).
2. **Brave Search API** — бесплатный лимит и очень дешевые тарифы, отличная альтернатива Google.
3. **SerpAPI / Serper.dev** — проксируют настоящую выдачу Google/Google Shopping/Yandex без блокировок.

### Пример интеграции Tavily:

```python
from tavily import TavilyClient

tavily = TavilyClient(api_key="your_tavily_key")
response = tavily.search(query="Ferrari site:ss.com", search_depth="basic")

for result in response['results']:
    print(f"Title: {result['title']}\nURL: {result['url']}\nSnippet: {result['content']}\n")
```

---

## Вариант 3. Настройка базового маскирования (Stealth) в Playwright

Если вам критически важно искать именно через Google/Yandex в браузере, необходимо скрыть признаки автоматизации.

Добавьте следующие параметры конфигурации при инициализации контекста браузера (например, в файле `src/shadow_web/mcp/server.py`):

```python
async def _ensure_browser():
    from playwright.async_api import async_playwright

    if "playwright" not in _session:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        
        # Настройка реалистичного контекста
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            locale="ru-RU",
            timezone_id="Europe/Riga",
        )
        
        # Скрытие navigator.webdriver (базовая проверка большинства сайтов)
        await context.add_init_script("delete navigator.__proto__.webdriver;")
        
        page = await context.new_page()
        _session["playwright"] = pw
        _session["browser"] = browser
        _session["context"] = context
        _session["page"] = page
```

---

## Вариант 4. Использование пакета `playwright-stealth`

Для более продвинутого скрытия от систем детекции (таких как Cloudflare, Akamai, PerimeterX) можно использовать обертку `playwright-stealth`. Она подменяет фингерпринты плагинов, кодеков, видеокарты и параметры WebGL.

### Установка:
```bash
pip install playwright-stealth
```

### Подключение в коде:
```python
from playwright_stealth import stealth_async

context = await browser.new_context()
page = await context.new_page()

# Применяем полную маскировку к странице
await stealth_async(page)

# Теперь можно переходить на сайты с проверкой ботов
await page.goto("https://google.com")
```
