# Planto — перенос auto feed в другой проект

**Это готовый пакет для переноса**, не только документация. В архиве:

```
planto-transfer/
├── scripts/buyer-feed/      ← Python-фид (весь код)
├── data/                    ← planto-daily.csv, meta, cohort (+ примеры)
├── .github/workflows/       ← planto-feed.yml для CI
├── secrets.env.example      ← шаблон секретов
├── run-feed.sh              ← локальный прогон одной командой
├── MANIFEST.txt             ← краткое содержание
└── README.md                ← этот файл
```

### Быстрый старт после распаковки

```bash
# 1. Скопируй содержимое planto-transfer/ в корень нового git-репо
cp secrets.env.example secrets.env   # заполни 5 ключей из Bitwarden
chmod +x run-feed.sh
./run-feed.sh                        # обновит data/*

# 2. GitHub: Settings → Secrets → 5 переменных (см. §3)
# 3. Push → Actions «Planto data feed» → Run workflow
# 4. Подключи Netlify к репо, положи elixir.html рядом с data/
```

Документ ниже — полное описание архитектуры, API и интеграции фронта.

---

## 1. Что делает система

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ Yandex Direct   │────▶│                  │     │ planto-daily.csv│
│ (spend)         │     │  scripts/        │────▶│ planto-cohort   │
├─────────────────┤     │  buyer-feed      │     │ .json           │
│ AppMetrica      │────▶│  (Python 3.12)   │────▶│ planto-meta.json│──▶ elixir.html
│ (installs)      │     │                  │     │                 │    (Auto feed UI)
├─────────────────┤     └──────────────────┘     └─────────────────┘
│ Supabase PG     │────▶        ▲
│ (trials, bills) │             │
└─────────────────┘     GitHub Actions cron (каждые 2 ч)
                        или локальный cron / ручной запуск
```

**Вход:** 5 API-ключей.  
**Выход:** 3–4 файла в `data/`, которые фронт читает same-origin (Netlify / любой static host).

| Метрика | Источник | Описание |
|---------|----------|----------|
| **Spend** | Yandex Direct Reports API | Расход без НДС, по дням |
| **Installs** | AppMetrica Reporting API | `ym:i:installDevices`, уникальные установки |
| **Trials** | Supabase Postgres | Старты триала RuStore (`rustore_subscription_entitlements`), distinct user_id по дню (MSK) |
| **Sold** | Supabase | Кол-во успешных списаний (годовые + месячные) |
| **Bills (fb)** | Supabase | То же, что sold — кол-во биллов в CSV-колонке `fb` |
| **Paid net** | Supabase | Сумма списаний (2490 ₽ год / 399 ₽ мес) для когорт P&L |
| **Когорты** | Расчёт из daily + bills | Недельные корзины от якорной даты |

**Fallback:** если Supabase недоступен — `data/rustore-payments.csv` (ручной/архивный CSV оплат).

---

## 2. Файлы для копирования

**Уже лежат в этом пакете** — распакуй `planto-transfer/` в корень целевого репозитория.

Дополнительно из основного репо Elixir:

```
elixir.html                  # фронт: Auto feed, когорты, applyPlantoAutoSource
```

Структура пакета (дублирует основной репо):

```
scripts/buyer-feed/          # весь Python-пакет (скопирован)
├── __main__.py              # точка входа
├── feed.py                  # оркестратор
├── config/planto.json       # конфиг проекта
├── direct.py                # Yandex Direct
├── appmetrica.py            # AppMetrica
├── supabase.py              # Postgres / RuStore
├── daily.py                 # CSV read/write
├── cohort.py                # когорты P&L
├── payments.py              # CSV fallback
├── secrets.py               # загрузка секретов
└── requirements.txt

.github/workflows/planto-feed.yml
data/planto-daily.csv        # пример / текущий срез
data/planto-meta.json
data/planto-cohort.json
data/rustore-payments.csv
secrets.env.example
run-feed.sh
```

**Не копировать / не коммитить:** `secrets.env`, `__pycache__/`, `_buyer-extract/`.

---

## 3. Секреты (5 переменных)

| Переменная | Назначение | Пример значения (не секрет) |
|------------|------------|----------------------------|
| `APPMETRICA_OAUTH_TOKEN` | OAuth-токен AppMetrica Reporting API | из кабинета AppMetrica |
| `APPMETRICA_APPLICATION_ID` | ID приложения | `6305902` |
| `DIRECT_OAUTH_TOKEN` | OAuth Yandex Direct | из Direct API |
| `DIRECT_CLIENT_LOGIN` | Логин клиента в Директе | `doxmediagroup` |
| `SUPABASE_DB_URL` | Postgres connection string | `postgresql://user:pass@…pooler.supabase.com:5432/postgres` |

**Приоритет загрузки** (`secrets.py`):

1. переменные окружения (GitHub Actions);
2. `secrets.env` в корне проекта;
3. `supabase/secrets.env` (если есть).

Шаблон — `secrets.env.example`:

```env
APPMETRICA_OAUTH_TOKEN=
APPMETRICA_APPLICATION_ID=6305902
DIRECT_OAUTH_TOKEN=
DIRECT_CLIENT_LOGIN=doxmediagroup
SUPABASE_DB_URL=
```

---

## 4. Конфиг проекта

`scripts/buyer-feed/config/planto.json`:

```json
{
  "id": "planto",
  "name": "Planto",
  "anchor": "2026-06-05",
  "refresh_days": 7,
  "attribution_lag_days": 2,
  "trial_lag_days": 7,
  "currency": "RUB",
  "direct_client_login": "doxmediagroup",
  "appmetrica_application_id": "6305902",
  "payments_csv": "data/rustore-payments.csv",
  "daily_csv": "data/planto-daily.csv",
  "cohort_json": "data/planto-cohort.json",
  "meta_json": "data/planto-meta.json",
  "state_json": "data/planto-feed-state.json"
}
```

| Поле | Смысл |
|------|--------|
| `anchor` | Первая дата UA-кампании; с неё строятся daily и когорты |
| `refresh_days` | Сколько последних дней перезапрашивать у API (остальное из CSV) |
| `attribution_lag_days` | Запас к refresh для late-attribution |
| `trial_lag_days` | Лаг годового триала (7 д) для P&L «Рано» |

При переносе на **другое приложение** поменяй: `anchor`, `appmetrica_application_id`, `direct_client_login`, пути `*_csv`/`*_json` при необходимости.

---

## 5. Форматы выходных данных

### 5.1 `data/planto-daily.csv`

Заголовки (строго):

```csv
date,spend,installs,trials,sold,fb
05.06.2026,318.09,2,0,1,1
```

| Колонка | Тип | Источник |
|---------|-----|----------|
| `date` | `DD.MM.YYYY` | — |
| `spend` | float, ₽ | Direct |
| `installs` | int | AppMetrica |
| `trials` | int | Supabase trial starts |
| `sold` | int | Supabase bills (шт.) |
| `fb` | int | = sold (legacy имя для дашборда) |

### 5.2 `data/planto-meta.json`

Служебный срез последнего прогона:

```json
{
  "generated_at": "2026-07-03T14:56:31+07:00",
  "anchor": "2026-06-05",
  "until": "2026-07-03",
  "window_start": "2026-06-24",
  "trial_attribution": "supabase_trial_start",
  "sources": {
    "spend": "direct_api",
    "installs": "appmetrica_reporting",
    "trials": "supabase_trial_start",
    "bills": "supabase_main_active"
  },
  "errors": [],
  "days": 29,
  "payments_by_plan": {
    "yearly": { "count": 11, "rub": 27390 },
    "monthly": { "count": 6, "rub": 2394 },
    "total": { "count": 17, "rub": 29784 }
  }
}
```

**Критерий успеха:** `"errors": []`.  
Фронт сравнивает `until` с последней датой в CSV (health check).

### 5.3 `data/planto-cohort.json`

```json
{
  "anchor": "2026-06-05",
  "until": "2026-07-03",
  "report_date": "2026-07-03",
  "trial_lag_days": 7,
  "rows": [
    {
      "cohort": "VI · W2 8–14",
      "cohort_id": "2026-06-W2",
      "month_key": "2026-06",
      "start": "2026-06-08",
      "end": "2026-06-14",
      "spend": 11901,
      "installs_am": 999,
      "trials_sb": 5,
      "trials_am": 5,
      "cpi": 12,
      "cpt": 2380,
      "sold": 4,
      "paid_net": 9960,
      "pnl": -1940.77,
      "pnl_display": "-1 941",
      "mature": true,
      "checkpoint": "2026-06-21",
      "when": "зрелая"
    }
  ]
}
```

Корзины: **недели внутри месяца** (W1 1–7, W2 8–14, …) от `anchor`.

### 5.4 `data/rustore-payments.csv` (fallback)

```csv
pay_date,amount_rub,plan,card_last4,status,note
2026-06-15,2490,yearly,8176,paid,
2026-06-16,2490,yearly,0377,refunded,Саша
```

Используется только если Supabase bills не поднялись.

---

## 6. Внешние API и БД

### 6.1 Yandex Direct

- **URL:** `POST https://api.direct.yandex.com/json/v5/reports`
- **Отчёт:** `CAMPAIGN_PERFORMANCE_REPORT`, поля `Date`, `Cost`
- **Headers:** `Authorization: Bearer {DIRECT_OAUTH_TOKEN}`, `Client-Login: {DIRECT_CLIENT_LOGIN}`
- **VAT:** `IncludeVAT: NO`

### 6.2 AppMetrica Reporting

- **Installs:** `GET https://api.appmetrica.yandex.com/stat/v1/data/bytime`  
  `metrics=ym:i:installDevices`, `date_dimension=day`
- **Crosscheck trials:** событие `trial_started` (только сверка в meta, не daily)
- **Auth:** `Authorization: OAuth {APPMETRICA_OAUTH_TOKEN}`

### 6.3 Supabase Postgres

**Таблица:** `rustore_subscription_entitlements`

**SQL (упрощённо):**

```sql
SELECT purchase_id, user_id::text, product_code, period, status,
       last_subscription_event_type, last_event_time, activated_at
FROM rustore_subscription_entitlements
WHERE user_id IS NOT NULL
  AND period IN ('TRIAL', 'MAIN', 'GRACE', 'CLOSED')
  AND coalesce(activated_at, last_event_time) IS NOT NULL;
```

**Логика триалов (MSK):**

- Daily: новые старты — `period=TRIAL`, события `ACTIVATED`, `CLIENT_SYNC`, `RECOVERED`, `CANCELLED`
- Cohort: + backdate годового `MAIN` на 7 дней назад
- Один user = один триал (earliest start)

**Логика биллов:**

- Успешное списание: `period='MAIN'` AND `status='ACTIVE'`
- `MAIN CLOSED` (возврат) **не** считается
- Цены: **год 2490 ₽**, **месяц 399 ₽** (`supabase.py`: `YEARLY_PRICE`, `MONTHLY_PRICE`)
- Годовой bill: cohort_day = pay_date − 7; месячный: cohort_day = pay_date

---

## 7. Интеграция фронта (`elixir.html`)

Константы (same-origin paths):

```javascript
const PLANTO_DATA_URL = 'data/planto-daily.csv';
const PLANTO_COHORT_URL = 'data/planto-cohort.json';
const PLANTO_META_URL = 'data/planto-meta.json';
```

Ключевые функции для переноса:

| Функция | Назначение |
|---------|------------|
| `applyPlantoAutoSource()` | При загрузке проекта `planto` подменяет Google Sheets на `data/planto-daily.csv` |
| `isPlantoAutoFeed()` | `dataSource === 'auto'` или URL содержит `planto-daily.csv` |
| `fetchPlantoMeta()` / `fetchPlantoCohort()` | GET JSON с cache-bust |
| `bustLocalFeedUrl()` | `?_=timestamp` для локальных файлов |
| `fetchWithFallback()` | Локальные `data/*` — только same-origin, без CORS-прокси |
| `renderPlantoBreakdownCard()` | Карточка «Auto feed · live» + health |
| `renderPlantoCohortSection()` | Таблица когорт по месяцу |
| `parsePlantoRows()` | Колонка `sold` в CSV |
| `getTableConfig()` | Полная таблица для auto feed (installs, trials, sold, fb) |

Проект в JSONBin / localStorage должен иметь:

```json
{
  "id": "planto",
  "type": "planto",
  "dataSource": "auto",
  "urls": ["data/planto-daily.csv"],
  "sheetSources": [{ "id": "auto", "url": "data/planto-daily.csv", "label": "Auto feed" }]
}
```

`applyPlantoAutoSource()` делает это автоматически при каждой загрузке.

**Важно:** открывать дашборд только через HTTP-сервер (Netlify, `python -m http.server`), не `file://`.

---

## 8. Запуск

### Локально

```bash
cp secrets.env.example secrets.env   # заполнить из Bitwarden / vault
pip install -r scripts/buyer-feed/requirements.txt
python scripts/buyer-feed/__main__.py --work-dir .
python -m http.server 8081
# → http://localhost:8081/elixir.html
```

На macOS при SSL-ошибках Supabase:

```bash
export SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())")
```

### GitHub Actions

Файл: `.github/workflows/planto-feed.yml`

- **Cron:** `0 */2 * * *` (каждые 2 часа)
- **Manual:** `workflow_dispatch`
- **Secrets:** 5 переменных из §3
- **Commit:** `data/planto-*.csv/json` от `Planto Feed Bot`

### Свой сервер

Cron каждые ~2 ч:

```bash
python /path/to/scripts/buyer-feed/__main__.py --work-dir /path/to/project
# затем деплой data/* на static host
```

---

## 9. Чеклист переноса в новый проект

- [ ] Скопировать `scripts/buyer-feed/` и `.github/workflows/planto-feed.yml`
- [ ] Создать `data/` и положить примеры CSV/JSON (или прогнать feed)
- [ ] Добавить `secrets.env.example`, убедиться что `secrets.env` в `.gitignore`
- [ ] Завести 5 секретов в GitHub Actions (или env на сервере)
- [ ] Обновить `config/planto.json`: `anchor`, AppMetrica ID, Direct login
- [ ] Проверить доступ к Supabase: таблица `rustore_subscription_entitlements`
- [ ] Подключить фронт: URLs `data/planto-*` + функции из §7
- [ ] Настроить деплой static site (Netlify и т.п.) на коммиты `data/*`
- [ ] Прогнать workflow вручную → `"errors": []` в meta
- [ ] Открыть Planto → «Auto feed · live», без жёлтого предупреждения

---

## 10. Текущий прод (reference)

| Что | Значение |
|-----|----------|
| GitHub repo | `lusnikovvaceslav9-oss/elixir-dashboard` |
| Netlify | `https://delightful-dasik-29191d.netlify.app/elixir.html` |
| Workflow | Actions → «Planto data feed» |
| Якорь кампании | `2026-06-05` |
| AppMetrica app | `6305902` |
| Direct login | `doxmediagroup` |

---

## 11. Траблшутинг

| Симптом | Причина | Решение |
|---------|---------|---------|
| `"errors": ["direct: …"]` | Нет/просрочен Direct token | Обновить `DIRECT_OAUTH_TOKEN` |
| `"supabase: …"` | Неверный `SUPABASE_DB_URL` или нет доступа | Проверить pooler URL и пароль |
| `Payments (CSV fallback)` в логе | Supabase bills не поднялись | Исправить DB URL; CSV — временный fallback |
| Жёлтый health на дашборде | CSV старее `meta.until` | Перезапустить feed, задеплоить `data/` |
| Чужие/старые цифры | `file://` или CORS-прокси | Same-origin HTTP из папки с `data/` |
| Нет колонки «Продано» | Старый фронт | Обновить `elixir.html` (auto feed branch) |

---

## 12. Связанные документы в репозитории

- `SETUP.md` — пошаговая установка для баера / агента
- `data/trials-reconcile.md` — заметки по сверке триалов (если нужна отладка)

---

*Сгенерировано для переноса Planto auto feed. Обновляй `anchor` и IDs при смене приложения или аккаунта рекламы.*
