# Elixir Telegram Bot (Cloudflare Worker)

Бесплатный бот 24/7 на Cloudflare Workers: вопросы как в веб-чате + `/report` / `/digest` по запросу.

## Секреты

```bash
cd worker
npx wrangler login
npx wrangler secret put TELEGRAM_BOT_TOKEN          # токен от @BotFather
npx wrangler secret put TELEGRAM_ALLOWED_CHAT_IDS   # например: 123456789
npx wrangler secret put JSONBIN_MASTER_KEY          # тот же, что для дашборда
npx wrangler secret put SESSION_SECRET              # openssl rand -hex 32
# опционально:
# npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
# npx wrangler secret put ADMIN_PASSWORD
```

## Деплой

```bash
cd worker
npx wrangler deploy
```

После деплоя зарегистрируй webhook (подставь URL воркера и SESSION_SECRET):

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
