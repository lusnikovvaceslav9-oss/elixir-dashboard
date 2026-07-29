/** Core dashboard storage — projects[] + _csv_uploads + _worker, Supabase-backed.
 *
 * Replaces JSONBin: one row per record (project, or the `_csv_uploads` /
 * `_worker` singletons), `data` holding the exact JSON object the app used
 * to keep in the JSONBin array. Same Supabase project as worker/library.js
 * (LIBRARY_SUPABASE_URL / LIBRARY_SUPABASE_SERVICE_KEY) — just another table.
 *
 * listAllRecords/replaceAllRecords are direct replacements for the jbGetRaw/
 * jbPutRaw pattern used to appear in worker/index.js, worker/telegram/bot.js
 * and worker/telegram/data.js — same "read everything, write everything"
 * contract, so callers don't need to change their own merge logic.
 */

async function sbFetch(env, path, opts = {}) {
  if (!env.LIBRARY_SUPABASE_URL || !env.LIBRARY_SUPABASE_SERVICE_KEY) {
    throw new Error('dashboard_supabase_not_configured');
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
  // Prefer: return=minimal can come back 200/201 with an empty body, not just 204.
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

/** Full raw list, including the `_worker` / `_csv_uploads` singleton rows. */
export async function listAllRecords(env) {
  const rows = await sbFetch(env, 'dashboard_records?select=id,data&order=id.asc');
  return (rows || []).map(r => r.data);
}

/** Whole-array replace: upserts everything present, deletes rows that dropped out. */
export async function replaceAllRecords(env, records) {
  const list = Array.isArray(records) ? records.filter(r => r && r.id) : [];
  // A legitimate save always carries at least _worker/_csv_uploads through — an
  // empty list can only mean a bug upstream (failed read treated as "no data",
  // a stray test call, ...), never real intent to delete every row.
  if (!list.length) throw new Error('refusing_empty_replace');
  const existing = await sbFetch(env, 'dashboard_records?select=id');
  const existingIds = new Set((existing || []).map(r => r.id));
  const incomingIds = new Set(list.map(r => r.id));

  if (list.length) {
    const rows = list.map(r => ({ id: r.id, data: r }));
    await sbFetch(env, 'dashboard_records', {
      method: 'POST',
      headers: { Prefer: 'return=minimal,resolution=merge-duplicates' },
      body: JSON.stringify(rows),
    });
  }

  const toDelete = [...existingIds].filter(id => !incomingIds.has(id));
  if (toDelete.length) {
    await sbFetch(env, `dashboard_records?id=in.(${toDelete.map(encodeURIComponent).join(',')})`, {
      method: 'DELETE',
      headers: { Prefer: 'return=minimal' },
    });
  }
}
