---
name: shadow-web
description: >-
  Browser automation for Cursor via Shadow Web MCP: detail levels (minimal/terse/xml/full),
  page classifier (SPA, Anti-bot, Shadow DOM), shadow_grep filtering, navigate/click/fill
  by data-sid, diff snapshots, self-healing. Use when browsing the web, scraping,
  interacting with sites, compressing HTML, or when the user mentions shadow-web,
  shadow_grep, Action Map, data-sid, or page_class.
---

# Shadow Web MCP

Shadow Web — локальный MCP для веб-автоматизации. Playwright смотрит всю страницу; **LLM видит только то, что запрошено** через `detail` и `shadow_query`.

**Не используй** `cursor-ide-browser`, если доступен `shadow-web`.

---

## Жёсткие правила (токены)

1. **Никогда** `detail="full"` без явной отладки по просьбе пользователя.
2. **Никогда** не читай/не пересказывай целиком `groups`, `xml_map`, `action_map` из ответа — они огромные.
3. После `navigate` / `snapshot` смотри только: `url`, `title`, `action_count`, `page_class`, `groups_summary` — затем **`shadow_query`**.
4. Лимит: **не более 1× `detail="xml"` или `full` на 5 tool calls**.
5. После клика — **`snapshot(diff=true, detail="terse")`**, не полный переснимок.
6. **Таблицы/формы** — `schema_session()` (не `detail=full`, не `get_page_html` без нужды). Default `max_rows=50`; полный HTML только с `max_rows=0` / `max_chars=0`.

---

## Уровни детализации (`navigate`, `snapshot`)

| `detail` | Когда | Что в ответе |
|----------|-------|--------------|
| **`minimal`** | Разведка URL, проверка доступности | `url`, `title`, `action_count`, `groups_summary`, `page_class` |
| **`terse`** (default) | 90% сценариев | top-15 `action_map` + `groups_summary`; при `diff=true` — `appeared/changed/disappeared` |
| **`xml`** | Нужна структура DOM без fat JSON | `xml_map` + метаданные |
| **`full`** | Только отладка | `groups` + `xml_map` + `clean_html_preview` (≤500 символов) |

```text
navigate(url, detail="minimal")           # разведка
shadow_query("intent:login", format="terse")
click("3")
snapshot(diff=true, detail="terse")
```

`terse` в `navigate`/`snapshot` — **первые 15 элементов**, не «самые важные». Если нужного sid нет → `shadow_query`, не `detail="full"`.

---

## Класс страницы (`page_class`)

Каждый ответ `navigate`/`snapshot` содержит `page_class` и `page_class_reason`. Действуй по классу:

| Класс | Что делать |
|-------|------------|
| **Static** | Стандартный workflow |
| **SPA** | Readiness: consent dismiss + wait + scroll-until-content при бедном first paint. Если `action_count=0` → `snapshot(detail="terse")` ещё раз или сообщи пользователю |
| **SparseShell** | **Стоп.** Cookie/anti-bot шелл без контента после readiness (скролл не спас). Не индексируй и не долби `navigate` — сообщи пользователю |
| **Anti-bot** | **Стоп.** Сообщи: captcha/Cloudflare, headless не прошёл. Не долбить повторными `navigate` |
| **Shadow DOM** | Ок, flatten включён. При пропусках: `navigate(..., capture_mode="dual")` |
| **Closed Shadow** | `capture_mode="a11y"` или `dual` |
| **Iframe-heavy** | Предупреди: cross-origin iframe недоступен |
| **Auth-gated** | `shadow_query("intent:login")` → fill/click или попроси пользователя залогиниться |
| **WebMCP** | `webmcp_list_tools` → `webmcp_execute_tool`, DOM не нужен |

---

## Workflow по сценарию

| Сценарий | Шаги |
|----------|------|
| **Логин / форма** | `minimal` → `form_fill_plan(profile)` → `form_fill_execute(plan, answers)` |
| **Найти кнопку** | `shadow_query("label~/submit\|send\|ok/i")` или `type:button` + `type:a` |
| **Меню / навигация** | `shadow_query("group:Navigation")` |
| **Таблицы, odds, цены** | `schema_session()` или `shadow_query` по `label~` / `group:` |
| **Структура формы** | `schema_session()` → `forms[].fields` перед `fill` |
| **Статический HTML** | `compress_html` / `shadow_grep_html` / `schema_page(html)` — без браузера |
| **Мультишаг** | один baseline `navigate` → действия → только `snapshot(diff=true)` |
| **Поиск в сети** | `web_search(query)` → `shadow_query` по результатам → `click(sid)` |
| **Длинный текст / каталог / лента** | `content_outline` → `content_blocks(ids)`; catalog → `cards=`, feed → `feeds=` (без цен) |

---

## Инструменты

| Задача | Инструмент |
|--------|------------|
| Открыть страницу | `navigate(url, detail="minimal"\|"terse")` |
| Фильтр элементов | `shadow_query(query, format="terse")` |
| Клик / ввод | `click(sid)`, `fill(sid, value)` |
| Дельта после действия | `snapshot(diff=true, detail="terse")` |
| HTML без браузера | `compress_html`, `shadow_grep_html`, `schema_page(html)` |
| Таблицы / формы / списки | `schema_session()` после `navigate` (default max_rows=50) |
| Таблица → CSV / JSON | `schema_session_csv()` / `schema_session_json()` |
| Только таблицы | `schema_table(html)` или `schema_csv(html)` |
| Сырой HTML сессии | `get_page_html(max_chars=50000)` — осторожно с токенами |
| Web search | `web_search` |
| Form fill plan | `form_fill_plan(profile, url?)` |
| Form fill execute | `form_fill_execute(plan, answers)` |
| Chrome 145+ WebMCP | `webmcp_list_tools`, `webmcp_execute_tool` |

### `capture_mode` (при Shadow DOM / a11y)

`auto` (default) | `dom` | `a11y` | `dual` — передай в `navigate(url, capture_mode="dual")` при `Closed Shadow` или пустом Action Map на видимой странице.

---

## shadow_grep — синтаксис

```text
type:button              # тег; многие «кнопки» — type:a
intent:login             # login, cart, search, …
group:Login Form         # семантическая группа
label~/checkout/i        # regex по label
type:a intent:cart       # AND
id:1,3,5                 # конкретные sid
```

Форматы `shadow_query`: **`terse`** (default), `json`, `xml`.

---

## Self-healing

`click`/`fill` → binding path → local heal (кэш `~/.shadow-web/heal_cache.json`) → LLM heal (`SHADOW_WEB_HEAL_URL` + `/v1/heal`).

```bash
python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Нужны `DEEPSEEK_API_KEY` или `OPENAI_API_KEY` в `.env`.

---

## Ограничения

- Первый `navigate`: cold start Chromium ~3–8 с.
- Одна браузерная сессия — новый `navigate` заменяет контекст.
- `web_search` — Brave Search (+ Yahoo fallback), без API-ключей; headless с stealth UA.
- Anti-bot БК (Fonbet и др.) в headless часто дают `action_count=0` + `page_class: Anti-bot`.

## Отладка

Output panel → **MCP Logs**. Локально: `venv/bin/shadow-web-mcp`.
