# Elixir Telegram Bot (Cloudflare Worker)

Бесплатный бот 24/7 на Cloudflare Workers: вопросы как в веб-чате + `/report` / `/digest` по запросу.

## Секреты

```bash
cd worker
npx wrangler login
npx wrangler secret put TELEGRAM_BOT_TOKEN          # токен от @BotFather
npx wrangler secret put TELEGRAM_ALLOWED_CHAT_IDS   # например: 123456789
npx wrangler secret put SESSION_SECRET              # openssl rand -hex 32
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET     # openssl rand -hex 24 — обязателен, иначе /telegram/webhook отклоняет все запросы
# опционально:
# npx wrangler secret put ADMIN_PASSWORD
```

## Деплой

```bash
cd worker
npx wrangler deploy
```

После деплоя зарегистрируй webhook (подставь URL воркера и SESSION_SECRET). Убедись, что `TELEGRAM_WEBHOOK_SECRET` уже задан секретом до этого шага — `/telegram/setup` подхватит его и пропишет в Telegram, а `/telegram/webhook` без него отклоняет все входящие апдейты:

```bash
curl -X POST "https://elixir-dashboard-proxy.<subdomain>.workers.dev/telegram/setup" \
  -H "X-Setup-Key: <SESSION_SECRET>"
```

## Использование

1. Напиши боту в личку `/start` — он покажет `chat_id` / `user_id`.
2. Добавь id в `TELEGRAM_ALLOWED_CHAT_IDS` (`wrangler secret put ...`).

### Добавить бота в беседу (группу)

1. В группе: **Добавить участников** → найди `@elexir_dashbot` → добавить.
2. В группе напиши `/id@elexir_dashbot` — бот пришлёт `chat_id` группы (отрицательный, вида `-100…`).
3. **Важно — Group Privacy** в [@BotFather](https://t.me/BotFather):
   - `/mybots` → Elixir → **Bot Settings** → **Group Privacy** → **Turn off**
   - Иначе бот в группе видит только команды и сообщения с `@elexir_dashbot`, обычный текст («спенд планта») до него не доходит.
4. После выключения Privacy можно писать свободно. Пока Privacy включён — пиши так:
   - `@elexir_dashbot спенд планта за июль`
   - `/report@elexir_dashbot Planto`

Доступ: достаточно твоего `user_id` в whitelist — в любой группе, куда тебя пустили, бот ответит. Либо отдельно добавь `chat_id` группы.

Автодайджест и `/digest` → **в личку** (`TELEGRAM_DIGEST_CHAT_IDS` / `DIGEST_CHAT_IDS`), не в группу.

## Безопасность

- Токен бота **никогда** не коммитить в git.
- Если токен светился в чате/тикете — сразу `/revoke` в @BotFather и новый `wrangler secret put TELEGRAM_BOT_TOKEN`.

## Библиотека (ФБ-кабинеты / пиксели / креативы / наработки)

Тот же воркер отдаёт `/api/library/*` для раздела «Библиотека» в `elixir.html` — CRUD поверх отдельного Supabase-проекта (не того, что для Planto-биллинга). Схема — в `scripts/library/schema.sql`, прогнать один раз в SQL editor нового Supabase-проекта.

```bash
cd worker
npx wrangler secret put LIBRARY_PASSWORD              # пароль на просмотр библиотеки (не путать с ADMIN_PASSWORD)
npx wrangler secret put LIBRARY_SUPABASE_URL           # https://<project>.supabase.co
npx wrangler secret put LIBRARY_SUPABASE_SERVICE_KEY   # service_role key из Settings → API (не anon!)
npx wrangler deploy
```

`service_role` key даёт полный доступ в обход RLS — держим его только в секретах воркера, в браузер он никогда не попадает. Таблицы (`fb_accounts`, `pixels`, `creatives`, `insights`, `contractors`) сознательно не хранят логины/пароли/токены доступа к рекламным кабинетам — только ID/статус/лимиты/заметки.

## Основное хранилище (projects[] / _csv_uploads / _worker)

Список проектов и загруженные CSV раньше жили в JSONBin, куда `elixir.html` ходил напрямую своим мастер-ключом из браузера. Теперь это `worker/dashboard.js` поверх той же Supabase-базы, что и Библиотека (`LIBRARY_SUPABASE_URL`/`LIBRARY_SUPABASE_SERVICE_KEY` — второй проект не нужен), схема — `scripts/dashboard-db/schema.sql`.

`DASHBOARD_WRITE_KEY` — служебный ключ, гейтит `PUT /api/projects/raw` и `POST /api/csv-uploads`. Уже сгенерирован и задан секретом, отдельно настраивать не нужно; тот же уровень защиты, что раньше был у JSONBin-ключа (зашит в отдаваемый браузеру JS, не полноценный контроль доступа).

Старый JSONBin-бин **не удалён** — код его больше не читает, но он остаётся read-only бэкапом на случай отката.
