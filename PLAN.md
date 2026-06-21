# Shadow Web — План развития

> Дата: 2025-06-21 (rev. 3 — + оговорки §10)  
> Статус проекта: MVP (~654 строк, 1 коммит, 4/4 unit-тестов)  
> Цель: adoption через open-source, monetization — после traction

---

## Changelog плана (rev. 2)

| Было | Стало | Почему |
|------|-------|--------|
| Phase 0 = OCI deploy + auth | Phase 0 = Local-First MVP | Инфра до фидбека — преждевременная оптимизация |
| Page diff по умолчанию | Page diff опциональный + breadcrumbs | LLM теряет контекст на «голой» дельте |
| Heal только через API | Local heal → API fallback | Экономия на LLM; работает offline |
| Cloud-first monetization | Open-source → composite trigger → cloud | §10.6 — stars alone недостаточно |

**rev. 3:** добавлен §10 «Оговорки и known limits» (Shadow DOM, iframe, heal, browser-use, benchmarks).

---

## 1. Текущее состояние

### Что работает

| Компонент | Файл | Статус |
|-----------|------|--------|
| DOM strip + Action Map + `data-sid` | `src/shadow_web/compressor.py` | ✅ 4 unit-теста |
| Playwright wrapper (click/fill + heal) | `src/shadow_web/wrapper.py` | ✅ |
| Local FastAPI `/v1/compress`, `/v1/heal` | `server/main.py` | ✅ `localhost` + `.env` |
| Упаковка pip | `pyproject.toml` v0.1.0 | ✅ не опубликован |

### Чего нет (и это нормально для Phase 0)

- MCP-сервер (локальный)
- PyPI publish
- Shadow DOM / iframe flattening
- Local-first heal (fuzzy matching)
- Semantic grouping Action Map
- browser-use интеграция
- Бенчмарки в README (цифры 33× / 90% — заявлены, не измерены)

### Отложено до traction (100+ GitHub stars / активные issues)

- Деплой API на OCI
- API keys, rate limit, billing
- Landing + pricing page

### Конкуренты (кратко)

| Проект | Сильная сторона | Слабость для нас |
|--------|-----------------|------------------|
| [llm-as-dom / lad](https://github.com/menot-you/llm-as-dom) | SemanticView, MCP, `lad_jq` | TS-first, не Python |
| [Retio PageMap](https://github.com/retio-ai/retio-pagemap) | 97% token reduction, MCP | Shadow DOM не заявлен |
| [agent-browser](https://github.com/malovnik/agent-browser) | a11y, page diff, intents | TS, не browser-use |
| [browser-use](https://github.com/browser-use/browser-use) | Огромная Python-аудитория | Screenshot-heavy, много токенов |
| [Stagehand](https://github.com/browserbase/stagehand) | Self-healing, cloud | TS, Browserbase lock-in |

**Вывод:** Action Map — commodity. Moat = **Shadow DOM flatten + browser-use plugin + local heal**.

---

## 2. Позиционирование

### Было

> «Сжимаем HTML для AI-агентов»

### Станет (Local-First)

> **Shadow Web — Python SDK для AI-агентов:**  
> видит Shadow DOM и iframe → группирует Action Map → чинит селекторы локально →  
> интегрируется с browser-use из коробки.

### Уникальный угол (moat)

1. **Shadow DOM + iframe flatten** — Salesforce, Stripe Dashboard, SaaS-панели. lxml их не видит; Playwright `page.evaluate` — видит.
2. **Local-first heal** — fuzzy match по label / `aria-label` / `name` до вызова LLM (confidence > 85% → бесплатно).
3. **Semantic grouping** — Action Map блоками («Login Form», «Checkout»), не плоский список.
4. **browser-use plugin** — drop-in замена тяжёлого page context.
5. *(Phase 2)* WebMCP bridge — когда Chrome adoption вырастет.
6. *(Phase 2)* shadow_grep — query-слой поверх Action Map.

---

## 3. Архитектура (целевая)

```
┌──────────────────────────────────────────────────────────────┐
│              ShadowPage / browser-use adapter                 │
├──────────────────────────────────────────────────────────────┤
│  1. DOM capture (Playwright in-browser)                      │
│     • flatten Shadow DOM (recursive open shadow roots)       │
│     • flatten same-origin iframes                            │
│     ↓                                                        │
│  2. lxml strip + data-sid injection                          │
│     ↓                                                        │
│  3. Semantic grouping (forms, nav, modals)                   │
│     ↓                                                        │
│  4. shadow_grep query [optional filter before LLM]           │
│     ↓                                                        │
│  5. XML Action Map → LLM / browser-use context               │
│     ↓                                                        │
│  6. Playwright execute (click/fill by sid)                   │
│     ↓ (on failure)                                           │
│  7. Local heal (fuzzy, confidence score)                     │
│     ↓ (if confidence < 85%)                                  │
│  8. LLM heal via local FastAPI or DEEPSEEK_API_KEY in .env   │
├──────────────────────────────────────────────────────────────┤
│  [Phase 2] WebMCP detect → executeTool() if available        │
│  [Phase 2] Page diff — opt-in, always with breadcrumbs       │
└──────────────────────────────────────────────────────────────┘
```

### Local-First setup (Phase 0)

```bash
pip install shadow-web
cp .env.example .env   # DEEPSEEK_API_KEY или OPENAI_API_KEY
# MCP в Cursor — без облака:
shadow-web-mcp         # или python -m shadow_web.mcp
# Опционально локальный heal API:
uvicorn server.main:app --port 8000
```

---

## 4. Roadmap

### Phase 0 — Local-First MVP (5–7 дней)

**Цель:** `pip install shadow-web` + MCP в Cursor + GitHub с бенчмарками.  
**Не цель:** облако, billing, auth.

| # | Задача | Файлы | Effort | Приоритет |
|---|--------|-------|--------|-----------|
| 0.1 | **Локальный MCP-сервер** (`snapshot`, `click`, `compress`, `query`) | `src/shadow_web/mcp/` | 1–2 д | P0 |
| 0.2 | **Publish PyPI** (`shadow-web`) | `pyproject.toml`, entry points | 0.5 д | P0 |
| 0.3 | **Shadow DOM + iframe flatten** | `src/shadow_web/dom_capture.py` | 2–3 д | P0 |
| 0.4 | **Semantic grouping** Action Map | `src/shadow_web/compressor.py` | 1 д | P0 |
| 0.5 | **Local-first heal** (fuzzy, без LLM) | `src/shadow_web/heal_local.py` | 1–2 д | P0 |
| 0.6 | **Бенчмарки** + README (≥3 сайта, incl. shadow DOM demo) | `benchmarks/run.py` | 0.5 д | P0 |
| 0.7 | **browser-use example** (demo + thin adapter, pin version) | `examples/browser_use/` | 1–2 д | P0 |
| 0.8 | GitHub + пост Reddit r/LocalLLaMA | — | 0.5 д | P0 |

**Критерий готовности Phase 0:**
- [ ] `pip install shadow-web && shadow-web-mcp` работает в Cursor
- [ ] Shadow DOM test page: элементы видны в Action Map
- [ ] Local heal чинит сломанный class без LLM
- [ ] README: таблица бенчмарков с реальными цифрами
- [ ] `examples/browser_use/` — runnable demo

---

#### 0.3 Shadow DOM + iframe — детали (критично)

```javascript
// dom_capture.py — inject via page.evaluate
function flattenDOM(root = document.body) {
  // 1. Recurse into open shadow roots
  // 2. Clone flattened tree as HTML string
  // 3. Same-origin iframes: document.querySelector('iframe') → contentDocument
  // 4. Cross-origin iframes: mark as [iframe: url] — не flatten, но в map
}
```

- `data-sid` проставляется **после** flatten, на плоском дереве
- Unit-тест: HTML fixture с `<template shadowroot>` или Playwright на webcomponent.dev
- **Benchmark story:** «PageMap видит 12 кнопок, Shadow Web — 47» на Stripe-like fixture

---

#### 0.4 Semantic grouping — формат

```json
{
  "url": "https://app.example.com/login",
  "groups": [
    {
      "name": "Login Form",
      "elements": [
        {"id": "1", "type": "input[email]", "label": "Email"},
        {"id": "2", "type": "input[password]", "label": "Password"},
        {"id": "3", "type": "button", "label": "Sign in"}
      ]
    },
    {
      "name": "Navigation",
      "elements": [{"id": "4", "type": "a", "label": "Forgot password?"}]
    }
  ]
}
```

Heuristics (без LLM):
- `<form>` → группа по `aria-label` / legend / id
- `role="dialog"` → modal group
- `<nav>`, `<header>`, `<footer>` → named sections

---

#### 0.5 Local-first heal — алгоритм

```
1. Получить action metadata (label, type) по sid
2. Кандидаты: querySelectorAll(tag) на live page
3. Score каждого:
   - exact text match label        → +0.4
   - aria-label / placeholder      → +0.3
   - name attribute                → +0.2
   - stable id / data-testid       → +0.3
   - fuzzy ratio (label, text) > 0.9 → +0.2
4. if top_score >= 0.85 → return selector, source: "local"
5. else → POST /v1/heal (local FastAPI + .env key), source: "llm"
```

Кэш: `(domain, label, type) → selector` в `~/.shadow-web/heal_cache.json`

---

#### 0.7 browser-use интеграция

```python
# examples/browser_use/shadow_context.py
from browser_use import Agent
from shadow_web.browser_use import ShadowBrowserContext

# ShadowBrowserContext подменяет page state:
# вместо screenshot/DOM dump → grouped Action Map XML
agent = Agent(
    task="Login to the app",
    browser_context=ShadowBrowserContext(...),
)
```

- Hook в `browser_use.browser.context` или custom `Controller` — **thin layer**, см. §10.5
- README: «Example: fewer tokens with browser-use» (не «из коробки» до external tester)
- PR/issue в browser-use repo после стабильного demo

---

### Phase 1 — Дифференциация (после первых пользователей)

**Триггер:** 10+ stars, 2+ external issues, или 1 интеграция от стороннего dev.

| # | Задача | Effort |
|---|--------|--------|
| 1.1 | **shadow_grep** query-слой + MCP tool | 1–2 д |
| 1.2 | **WebMCP bridge** (Chrome 145+ preview) | 2–3 д |
| 1.3 | **Intent presets** (`login`, `checkout`, …) | 1 д |
| 1.4 | **Page diff (opt-in)** — см. ниже | 1–2 д |

#### 1.4 Page Diff — только opt-in, с breadcrumbs

**Проблема:** LLM путается на «голой» дельте без контекста страницы.

**Решение:**

```python
page.refresh(diff=True)  # default: False
```

При `diff=True` возвращать:
- краткий **page skeleton** (url, title, group names — ~50 tokens)
- **breadcrumbs** для каждого changed element: `Nav > Login Form > button[3]`
- delta: `changed`, `appeared`, `disappeared`

Не заменять full snapshot по умолчанию — только экономить на stable pages (dashboards, SPA).

---

### Phase 2 — Cloud & monetization

**Триггер:** составной (§10.6): 100+ stars + external activity / 5+ MCP users / inbound / платящий клиент.

| # | Задача | Effort |
|---|--------|--------|
| 2.1 | OCI deploy (или Fly.io) | 1 д |
| 2.2 | API keys + rate limit | 0.5 д |
| 2.3 | Verified heal (Playwright check на сервере) | 2 д |
| 2.4 | a11y dual mode (CDP tree) | 3–4 д |
| 2.5 | Pricing (LemonSqueezy) | 1 д |

| Tier | Лимит | Цена |
|------|-------|------|
| Free (local) | unlimited, свой API key | $0 |
| Cloud Free | 100 req/day | $0 |
| Pro | 10K req/mo | $29/mo |

---

### Phase 3 — Рост (по traction)

- Landing page
- MCP Registry publish
- WebMCP declarative API
- LangChain adapter
- `shadow_grep` CLI для CI smoke tests

**Не делать без платящих:**
- Kubernetes, full observability
- Stealth / anti-detect
- Vision/screenshot mode

---

## 5. Модели монетизации (пересмотр)

| Модель | Когда | Upside |
|--------|-------|--------|
| **Open-source adoption** | Phase 0 — сейчас | Stars, issues, browser-use ecosystem |
| **Ручная интеграция** | После demo | $500–2K, быстрые деньги |
| **Hosted heal API** | Phase 2, 100+ stars | Recurring |
| **Enterprise (Shadow DOM SLA)** | Phase 3 | Salesforce/Stripe automation — высокий чек |

**Путь:** Local MVP → GitHub/Reddit → browser-use community → ручные интеграции → cloud.

---

## 6. Каналы дистрибуции

| Канал | Phase | Действие |
|-------|-------|----------|
| GitHub + README benchmarks | 0 | Shadow DOM demo GIF |
| PyPI | 0 | `pip install shadow-web` |
| Reddit r/LocalLLaMA | 0 | «browser-use with 90% fewer tokens» |
| browser-use Discord / PR | 0–1 | Integration example |
| MCP Registry | 1 | После стабилизации tools |
| Product Hunt | 2 | После cloud |

---

## 7. Метрики успеха

### Phase 0 (2–3 недели)

- [ ] PyPI publish, `pip install` работает
- [ ] MCP в Cursor — 3+ tools без облака
- [ ] Shadow DOM fixture: 100% interactive elements captured
- [ ] Local heal: ≥80% success на test suite без LLM
- [ ] README benchmarks: ≥3 сайта
- [ ] browser-use example runnable
- [ ] 10+ GitHub stars

### Phase 1 (1–2 месяца)

- [ ] 50+ stars OR 5+ external issues
- [ ] shadow_grep + WebMCP в main
- [ ] 1 external contributor

### Phase 2 / monetization

- [ ] Составной триггер cloud (§10.6): stars + issues / MCP users / inbound / платящий
- [ ] 1 платящий клиент
- [ ] Cloud endpoint live

---

## 8. Структура файлов (целевая)

```
shadow-web/
├── src/shadow_web/
│   ├── compressor.py       # strip + action map (existing)
│   ├── dom_capture.py      # Shadow DOM + iframe flatten [Phase 0]
│   ├── grouping.py         # semantic groups [Phase 0]
│   ├── heal_local.py       # fuzzy local heal [Phase 0]
│   ├── query.py            # shadow_grep [Phase 1]
│   ├── diff.py             # opt-in page diff + breadcrumbs [Phase 1]
│   ├── webmcp.py           # WebMCP bridge [Phase 1]
│   ├── browser_use.py      # browser-use adapter [Phase 0]
│   ├── wrapper.py          # ShadowPage orchestrator
│   └── mcp/
│       └── server.py       # local MCP [Phase 0]
├── server/
│   └── main.py             # local FastAPI (optional, .env keys)
├── examples/
│   └── browser_use/
│       └── demo.py
├── benchmarks/
│   └── run.py
├── tests/
│   ├── test_shadow_dom.py  # [Phase 0]
│   └── test_heal_local.py  # [Phase 0]
├── deploy/
│   └── oci.sh              # [Phase 2 — не трогать сейчас]
└── PLAN.md
```

---

## 9. Риски

| Риск | Митигация |
|------|-----------|
| Cross-origin iframe не flatten | Явно маркировать в Action Map; см. §10 |
| Closed Shadow DOM (mode: closed) | Fallback на a11y tree (Phase 2); см. §10 |
| browser-use API меняется | Pin version; example repo, не «из коробки»; см. §10 |
| Local heal false positives | Threshold 0.85 + verify selector before cache; см. §10 |
| WebMCP adoption медленный | Phase 1–2, не блокирует Phase 0 |

---

## 10. Оговорки и known limits

> Честные границы продукта. Не обещать в README/marketing то, что здесь помечено как limit.

### 10.1 Shadow DOM — не серебряная пуля

| Сценарий | Поведение | Phase |
|----------|-----------|-------|
| **Open shadow root** | Flatten через `shadowRoot` + рекурсия | 0 |
| **Closed shadow root** (`mode: closed`) | Из JS недоступен; элементы **не попадут** в Action Map | — |
| **Declarative shadow DOM** (SSR) | Flatten после hydration; до JS — пусто | 0 |
| **Web Components с slots** | Flatten + сохранять slot-assigned nodes | 0 |

**Closed shadow → fallback (Phase 2):** CDP `Accessibility.getFullAXTree` — screen reader видит closed shadow, lxml/JS flatten — нет.

**Риск naive flatten:** сериализация shadow tree в HTML-string и обратный parse через lxml может **потерять live bindings** (event listeners, custom element state). Правильный путь: flatten **in-browser** → отдать строку в compressor, **не** re-inject flattened HTML обратно в document для кликов. Клики — по live page через healed/live selectors, не по flattened copy.

**Marketing limit:** не писать «100% Shadow DOM coverage» — писать «open shadow roots + closed fallback roadmap».

---

### 10.2 Iframe

| Тип | Flatten | В Action Map |
|-----|---------|--------------|
| Same-origin iframe | ✅ `contentDocument` | Элементы с prefix `[iframe: name]` |
| Cross-origin iframe | ❌ browser security | Запись `{type: "iframe", src: "...", label: "..."}` — агент знает, что блок есть, но не видит inside |
| `sandbox` iframe без `allow-same-origin` | ❌ | Как cross-origin |

**Не обещать:** автоматизацию Stripe Dashboard / Salesforce **без** ручной настройки (часто cross-origin embeds, SSO, closed shadow).

---

### 10.3 Local heal — ограничения

- Порог **0.85** — стартовая эвристика, не константа; нужен `test_heal_local.py` на fixtures.
- **False positive:** две кнопки «Submit» на странице → fuzzy match может выбрать не ту. Митигация: scope по semantic group + verify `element.is_visible()` + optional `bounding_box` proximity к last known position.
- **False negative:** label изменился полностью («Sign in» → «Continue») → только LLM heal.
- Кэш `(domain, label, type)` **инвалидируется** при смене URL path или при failed click после heal.

**Не обещать:** «zero LLM heal» — обещать «majority of selector breaks resolved locally».

---

### 10.4 Page diff (Phase 1)

- **Default: off.** Diff без skeleton + breadcrumbs не включать.
- Плохие кейсы для diff: wizard/multi-step forms, infinite scroll, табы — структура «прыгает», агент теряет grounding.
- Хорошие кейсы: dashboard, settings page, SPA с stable layout.
- Diff **никогда не заменяет** url + title + list of group names в первом сообщении после action.

---

### 10.5 browser-use интеграция

- **Phase 0 deliverable:** `examples/browser_use/demo.py` + отдельный README — **не** глубокий merge в upstream browser-use.
- Архитектура browser-use заточена под agent loop со screenshots; подмена context может потребовать patch/hook глубже, чем 100 строк — **thin adapter**, pin `browser-use==X.Y.Z`.
- Не заявлять «pip install shadow-web и browser-use сам подхватит» до рабочего demo и 1 external tester.
- Путь в ecosystem: example repo → Discord → issue/PR в browser-use, если API стабилен.

---

### 10.6 Cloud / monetization — составной триггер

**100 GitHub stars alone — слабый сигнал** (накрутка, stars без users).

Phase 2 cloud только если **любое** из:

- 100+ stars **и** ≥3 external issues/PRs  
- ≥5 активных установок MCP (issues, Discord feedback)  
- 1 явный inbound: «нужен hosted API / SLA»  
- 1 платящий клиент на ручной интеграции с запросом cloud  

---

### 10.7 Token savings — только с бенчмарками

- Цифры 33× / 90% из README — **гипотеза** до `benchmarks/run.py`.
- В marketing использовать только **измеренные** значения per-site (HN, Wikipedia, shadow DOM fixture).
- Savings зависят от страницы: login form — скромнее; Amazon product page — агрессивнее.

---

### 10.8 WebMCP (Phase 1+)

- Chrome 145+ preview — не все пользователи имеют compatible browser.
- Tool execution требует **visible tab** (не pure headless) — документировать.
- `document.modelContext` (Chrome 150+: `navigator.modelContext` deprecated).
- Adoption может быть медленным год+ — Action Map остаётся primary path.

---

### 10.9 lxml vs live DOM

- `compressor.py` (lxml) работает на **строке HTML**, не на live tree.
- Shadow DOM / iframe capture **обязан** happen in Playwright **до** lxml — порядок: `page.evaluate(flatten)` → `process_html(string)`.
- Static HTML file / curl без browser — Shadow DOM features **недоступны** (expected).

---

### 10.10 Что не делаем (scope boundary)

- Cross-origin iframe piercing (browser security — невозможно легально)
- Closed shadow without a11y fallback (до Phase 2)
- CAPTCHA / bot bypass / stealth (другой продукт, legal risk)
- Guaranteed success rate на произвольных SaaS без customer-specific tuning

---

## 11. Следующий шаг

**Phase 0 порядок реализации:**

1. `dom_capture.py` — Shadow DOM flatten (максимальный diff vs конкурентов)
2. `grouping.py` + обновить `compressor.py`
3. `heal_local.py` + wiring в `wrapper.py`
4. Local MCP server
5. PyPI + benchmarks + browser-use example
6. GitHub / Reddit

Сказать **«делай»** → начать с `dom_capture.py` (Shadow DOM).
