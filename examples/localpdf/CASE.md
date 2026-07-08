# LocalPDF × Shadow Web — кейс трафика и продаж

Живой прогон: `python scripts/localpdf_competitor_scan.py` (22 Jun 2026).

---

## Задача

**Трафик** → programmatic SEO + обновляемые comparison-страницы.  
**Продажи** → free → Pro, дифференциация «без upload» vs Smallpdf/iLovePDF.

Shadow Web здесь — **не парсер ради данных**, а **дешёвый weekly-аудит конкурентов** для контента и pricing-таблиц без ручного копипаста.

---

## Что показал скан (факты)

| Сайт | Элементов | page_class | Позиционирование (meta/H1) |
|------|-----------|------------|----------------------------|
| **localpdf.online** | 83 | Static | Private, zero uploads, offline |
| **smallpdf.com** | 151–169 | Static | 30+ tools, **AI PDF**, free trial |
| **ilovepdf.com** | 191 | Static | Completely free, all tools |
| **sejda.com** | 297 | Static | Free, no install |
| **pdf24.org** | 88 | Iframe-heavy | Free, no limits, no watermarks |

**У LocalPDF уже есть:** 5 compare-страниц (`vs-smallpdf`, `ilovepdf`, `sejda`, `pdf24`, `pdfescape`), pricing (Free + Pro Monthly/Yearly), use-cases (lawyers, HR).

**SEO-аудит (апр 2026):** 72/100 — www без редиректа, дыры в sitemap, тонкий контент на landing (<350 слов).

---

## Уникальное торговое предложение (не копировать конкурентов)

Конкуренты выигрывают **шириной** (AI chat, 30+ tools, free forever).  
LocalPDF выигрывает **углом доверия**:

1. **Файл не уходит на сервер** — контракты, HR, бухгалтерия  
2. **Offline / local-first** — полевые условия, NDA  
3. **Один workspace**, не зоопарк utility-страниц  

Контент и реклама — только вокруг этого. Не гнаться за «AI PDF Summarizer» в v1 маркетинга.

---

## Воронка продаж

```
Поиск: "edit pdf without upload" / "localpdf vs smallpdf"
    → Compare-страница (доверие + таблица)
    → CTA "Open LocalPDF — free"
    → App /pricing → Pro Monthly / Yearly
```

**Скан показал CTA на pricing:** `Use Free`, `Start Pro Monthly`, `Start Pro Yearly`.  
**Слабое место:** compare-страницы почти без pricing-ряда — пользователь не видит цену до клика.

---

## 3 автоматизации на Shadow Web

### 1. Weekly competitor digest (трафик)

```bash
python scripts/localpdf_competitor_scan.py --json reports/localpdf-$(date +%Y%m%d).json
```

Агент в Cursor читает JSON → diff с прошлой неделей → PR в `LocalPDF_V6`:
- новые фичи у Smallpdf (AI tools)
- смена meta/H1 у конкурентов
- обновление строк в `comparisonRows` на compare/*

**Токены:** ~8 страниц × `minimal` + `shadow_query` ≈ **5–15k tok/нед** vs ручной обзор **50k+**.

### 2. Programmatic SEO — 10 страниц за цикл

Ключи, где LocalPDF силён, а конкуренты слабы в narrative:

| URL (создать/усилить) | Intent | Угол |
|-----------------------|--------|------|
| `/pdf-tools-without-upload` | уже есть, **расширить до 900+ слов** | vs upload-first |
| `/offline-pdf-editor` | новый | offline + browser |
| `/private-pdf-editor` | уже есть, thin | sensitive docs |
| `/compare/localpdf-vs-smallpdf` | + pricing block | privacy + price |
| `/features/ocr-pdf` | + «local OCR» | vs cloud OCR |
| `/use-cases/lawyers` | + FAQ schema | compliance |

Shadow Web: `navigate(detail=minimal)` → `shadow_query` по фичам конкурента → черновик секции «How X differs».

### 3. Alert на изменения конкурента (продажи)

Если в скане появилось новое `label` с `AI|Chat|Summar` у Smallpdf — Telegram/почта → ты пишешь пост «почему local-first лучше для NDA» в тот же день. **Окно контент-маркетинга 48 ч.**

---

## Контент-таблица для compare-страниц (из скана)

Добавить в каждую compare-страницу Astro:

| Критерий | LocalPDF | Smallpdf | iLovePDF | Sejda | PDF24 |
|----------|----------|----------|----------|-------|-------|
| Upload required | Нет (local-first) | Да | Да | Да | Да |
| AI / Chat PDF | Нет (privacy) | **Да** | Частично | Нет | Нет |
| Tool count (snapshot) | 83 | 151+ | 191 | 297 | 88 |
| Free tier | Да | Trial | Да | Да | Да |
| Pro on-site | Monthly/Yearly | Team plans | Premium | Pro | — |

Данные обновлять скриптом раз в месяц (числа `action_count` — proxy для «ширины продукта»).

---

## Быстрые SEO-фиксы (без Shadow Web, но блокируют трафик)

Из `localpdf-seo-audit.md` — сделать до масштабирования контента:

1. 301 `www` → non-www  
2. Canonical `/pricing` → `/pricing` (не `.html`)  
3. Добавить в sitemap: `/pricing`, `/private-pdf-editor`, …  
4. Блог: 2025 → 2026 + `dateModified`  

---

## KPI кейса (30 дней)

| Метрика | Сейчас (оценка) | Цель |
|---------|-----------------|------|
| Compare-страницы в GSC impressions | нет данных | +500/мес |
| Органика на «without upload» кластер | низкая | 3 страницы в top-50 |
| Pro conversion с `/pricing` | нет данных | baseline + A/B |
| Время обновления compare-таблиц | вручную | **<15 мин/нед** (скрипт) |

---

## Почему это материально

- **Трафик:** comparison + long-tail «private/offline/no upload» — коммерческий intent, CPC в PDF-нише высокий.  
- **Продажи:** compare доводит до app; pricing page уже готова — не хватает **доверия и цены на сравнении**.  
- **Стоимость:** Shadow Web снимает 80–95% токенов vs «скормить агенту весь Smallpdf» — weekly цикл остаётся в копейках.

---

## Следующий шаг

1. Запустить `localpdf_competitor_scan.py` по cron / Cursor Automation раз в неделю.  
2. Один PR в LocalPDF: pricing row на `localpdf-vs-smallpdf.astro` + расширить `/pdf-tools-without-upload`.  
3. Закрыть P0 из SEO-аудита.

Скажи «делай PR» — сделаю compare + thin page в `LocalPDF_V6`.
