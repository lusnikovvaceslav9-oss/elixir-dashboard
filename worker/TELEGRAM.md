# Elixir Telegram Bot (Cloudflare Worker)

Бесплатный бот 24/7 на Cloudflare Workers: вопросы как в веб-чате + `/report` / `/digest` + утренний дайджест.

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

1. Напиши боту `/start` — он покажет `chat_id`.
2. Добавь chat_id в `TELEGRAM_ALLOWED_CHAT_IDS` (`wrangler secret put ...`) и перезадеплой/обнови secret.
3. Примеры:
   - `сколько потратили на планта в июле`
   - `/report Planto`
   - `/digest`
   - `/refresh`

Утренний дайджест: cron `0 7 * * *` UTC (= 10:00 МСК) → всем id из allowlist.

## Безопасность

- Токен бота **никогда** не коммитить в git.
- Если токен светился в чате/тикете — сразу `/revoke` в @BotFather и новый `wrangler secret put TELEGRAM_BOT_TOKEN`.
