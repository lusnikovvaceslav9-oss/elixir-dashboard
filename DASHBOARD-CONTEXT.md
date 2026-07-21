# Контекст наработок elixir-dashboard

Документ для интеграции в **твою** структуру: что сделано, зачем, где в коде, какие правила не ломать.

Репо: `lusnikovvaceslav9-oss/elixir-dashboard`  
Главный UI: `elixir.html` (монолит: HTML + CSS + JS)  
Живой URL: GitHub Pages → `elixir.html`

**Не трогать:** чужие папки/темы вне этого репо (например `Desktop/тема`).  
**Не коммитить:** `secrets.env`, логи скрейперов, credentials.

---

## 1. Карта проектов в дашборде

| Проект | Тип данных | Основной ввод | Ключевые особенности |
|--------|-----------|---------------|----------------------|
| **Planto** | auto-feed | `data/planto-*.csv/json` + buyer-feed | Триалы AM, bills RuStore (−7д yearly) |
| **JGGL** | multi-sheet CSV + Sheets | Upload CSV → iOS/Android + Waitlist/Redirect | Сплит по `android` в названии кампании |
| **Qlosophy** | multi-sheet CSV | Upload → «Лист 1» / Web | `Лист1` ≡ `Main` |
| **Quadcode** | monthly sheets + CSV | Upload → месяц по датам CSV | Не склеивать листы месяцев |
| Прочие | Google Sheets | sheetSources / monthlySheets | Как раньше |

Определение типа проекта (по `id` / `name`):

- `isJgglProject`, `isQlosophyProject`, `isQuadcodeProject`, `isPlantoAutoFeed` в `elixir.html`

---

## 2. Архитектура данных UI

```
projects[]  →  cache[projId] = { sources: [ { id, label, url, rows[], ... } ] }
                ↑
        fetchProject(proj)
          ├─ Google Sheets / monthlySheets
          ├─ local data/<dir>/*.csv (manifest)
          └─ CSV uploads (localStorage + JSONBin `_csv_uploads`)
```

**Правила листов**

- Всегда смотреть **один** выбранный `source` (`sourceIndexByProject`), не flat-merge всех листов — иначе JGGL/Qlosophy **удваивают** spend/installs.
- Месяц (`currentMonth`) режет строки внутри листа; для месячно-разбитых источников (Quadcode) месяц ↔ лист синхронизируются через `syncSourceIndexToMonth`.
- **JGGL исключение:** листы = iOS / Android / Waitlist / Redirect / Web → `syncSourceIndexToMonth` **не** перебивает клик пользователя.

**Overview (главная)**

- Плитки: метрики **последнего дня** (как у Planto), не сумма месяца.
- JGGL overview: суммировать только **iOS + Android**, не Waitlist/Redirect.

---

## 3. CSV upload (общее)

### Поведение

1. Файлы `.csv` / `.xlsx` (через SheetJS `XLSX`).
2. Хранение: `localStorage` + облако **JSONBin** ключ `_csv_uploads` → видно всем, не только локально.
3. Идентификация файла: **content hash** (SHA-256). Тот же файл → update, не дубль. Разные файлы → накопление, merge **по дате**.
4. `mergeRowsByDate` / `aggregateRowsByDate`: несколько кампаний в один день → сумма метрик (после сплита платформы).

### Ключевые функции

| Функция | Роль |
|---------|------|
| `processCsvUploadFile` | entry: parse → save → refresh cache |
| `saveUploadedCsv` | hash + JSONBin |
| `getUploadedCsvSources` | recover from storage |
| `expandUploadedSourcesByMonth` | Quadcode/Qlosophy → месячные листы; **JGGL → platform** |
| `expandJgglUploadedByPlatform` | iOS/Android split |
| `mergeUploadedSources` | влить upload в sheetSources |
| `pruneJgglAppSource` | удалить устаревший лист **App** |
| `ensureJgglPlatformSources` | если есть iOS — всегда есть и Android (и наоборот) |

### Пресеты upload

- **JGGL:** `Авто iOS / Android`, iOS, Android, Waitlist, Redirect, Web  
- **Qlosophy:** Лист 1, Web  
- **Quadcode:** Авто по датам CSV + существующие месяцы  

---

## 4. JGGL — iOS / Android

### Правило сплита

В названии **Campaign / Ad set / Ad** (без учёта регистра):

- есть подстрока `android` → лист **Android**
- иначе → лист **iOS**

### UI листов

Порядок: **iOS → Android → Waitlist → Redirect → Web**  
Лист **App** — удалён навсегда (`pruneJgglAppSource`).

### Баг, который нельзя вернуть

При `renderProjectDetail` раньше вызывался `syncSourceIndexToMonth` и для JGGL → клик по листу сразу сбрасывался на лист с max дней.  
Сейчас: для JGGL без month-partition sync **не** вызывается.

### Ре-аплоад

Старые месячные агрегаты без `Campaign name` после сплита уходят в iOS. Нужен CSV Meta Ads с колонкой названия.

Документация: `data/jggl/README.md`

---

## 5. Qlosophy

- Upload CSV как у JGGL.
- Канонизация: `Лист1` / `Main` / `Sheet1` → один ключ `main`, label **«Лист 1»** (`sourceLabelKey` / `canonicalSourceLabel` / `collapseEquivalentSources`).
- Месячная раскладка через `expandUploadedSourcesByMonth`.
- Local scaffold: `data/qlosophy/` + manifest.

---

## 6. Quadcode

- CSV/XLSX upload → лист месяца по **доминирующей дате** в файле (`__auto_month__`).
- Не склеивать все месячные листы в один flat dataset.
- Month switcher синхронизирует выбранный source (`syncSourceIndexToMonth` OK здесь).

---

## 7. Бюджет (spend)

Проблемы, которые чинили:

- Суммирование кампаний + дневной итог → **удвоение**.
- Суммирование нескольких листов (App+Waitlist) → **удвоение**.

Правила:

- Spend для бюджета/аккаунта — с **одного primary** листа (или iOS+Android для JGGL overview), не со всех sheetSources.
- В campaign-parse: если в дне есть именованные кампании и строка-итог (`Campaign=—`) — брать **только кампании**.
- Snapshot бюджета: не затирать вниз артефактом парсинга без явной причины (reconcile при падении spend).

---

## 8. Planto buyer-feed

Код: `scripts/buyer-feed/`  
Конфиг: `config/planto.json` / `scripts/buyer-feed/config/planto.json`  
Выход: `data/planto-daily.csv`, `data/planto-cohort.json`, `data/planto-meta.json`, `data/planto-product.json`  
Секреты: `APPMETRICA_*`, `DIRECT_*`, `SUPABASE_DB_URL` (см. `SETUP.md`).

### Источники (актуально)

| Метрика | Источник | Атрибуция в daily |
|---------|----------|-------------------|
| Spend / clicks / impressions | Yandex Direct API | день расхода |
| Installs | AppMetrica Reporting (`ym:i:installDevices`) | день |
| **Trials** | AppMetrica `trial_started` (**уники** `ym:ce:users`) | день события |
| Trials crosscheck | Supabase / RuStore entitlements | только meta |
| **Bills (`fb`)** | Supabase `MAIN` + `ACTIVE` | **cohort day** |
| **Sold** | только yearly из bills | **cohort day** |
| Paid net / когорты | same bills | yearly: pay−7д; monthly: день оплаты |

### Триалы: почему раньше 8 vs 6

- AppMetrica = клиентские события (часто выше).
- Supabase = реальные RuStore подписки.
- **В дашборде trials = AppMetrica**, чтобы не было расхождения с UI AM / Директ.
- RuStore остаётся в `trials_sb_crosscheck_*` и для оплат.

App ID RuStore / AM: **6305902** (совпадает с Директ Про → Мобильные приложения).

### Bills: «правильно с задержкой 7 дней»

Yearly trial 7 дней → оплата 08.07 относится к **дню старта триала 01.07** (`pay_date − 7`).  
Monthly — без триала → на **день оплаты**.

Функции: `bills_by_cohort_day`, `sold_by_cohort_day`, `paid_net_by_cohort_day` в `supabase.py` / `payments.py`.  
Feed source flag: `supabase_main_active_cohort_day`.  
Meta: `bill_attribution: cohort_day_yearly_minus_7`.

Цены: yearly **2490 ₽**, monthly **399 ₽**.  
`MAIN`+`CLOSED` (возврат) не считаются.

### Когорты

- Якорь: **2026-06-05**, корзины по 7 дней.
- `trial_starts=None` в `analyze_cohort_from_daily` → trials берутся из daily (AM).
- UI когорты предпочитает `trials_am` над `trials_sb`.

### Прогон

```bash
pip install -r scripts/buyer-feed/requirements.txt
python scripts/buyer-feed/__main__.py --work-dir .
# или: PYTHONPATH=scripts/buyer-feed python -m buyer-feed  (зависит от layout)
```

CI: `.github/workflows/planto-feed.yml` (hourly).  
Dashboard читает feed с `raw.githubusercontent.com` (не локальный кэш при Pages).

`planto-transfer/` — пакет для переноса фида; может расходиться с основным `scripts/buyer-feed/` — при интеграции брать **основной** `scripts/buyer-feed/` + `data/planto-*`.

---

## 9. Что удалено / не возвращать

| Удалено | Почему |
|---------|--------|
| FB Ads scraper (`fb-ads-scraper/`) | Только ручной/CSV upload |
| JGGL лист **App** | CSV идёт в iOS/Android (+месяц фильтром) |
| Trials daily из Supabase как primary | Расхождение с AppMetrica |

---

## 10. Чеклист интеграции в свою структуру

1. **UI-shell**  
   - Перенеси логику из `elixir.html` блоками: upload / project detectors / sheet switcher / Planto feed URLs.  
   - Если у тебя split `elixir.js` + `elixir.css` — сохрани те же имена функций и инварианты выше.

2. **Хранилище uploads**  
   - Нужен общий store (JSONBin / свой backend) с hash-ключами, иначе «у меня есть, у коллеги нет».

3. **Planto data**  
   - Скопируй `scripts/buyer-feed/`, `data/planto-*`, secrets schema.  
   - Проверь `trial_attribution` и `bill_attribution` в meta после первого прогона.

4. **JGGL**  
   - Обязательно: platform split + prune App + **не** sync source↔month.  
   - Документируй naming convention кампаний (`…android…`).

5. **Не ломать**  
   - Один активный source при отрисовке таблиц/графиков.  
   - Campaign totals ≠ sum(campaign rows) + total row.  
   - Planto feed: AM trials + cohort-day bills.

6. **Деплой**  
   - После смены `data/` или `elixir.html` — push в `main` (Pages).  
   - Пользователям: hard refresh Cmd+Shift+R (иначе видят старый raw CSV / meta).

7. **Секреты**  
   - Только Actions secrets / локальный `secrets.env` (gitignored).

---

## 11. Быстрые маркеры в коде (поиск)

```
isJgglProject / isQlosophyProject / isQuadcodeProject
expandJgglUploadedByPlatform / jgglPlatformFromName / pruneJgglAppSource
ensureJgglPlatformSources / syncSourceIndexToMonth
saveUploadedCsv / hashUploadText / jbSaveCsvUploads
PLANTO_DATA_URL / resolvePlantoFeedUrl
bills_by_cohort_day / sold_by_cohort_day / appmetrica_trial_started
```

---

## 12. Краткая хронология решений (для «почему так»)

1. Uploads по hash + JSONBin → публичная видимость статы.  
2. JGGL/Qlosophy/Quadcode: раздельные листы, без удвоений.  
3. Overview: last-day metrics для всех.  
4. Удалён FB scraper.  
5. Planto: trials = AppMetrica (совпадение с AM); bills = RuStore на день триала (−7 yearly).  
6. JGGL: iOS/Android по имени кампании; App убран; фикс клика по листам.

---

*Сгенерировано как рабочий бриф для переноса. При конфликте с README-buyer / SETUP — приоритет у актуального кода `elixir.html` + `scripts/buyer-feed/feed.py`.*
