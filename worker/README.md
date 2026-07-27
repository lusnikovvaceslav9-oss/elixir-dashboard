# Elixir Dashboard — auth proxy + Telegram bot (Cloudflare Worker)

`elixir.html` — статика на GitHub Pages. Этот Worker держит секреты на сервере
и принимает Telegram webhook для Q&A и отчётов. Бесплатный тариф Cloudflare достаточен.

## Telegram-бот

Полная инструкция: [TELEGRAM.md](./TELEGRAM.md)

Кратко:

```bash
cd worker
npm install
npx wrangler login
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_ALLOWED_CHAT_IDS
npx wrangler secret put JSONBIN_MASTER_KEY
npx wrangler secret put SESSION_SECRET
npx wrangler deploy
curl -X POST "https://<worker-url>/telegram/setup" -H "X-Setup-Key: <SESSION_SECRET>"
```

Команды бота: свободный текст («спенд планта за июль»), `/report`, `/digest`, `/refresh`, `/help`.

## Auth proxy

Секреты: `JSONBIN_MASTER_KEY`, `ADMIN_PASSWORD`, `SESSION_SECRET`, опционально `GITHUB_DISPATCH_TOKEN`.

Если токен Telegram светился в чате — сразу revoke в @BotFather и новый secret.
