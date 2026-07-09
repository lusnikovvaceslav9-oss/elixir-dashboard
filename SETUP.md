# Plantó — установка фида данных (для баера / ИИ-агента)

Это пакет, который сам собирает данные Plantó (расход, установки, триалы, оплаты) и показывает их на дашборде `elixir.html`. Дашборд читает готовые файлы из `data/` — ручные Google-таблицы не нужны.

**На вход:** 5 секретов (в Bitwarden). **На выход:** обновлённые `data/planto-daily.csv`, `data/planto-cohort.json`, `data/planto-meta.json`, которые деплоятся на сайт.

---

## TL;DR для ИИ-агента (делай по порядку)

1. Распакуй архив. Рабочая папка — корень пакета (где лежат `elixir.html`, `README-buyer.md`, `serve-local.ps1`, папки `data/`, `scripts/`, `.github/`).
2. Секреты из Bitwarden (5 ключей ниже). Не коммить их. Два способа отдать фиду:
   - **прод/авто:** GitHub → Settings → Secrets and variables → Actions (см. «Вариант A»);
   - **локально:** `cp secrets.env.example secrets.env` и заполни (см. «Вариант B»).
3. Проверка локально: `pip install -r scripts/buyer-feed/requirements.txt`, затем прогон фида (команда ниже). В логе должно быть `Supabase bills: N charges` и `Supabase trials: N starts`, а в `data/planto-meta.json` — `"errors": []`.
4. Прод: запушь пакет в git-репозиторий с GitHub Actions → workflow `.github/workflows/planto-feed.yml` крутит фид **каждый час** и коммитит `data/`. Сайт деплоится через GitHub Pages (`.github/workflows/pages.yml`).
5. Проверка результата: открой `elixir.html` через локальный http-сервер (НЕ двойным кликом) → проект Planto → блок «Auto feed» без жёлтого предупреждения.

Критерий готовности: фид отработал без ошибок, дашборд открылся, цифры за сегодня совпадают с RuStore.

---

## Секреты (5 ключей, из Bitwarden)

| Ключ | Что это | Пример/значение |
|---|---|---|
| `APPMETRICA_OAUTH_TOKEN` | токен AppMetrica Reporting API (установки, crosscheck триалов) | из Bitwarden |
| `APPMETRICA_APPLICATION_ID` | ID приложения в AppMetrica | `6305902` |
| `DIRECT_OAUTH_TOKEN` | токен Yandex Direct API (расход) | из Bitwarden |
| `DIRECT_CLIENT_LOGIN` | логин клиента Директа | `doxmediagroup` |
| `SUPABASE_DB_URL` | строка подключения Postgres (триалы + биллы) | `postgresql://…pooler.supabase.com:5432/postgres` |

Как фид ищет секреты (первое непустое значение по каждому ключу выигрывает):
1. переменные окружения (так работает GitHub Action);
2. `secrets.env` в корне пакета;
3. `supabase/secrets.env` (если есть).

**Никогда не коммить `secrets.env`** — он в `.gitignore`. Коммитить можно только `secrets.env.example`.

---

## Вариант A — авто через GitHub Actions (рекомендуемый, прод)

1. Создай приватный git-репозиторий и запушь туда **весь** пакет (включая `.github/`).
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**. Добавь 5 секретов из таблицы выше (имена — точь-в-точь как ключи).
3. Repo → вкладка **Actions** → включи workflows, если выключены.
4. Проверь вручную: **Actions → «Planto data feed» → Run workflow**. После прогона в репозитории обновятся файлы `data/*` (коммит от `Planto Feed Bot`).
5. Дальше workflow идёт сам по расписанию `cron: 0 * * * *` — **каждый час**.
6. Деплой сайта: GitHub Pages (`pages.yml` на push в `main`). Кнопка «Обновить» в дашборде подтягивает свежий `data/` из git (raw.githubusercontent), без Netlify. Открывай `https://<user>.github.io/<repo>/elixir.html`.

Файл, который всё это делает, — `.github/workflows/planto-feed.yml`. Секреты в нём читаются как `env` из `${{ secrets.* }}`, файл `secrets.env` в проде **не нужен**.

---

## Вариант B — локальный прогон / проверка

Требования: Python 3.12+.

```bash
# из корня пакета
cp secrets.env.example secrets.env   # затем впиши значения из Bitwarden
pip install -r scripts/buyer-feed/requirements.txt
```

Прогон фида:

```bash
python scripts/buyer-feed/__main__.py --work-dir .
```

Открыть дашборд (обязательно через http-сервер из папки с `data/`, иначе CSV уйдёт в CORS-прокси и подтянет чужой кэш):

```bash
python -m http.server 8081 --bind 127.0.0.1
# → http://localhost:8081/elixir.html
```

На Windows всё это одной командой: `./serve-local.ps1` (прогон фида + сервер).

---

## Вариант C — свой сервер без GitHub (cron / Task Scheduler)

Задай 5 переменных окружения (или заполни `secrets.env`) и повесь в планировщик команду каждые ~2 часа:

```bash
python scripts/buyer-feed/__main__.py --work-dir /path/to/package
```

После прогона — задеплой обновлённые `data/*` (git push или заливка на хостинг). На Windows есть готовый регистратор задачи: `scripts/register-buyer-feed-task.ps1`.

---

## Что означают данные

- **Spend** — расход Yandex Direct без НДС.
- **Installs** — установки, AppMetrica Reporting API.
- **Trials** — старты триалов из Supabase/RuStore (distinct user_id по дате старта). AppMetrica `trial_started` — только сверка в meta.
- **Bills (fb) / Продано / Paid net** — оплаты из Supabase: подписка дошла до `period='MAIN'` + `status='ACTIVE'` = списание прошло. Возвраты (`MAIN CLOSED`) отсекаются сами. Сумма по продукту (годовой 2490 ₽, месячный 399 ₽). Руками ничего вводить не нужно — новую оплату подхватит следующий прогон.
- **Когорты** — недели по 7 дней от якоря `2026-06-05`; триалы в корзине = distinct users со стартом в дни корзины.
- `data/rustore-payments.csv` — **fallback** (если Supabase недоступен) и место для ручных корректировок; в обычном режиме не трогается.

Блок **Auto feed** на дашборде показывает срез последнего прогона. Жёлтое предупреждение = CSV не совпадает с `meta` (обычно данные не перезалиты после прогона).

---

## Траблшутинг

- **`Payments (CSV fallback)` вместо `Supabase bills` в логе** → `SUPABASE_DB_URL` не подхватился. Проверь, что секрет задан именно там, откуда запускаешь (env для Action; `secrets.env` для локали), и что в строке есть пароль.
- **`psycopg2` ModuleNotFoundError** → не установлены зависимости: `pip install -r scripts/buyer-feed/requirements.txt`.
- **`"errors": [...]` в `planto-meta.json`** → там текст проблемы по каждому источнику (direct/appmetrica/supabase). Триалы/биллы без Supabase не соберутся.
- **Дашборд показывает старые/чужие цифры** → открыл `elixir.html` двойным кликом или Live Server из другой папки. Запускай http-сервер из папки, где лежит `data/` (same-origin).
- **Данные на сайте не меняются** → проверь Actions → «Planto data feed» (коммит `data/*`) и «Deploy GitHub Pages»; в дашборде «Обновить» тянет свежий SHA с `raw.githubusercontent.com`.

## Чего НЕ делать

- Не коммитить `secrets.env` и реальные токены (только `secrets.env.example`).
- Не открывать `elixir.html` двойным кликом — только через http-сервер.
- Не редактировать `data/*` руками — их перезапишет следующий прогон фида.
