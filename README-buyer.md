# Planto — авто-данные для дашборда баера (elixir.html)

Пакет для [elixir-dashboard](https://github.com/lusnikovvaceslav9-oss/elixir-dashboard): страница **Planto** читает `data/planto-daily.csv` и `data/planto-cohort.json` вместо ручной Google-таблицы.

## Что внутри

| Файл | Назначение |
|------|------------|
| `elixir.html` | Shell дашборда (разметка + подключение CSS/JS) |
| `elixir.css` | Стили |
| `elixir.js` | Логика дашборда |
| `data/planto-daily.csv` | Spend, Inst, Trials, Bills по дням |
| `data/planto-cohort.json` | Недельные когорты (W1, W2 … от 2026-06-05) |
| `data/planto-meta.json` | Мета прогона фида (источники, ошибки) |
| `data/trials-reconcile.md` | Сравнение источников триалов (опционально) |
| `data/rustore-payments.csv` | Чеки RuStore — **fallback/корректировки** (основной источник биллов — Supabase) |
| `scripts/buyer-feed/` | Python-скрипт сбора данных |
| `.github/workflows/planto-feed.yml` | Автообновление **каждые 2 часа** |

## Установка (один раз)

1. Скопируй содержимое пакета в репо дашборда (или замени `elixir.html` + `elixir.css` + `elixir.js` + добавь `data/`, `scripts/`, `.github/`).
2. В **GitHub → Settings → Secrets** добавь ключи из `secrets.env.example`.
3. Убедись, что Netlify деплоит репо на push (или перезаливай `data/` вручную после локального запуска).
4. Открой `https://<your-site>/elixir.html` → проект Planto.

## Локальный запуск (проверка)

```powershell
cd docs/buyer-package
.\serve-local.ps1
```

Скрипт: прогон feed → `http://localhost:8081/elixir.html`. **Важно:** сервер должен стартовать из папки, где лежит `data/` (same-origin). Не открывай `elixir.html` двойным кликом и не запускай Live Server из другой директории — иначе CSV уйдёт в CORS-прокси и подтянет чужой кэш.

Ручной вариант:

```powershell
cd docs/buyer-package
pip install -r scripts/buyer-feed/requirements.txt
python scripts/buyer-feed/__main__.py --work-dir .
python -m http.server 8081 --bind 127.0.0.1
```

В блоке **Auto feed** смотри «Срез feed: 2026-07-02 · в CSV 28 дн. · июль: 2». Жёлтое предупреждение = CSV не совпадает с meta.

## Источники данных

- **Spend** — Yandex Direct API (без НДС)
- **Installs** — AppMetrica **Reporting API** (агрегаты по дням)
- **Trials** — Supabase / RuStore: **дата старта триала** (`trial_attribution: supabase_trial_start`), distinct user_id; AppMetrica `trial_started` — только crosscheck в meta
- **Bills (fb) / Продано / Paid net** — Supabase (`bills: supabase_main_active`): подписка дошла до `period='MAIN'` + `status='ACTIVE'` = списание прошло. Возвраты (`MAIN CLOSED`) отсекаются сами. Сумма по продукту (годовой 2490 ₽, месячный 399 ₽), дата — `last_event_time`. `data/rustore-payments.csv` — **fallback** (если БД недоступна) и ручные корректировки
- **Когорты** — 7 календарных дней от якоря **2026-06-05**; триалы в корзине = distinct users с trial start в днях корзины

## Каденс

GitHub Action: `cron: 0 */2 * * *` — каждые 2 часа. Запросы **узкие** (последние N дней + догон атрибуции), без 100 МБ Logs API.

## Биллы и оплаты

Основной источник — **Supabase**: фид сам считает списания из состояния подписок (`period='MAIN'` + `status='ACTIVE'`). Руками ничего вводить не нужно — при новой оплате следующий прогон подхватит bills, «Продано» и Paid net.

`data/rustore-payments.csv` нужен только если:
- Supabase недоступен (fallback — фид возьмёт биллы из CSV);
- нужна ручная корректировка (редкий кейс: возврат, которого не видно в состоянии подписки, или чек, не долетевший в БД).

## Fallback без GitHub Action

Windows Task Scheduler / cron:

```powershell
python -m scripts.buyer-feed --work-dir C:\path\to\elixir-dashboard
```

Затем commit + push `data/*` или drag-drop на Netlify.
