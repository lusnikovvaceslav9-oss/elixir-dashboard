// ═══════════════════════════════════════════════════════════════
// ELIXIR DASHBOARD — auth proxy + Telegram bot (Cloudflare Worker)
//
// Secrets (wrangler secret put):
//   JSONBIN_MASTER_KEY, ADMIN_PASSWORD, SESSION_SECRET,
//   GITHUB_DISPATCH_TOKEN (optional),
//   TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS,
//   TELEGRAM_WEBHOOK_SECRET (optional)
//
// Vars in wrangler.toml:
//   JSONBIN_BIN_ID, ALLOWED_ORIGIN, GITHUB_REPO, GITHUB_BRANCH, HUPP_FEED_WORKFLOW
// ═══════════════════════════════════════════════════════════════

import { handleTelegramUpdate, sendDigestToAllowed, setupWebhook } from './telegram/bot.js';

const JSONBIN_API = 'https://api.jsonbin.io/v3';
const SESSION_TTL_SEC = 60 * 60 * 8;

function cors(env) {
  return {
    'Access-Control-Allow-Origin': env.ALLOWED_ORIGIN || '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Telegram-Bot-Api-Secret-Token',
  };
}

function json(data, status, env) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...cors(env) },
  });
}

async function hmacHex(secret, msg) {
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(msg));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, '0')).join('');
}

async function signToken(env, exp) {
  return `${exp}.${await hmacHex(env.SESSION_SECRET, String(exp))}`;
}

async function verifyToken(env, token) {
  if (!token) return false;
  const [expStr, sig] = String(token).split('.');
  const exp = Number(expStr);
  if (!exp || Date.now() / 1000 > exp) return false;
  const expected = await hmacHex(env.SESSION_SECRET, String(exp));
  return expected.length === sig?.length && expected === sig;
}

function bearer(req) {
  const h = req.headers.get('Authorization') || '';
  return h.startsWith('Bearer ') ? h.slice(7) : '';
}

async function requireAuth(req, env) {
  return verifyToken(env, bearer(req));
}

async function jbGetRaw(env) {
  const res = await fetch(`${JSONBIN_API}/b/${env.JSONBIN_BIN_ID}/latest`, {
    headers: { 'X-Master-Key': env.JSONBIN_MASTER_KEY },
  });
  if (!res.ok) throw new Error(`JSONBin GET ${res.status}`);
  const data = await res.json();
  return Array.isArray(data.record) ? data.record : [];
}

async function jbPutRaw(env, record) {
  const res = await fetch(`${JSONBIN_API}/b/${env.JSONBIN_BIN_ID}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-Master-Key': env.JSONBIN_MASTER_KEY },
    body: JSON.stringify(record),
  });
  if (!res.ok) throw new Error(`JSONBin PUT ${res.status}`);
  return true;
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors(env) });

    try {
      // ── Telegram webhook ──
      if (url.pathname === '/telegram/webhook' && req.method === 'POST') {
        if (!env.TELEGRAM_BOT_TOKEN) return json({ ok: false, error: 'no_token' }, 500, env);
        const secret = env.TELEGRAM_WEBHOOK_SECRET;
        if (secret) {
          const got = req.headers.get('X-Telegram-Bot-Api-Secret-Token') || '';
          if (got !== secret) return json({ ok: false, error: 'bad_secret' }, 401, env);
        }
        const update = await req.json().catch(() => null);
        if (!update) return json({ ok: false }, 400, env);
        // Respond 200 quickly; process in background when possible
        ctx.waitUntil(handleTelegramUpdate(env, update).catch(e => console.log('tg err', e.message)));
        return json({ ok: true }, 200, env);
      }

      // ── One-shot: register webhook (call once after deploy) ──
      if (url.pathname === '/telegram/setup' && req.method === 'POST') {
        const setupKey = env.TELEGRAM_SETUP_KEY || env.SESSION_SECRET;
        const provided = req.headers.get('X-Setup-Key') || '';
        if (!setupKey || provided !== setupKey) return json({ ok: false, error: 'unauthorized' }, 401, env);
        if (!env.TELEGRAM_BOT_TOKEN) return json({ ok: false, error: 'no_token' }, 500, env);
        const origin = url.origin;
        const result = await setupWebhook(env, origin);
        return json({ ok: true, webhook: result }, 200, env);
      }

      // ── Manual digest trigger ──
      if (url.pathname === '/telegram/digest' && req.method === 'POST') {
        const setupKey = env.TELEGRAM_SETUP_KEY || env.SESSION_SECRET;
        const provided = req.headers.get('X-Setup-Key') || '';
        if (!setupKey || provided !== setupKey) return json({ ok: false, error: 'unauthorized' }, 401, env);
        const result = await sendDigestToAllowed(env);
        return json(result, 200, env);
      }

      if (url.pathname === '/api/projects' && req.method === 'GET') {
        const raw = await jbGetRaw(env);
        const projects = raw.filter(p => p && p.id !== '_worker' && p.id !== '_csv_uploads');
        return json(projects, 200, env);
      }

      if (url.pathname === '/api/csv-uploads' && req.method === 'GET') {
        const raw = await jbGetRaw(env);
        const rec = raw.find(p => p?.id === '_csv_uploads') || {};
        return json(rec, 200, env);
      }

      if (url.pathname === '/api/admin/login' && req.method === 'POST') {
        const body = await req.json().catch(() => ({}));
        if (!env.ADMIN_PASSWORD || body.password !== env.ADMIN_PASSWORD) {
          return json({ ok: false, error: 'invalid_password' }, 401, env);
        }
        const exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SEC;
        const token = await signToken(env, exp);
        return json({ ok: true, token, expiresAt: exp * 1000 }, 200, env);
      }

      if (url.pathname === '/api/projects' && req.method === 'POST') {
        if (!(await requireAuth(req, env))) return json({ ok: false, error: 'unauthorized' }, 401, env);
        const projects = await req.json().catch(() => null);
        if (!Array.isArray(projects)) return json({ ok: false, error: 'bad_body' }, 400, env);
        const raw = await jbGetRaw(env);
        const special = raw.filter(p => p && (p.id === '_worker' || p.id === '_csv_uploads'));
        await jbPutRaw(env, [...projects, ...special]);
        return json({ ok: true }, 200, env);
      }

      if (url.pathname === '/api/csv-uploads' && req.method === 'POST') {
        if (!(await requireAuth(req, env))) return json({ ok: false, error: 'unauthorized' }, 401, env);
        const payload = await req.json().catch(() => null);
        if (!payload || typeof payload !== 'object') return json({ ok: false, error: 'bad_body' }, 400, env);
        const raw = await jbGetRaw(env);
        const others = raw.filter(p => p && p.id !== '_csv_uploads');
        await jbPutRaw(env, [...others, { id: '_csv_uploads', ...payload }]);
        return json({ ok: true }, 200, env);
      }

      if (url.pathname === '/api/hupp-feed/dispatch' && req.method === 'POST') {
        if (!(await requireAuth(req, env))) return json({ ok: false, error: 'unauthorized' }, 401, env);
        if (!env.GITHUB_DISPATCH_TOKEN) return json({ ok: false, skipped: true, reason: 'no_token' }, 200, env);
        const ghUrl = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.HUPP_FEED_WORKFLOW}/dispatches`;
        const resp = await fetch(ghUrl, {
          method: 'POST',
          headers: {
            Accept: 'application/vnd.github+json',
            Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ ref: env.GITHUB_BRANCH || 'main' }),
        });
        if (resp.status === 204) return json({ ok: true }, 200, env);
        const t = await resp.text();
        return json({ ok: false, reason: `GitHub ${resp.status}: ${t.slice(0, 180)}` }, 200, env);
      }

      if (url.pathname === '/' || url.pathname === '/health') {
        return json({ ok: true, service: 'elixir-dashboard-proxy', telegram: !!env.TELEGRAM_BOT_TOKEN }, 200, env);
      }

      return json({ ok: false, error: 'not_found' }, 404, env);
    } catch (e) {
      return json({ ok: false, error: e.message || String(e) }, 500, env);
    }
  },

  /** Morning digest — cron in wrangler.toml (07:00 UTC = 10:00 MSK) */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(sendDigestToAllowed(env).catch(e => console.log('digest err', e.message)));
  },
};
