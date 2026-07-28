# Elixir Dashboard — auth proxy + Telegram bot (Cloudflare Worker)

`elixir.html` — статика на GitHub Pages. Worker хранит секреты и принимает Telegram webhook.

## Telegram

```bash
cd worker
npm install
npx wrangler login
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_ALLOWED_CHAT_IDS
npx wrangler secret put TELEGRAM_DIGEST_CHAT_IDS   # admin user id, e.g. 547303409
npx wrangler secret put JSONBIN_MASTER_KEY
npx wrangler secret put SESSION_SECRET
npx wrangler deploy
curl -X POST "https://<worker-url>/telegram/setup" -H "X-Setup-Key: <SESSION_SECRET>"
```

Команды: свободный текст, `/report`, `/digest`, `/refresh`, `/help`.

Автодайджест (10:00 MSK) и `/digest` из группы → **только в личку** админу.

Превью без отправки: `POST /telegram/digest?preview=1` + заголовок `X-Setup-Key`.

## Auth proxy

Секреты: `JSONBIN_MASTER_KEY`, `ADMIN_PASSWORD`, `SESSION_SECRET`, опционально `GITHUB_DISPATCH_TOKEN` (Hupp/Planto feed dispatch).
