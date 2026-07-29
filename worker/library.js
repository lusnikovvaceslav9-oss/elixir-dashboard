/** Elixir Library — Supabase REST (PostgREST) proxy for FB accounts / pixels / creatives / insights.
 *
 * Cloudflare Workers can't hold a raw Postgres connection (unlike scripts/buyer-feed/supabase.py,
 * which talks psycopg2 directly), so this goes through Supabase's REST API with the service-role
 * key. The key never leaves the Worker — the browser only ever sees the Worker's own bearer token.
 */

export const LIBRARY_ENTITIES = ['fb_accounts', 'pixels', 'creatives', 'insights'];

export function isLibraryEntity(entity) {
  return LIBRARY_ENTITIES.includes(entity);
}

async function sbFetch(env, path, opts = {}) {
  if (!env.LIBRARY_SUPABASE_URL || !env.LIBRARY_SUPABASE_SERVICE_KEY) {
    throw new Error('library_supabase_not_configured');
  }
  const url = `${env.LIBRARY_SUPABASE_URL.replace(/\/+$/, '')}/rest/v1/${path}`;
  const res = await fetch(url, {
    ...opts,
    headers: {
      apikey: env.LIBRARY_SUPABASE_SERVICE_KEY,
      Authorization: `Bearer ${env.LIBRARY_SUPABASE_SERVICE_KEY}`,
      'Content-Type': 'application/json',
      Prefer: 'return=representation',
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Supabase ${res.status}: ${text.slice(0, 300)}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export async function listLibrary(env, entity, projectId) {
  const params = new URLSearchParams({ select: '*', order: 'created_at.desc' });
  if (projectId) params.set('project_id', `eq.${projectId}`);
  return sbFetch(env, `${entity}?${params.toString()}`);
}

export async function createLibraryItem(env, entity, body) {
  const rows = await sbFetch(env, entity, { method: 'POST', body: JSON.stringify(body) });
  return Array.isArray(rows) ? rows[0] : rows;
}

export async function updateLibraryItem(env, entity, id, body) {
  const params = new URLSearchParams({ id: `eq.${id}` });
  const rows = await sbFetch(env, `${entity}?${params.toString()}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
  return Array.isArray(rows) ? rows[0] : rows;
}

export async function deleteLibraryItem(env, entity, id) {
  const params = new URLSearchParams({ id: `eq.${id}` });
  await sbFetch(env, `${entity}?${params.toString()}`, { method: 'DELETE' });
  return true;
}
