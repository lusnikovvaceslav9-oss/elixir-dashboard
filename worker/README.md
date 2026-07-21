# Elixir Dashboard — auth proxy (Cloudflare Worker)

Зачем: `elixir.html` — статика на GitHub Pages, без бэкенда. Сейчас JSONBin
master key и GitHub PAT лежат прямо в JS-коде страницы — их видно через
"просмотр кода страницы". Этот Worker закрывает секреты на сервере: браузер
общается только с Worker'ом, а он уже сам стучится в JSONBin/GitHub с ключами,
которых у клиента никогда нет.

Бесплатный тариф Cloudflare Workers более чем достаточен для этой нагрузки.

## Деплой (~5 минут)

1. Установи Wrangler (если ещё нет): `npm install -g wrangler`
2. Залогинься: `wrangler login` (откроется браузер, привяжет твой Cloudflare-аккаунт)
3. Из папки `worker/`: `wrangler deploy`
4. Задай секреты (каждая команда спросит значение интерактивно):
   ```
   wrangler secret put JSONBIN_MASTER_KEY
   wrangler secret put ADMIN_PASSWORD
   wrangler secret put SESSION_SECRET
   wrangler secret put GITHUB_DISPATCH_TOKEN   # опционально, для кнопки "Обновить" на Hupp
   ```
   - `JSONBIN_MASTER_KEY` — текущий ключ из `elixir.html` (`JB_KEY`, начинается на `$2a$10$...`).
     После переезда на Worker его стоит перевыпустить в JSONBin (Settings → API Keys → regenerate),
     раз он уже "засветился" в публичном исходнике.
   - `ADMIN_PASSWORD` — новый пароль для входа в Admin (можно оставить текущий `elixir2026`,
     но раз он был в открытом виде в гите — лучше сменить).
   - `SESSION_SECRET` — любая случайная строка (32+ символов), например: `openssl rand -hex 32`
   - `GITHUB_DISPATCH_TOKEN` — GitHub PAT (fine-grained, только `actions: write` на этот репозиторий),
     сейчас лежит в JSONBin как `_worker.githubDispatchToken` — тоже стоит перевыпустить.
5. После деплоя Wrangler покажет URL вида `https://elixir-dashboard-proxy.<subdomain>.workers.dev`
6. Пришли этот URL — я подключу `elixir.html` к Worker'у и уберу из фронта:
   - `JB_KEY` (JSONBin master key)
   - `ADMIN_PW_HASH` (сравнение пароля — целиком уедет на сервер)
   - `_worker.githubDispatchToken` из JSONBin (больше не нужен клиенту)

## Что проверить перед тем как считать это готовым

- [ ] `GET /api/projects` отдаёт список проектов без `_worker`/`_csv_uploads`
- [ ] `POST /api/admin/login` с верным паролем возвращает `{ok:true, token, expiresAt}`
- [ ] `POST /api/admin/login` с неверным паролем → 401
- [ ] `POST /api/projects` без `Authorization` → 401
- [ ] `POST /api/projects` с валидным токеном → сохраняет и не теряет `_worker`/`_csv_uploads`
- [ ] `wrangler.toml` → `ALLOWED_ORIGIN` выставлен на реальный домен GitHub Pages (не `*`) для продакшена

## Ограничения текущей версии

- Сессионный токен — просто HMAC-подписанная метка времени (stateless), без revoke-листа.
  Если токен утёк — он валиден до истечения TTL (8ч). Этого достаточно для внутреннего
  инструмента, но не для чего-то более критичного.
- Rate limiting на `/api/admin/login` не реализован — при желании можно добавить через
  Cloudflare's built-in rate limiting rules (не в коде Worker'а, а в дашборде Cloudflare).
