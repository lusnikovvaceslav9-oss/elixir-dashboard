// ═══════════════════════════════════════════════════════════════
// ELIXIR DASHBOARD — auth proxy + Telegram bot (Cloudflare Worker)
//
// Secrets (wrangler secret put):
//   ADMIN_PASSWORD, SESSION_SECRET,
//   GITHUB_DISPATCH_TOKEN (optional),
//   TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS,
//   TELEGRAM_WEBHOOK_SECRET (required — see worker/TELEGRAM.md),
//   LIBRARY_PASSWORD, LIBRARY_SUPABASE_URL, LIBRARY_SUPABASE_SERVICE_KEY,
//   DASHBOARD_WRITE_KEY (gates PUT /api/projects/raw and POST /api/csv-uploads)
//
// Vars in wrangler.toml:
//   ALLOWED_ORIGIN, GITHUB_REPO, GITHUB_BRANCH, HUPP_FEED_WORKFLOW
//
// projects[] / _csv_uploads / _worker live in Supabase (worker/dashboard.js) —
// JSONBIN_MASTER_KEY / JSONBIN_BIN_ID are no longer read by this file; the old
// JSONBin bin is left in place untouched as a read-only backup.
// ═══════════════════════════════════════════════════════════════

import { handleTelegramUpdate, sendDigestToAllowed, setupWebhook, notifyBudgetAlerts } from './telegram/bot.js';
import { getDashboardState } from './telegram/data.js';
import { inspectDigest } from './telegram/reports.js';
import { isLibraryEntity, listLibrary, createLibraryItem, updateLibraryItem, deleteLibraryItem } from './library.js';
import { listAllRecords, replaceAllRecords } from './dashboard.js';

const SESSION_TTL_SEC = 60 * 60 * 8;

function cors(env) {
  return {
    'Access-Control-Allow-Origin': env.ALLOWED_ORIGIN || '*',
    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Telegram-Bot-Api-Secret-Token, X-Dashboard-Key',
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

function requireDashboardWriteKey(req, env) {
  const key = req.headers.get('X-Dashboard-Key') || '';
  return !!env.DASHBOARD_WRITE_KEY && key === env.DASHBOARD_WRITE_KEY;
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
        // Fail closed: without a configured secret we cannot verify the request actually came from Telegram.
        if (!secret) return json({ ok: false, error: 'webhook_secret_not_configured' }, 500, env);
        const got = req.headers.get('X-Telegram-Bot-Api-Secret-Token') || '';
        if (got !== secret) return json({ ok: false, error: 'bad_secret' }, 401, env);
        const update = await req.json().catch(() => null);
        if (!update) return json({ ok: false }, 400, env);
        // Process in background so Telegram doesn't drop updates on slow data loads
        ctx.waitUntil(handleTelegramUpdate(env, update).catch(e => console.log('tg err', e.message || e)));
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
        if (url.searchParams.get('preview') === '1') {
          const state = await getDashboardState(env, { force: true });
          return json({ ok: true, ...inspectDigest(state) }, 200, env);
        }
        const result = await sendDigestToAllowed(env);
        return json(result, 200, env);
      }

      if (url.pathname === '/telegram/budget-alerts' && req.method === 'POST') {
        const setupKey = env.TELEGRAM_SETUP_KEY || env.SESSION_SECRET;
        const provided = req.headers.get('X-Setup-Key') || '';
        if (!setupKey || provided !== setupKey) return json({ ok: false, error: 'unauthorized' }, 401, env);
        const result = await notifyBudgetAlerts(env);
        return json(result, 200, env);
      }

      // ── Dashboard storage: projects[] / _csv_uploads / _worker (Supabase-backed) ──
      if (url.pathname === '/api/projects' && req.method === 'GET') {
        const raw = await listAllRecords(env);
        const projects = raw.filter(p => p && p.id !== '_worker' && p.id !== '_csv_uploads');
        return json(projects, 200, env);
      }

      // Full raw list incl. _worker/_csv_uploads — mirrors elixir.html's jbLoadRaw().
      if (url.pathname === '/api/projects/raw' && req.method === 'GET') {
        const raw = await listAllRecords(env);
        return json(raw, 200, env);
      }

      // Whole-array replace — mirrors elixir.html's jbSaveRecord(). Not a real
      // password: same "key baked into shipped JS" posture the old JSONBin
      // master key had, not a downgrade or an upgrade.
      if (url.pathname === '/api/projects/raw' && req.method === 'PUT') {
        if (!requireDashboardWriteKey(req, env)) return json({ ok: false, error: 'unauthorized' }, 401, env);
        const records = await req.json().catch(() => null);
        if (!Array.isArray(records)) return json({ ok: false, error: 'bad_body' }, 400, env);
        // A legitimate save always carries the _worker/_csv_uploads records through —
        // an empty array can only mean a bug upstream, never real intent to wipe everything.
        if (!records.length) return json({ ok: false, error: 'refusing_empty_replace' }, 400, env);
        await replaceAllRecords(env, records);
        return json({ ok: true }, 200, env);
      }

      if (url.pathname === '/api/csv-uploads' && req.method === 'GET') {
        const raw = await listAllRecords(env);
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

      if (url.pathname === '/api/csv-uploads' && req.method === 'POST') {
        if (!requireDashboardWriteKey(req, env)) return json({ ok: false, error: 'unauthorized' }, 401, env);
        const payload = await req.json().catch(() => null);
        if (!payload || typeof payload !== 'object') return json({ ok: false, error: 'bad_body' }, 400, env);
        const raw = await listAllRecords(env);
        const others = raw.filter(p => p && p.id !== '_csv_uploads');
        await replaceAllRecords(env, [...others, { id: '_csv_uploads', ...payload }]);
        return json({ ok: true }, 200, env);
      }

      // ── Library: FB accounts / pixels / creatives / insights (Supabase-backed) ──
      if (url.pathname === '/api/library/login' && req.method === 'POST') {
        const body = await req.json().catch(() => ({}));
        if (!env.LIBRARY_PASSWORD || body.password !== env.LIBRARY_PASSWORD) {
          return json({ ok: false, error: 'invalid_password' }, 401, env);
        }
        const exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SEC;
        const token = await signToken(env, exp);
        return json({ ok: true, token, expiresAt: exp * 1000 }, 200, env);
      }

      const libraryMatch = url.pathname.match(/^\/api\/library\/([a-z_]+)(?:\/([^/]+))?$/);
      if (libraryMatch) {
        // Read is gated too — the library isn't public like the rest of the dashboard.
        if (!(await requireAuth(req, env))) return json({ ok: false, error: 'unauthorized' }, 401, env);
        const [, entity, id] = libraryMatch;
        if (!isLibraryEntity(entity)) return json({ ok: false, error: 'unknown_entity' }, 404, env);
        try {
          if (req.method === 'GET') {
            const projectId = url.searchParams.get('project_id') || undefined;
            const rows = await listLibrary(env, entity, projectId);
            return json(rows, 200, env);
          }
          if (req.method === 'POST' && !id) {
            const body = await req.json().catch(() => null);
            if (!body || typeof body !== 'object') return json({ ok: false, error: 'bad_body' }, 400, env);
            const row = await createLibraryItem(env, entity, body);
            return json(row, 200, env);
          }
          if (req.method === 'PUT' && id) {
            const body = await req.json().catch(() => null);
            if (!body || typeof body !== 'object') return json({ ok: false, error: 'bad_body' }, 400, env);
            const row = await updateLibraryItem(env, entity, id, body);
            return json(row, 200, env);
          }
          if (req.method === 'DELETE' && id) {
            await deleteLibraryItem(env, entity, id);
            return json({ ok: true }, 200, env);
          }
          return json({ ok: false, error: 'method_not_allowed' }, 405, env);
        } catch (e) {
          return json({ ok: false, error: e.message || String(e) }, 502, env);
        }
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
        return json({
          ok: true,
          service: 'elixir-dashboard-proxy',
          telegram: !!env.TELEGRAM_BOT_TOKEN,
        }, 200, env);
      }

      return json({ ok: false, error: 'not_found' }, 404, env);
    } catch (e) {
      return json({ ok: false, error: e.message || String(e) }, 500, env);
    }
  },

  async scheduled(event, env, ctx) {
    // Morning digest 10:00 MSK → admin DM (+ budget alerts after)
    if (event.cron === '0 7 * * *') {
      ctx.waitUntil(
        sendDigestToAllowed(env)
          .then(r => console.log('digest', JSON.stringify(r)))
          .catch(e => console.log('digest err', e.message || e))
      );
      return;
    }
    // Every 6h: budget exhaustion alerts only
    ctx.waitUntil(
      notifyBudgetAlerts(env)
        .then(r => console.log('budget alerts', JSON.stringify(r)))
        .catch(e => console.log('budget alerts err', e.message || e))
    );
  },
};
