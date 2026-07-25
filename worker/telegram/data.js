/** Load dashboard projects + daily rows for Telegram bot. */

import {
  parseSheetRows, aggregateByDate, dedupeSources, sourceUrlKey, sum, rowIso,
  mergeUploadEntries, hydrateStoredRow,
} from './csv.js';

const JSONBIN_API = 'https://api.jsonbin.io/v3';

async function fetchText(url, timeoutMs = 15000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      signal: ctrl.signal,
      headers: { 'User-Agent': 'ElixirTelegramBot/1.0' },
      redirect: 'follow',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    if (text.trim().startsWith('<!')) throw new Error('HTML instead of CSV');
    return text;
  } finally {
    clearTimeout(t);
  }
}

function sheetExportUrl(url) {
  const id = (String(url).match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/) || [])[1];
  if (!id) return url;
  const gid = (String(url).match(/[#&?]gid=(\d+)/) || [])[1];
  let u = `https://docs.google.com/spreadsheets/d/${id}/export?format=csv`;
  if (gid && gid !== '0') u += `&gid=${gid}`;
  return u;
}

function rawFeed(env, path) {
  const repo = env.GITHUB_REPO || 'lusnikovvaceslav9-oss/elixir-dashboard';
  const branch = env.GITHUB_BRANCH || 'main';
  return `https://raw.githubusercontent.com/${repo}/${branch}/${path}`;
}

async function loadProjects(env) {
  const res = await fetch(`${JSONBIN_API}/b/${env.JSONBIN_BIN_ID}/latest`, {
    headers: { 'X-Master-Key': env.JSONBIN_MASTER_KEY },
  });
  if (!res.ok) throw new Error(`JSONBin ${res.status}`);
  const data = await res.json();
  const raw = Array.isArray(data.record) ? data.record : [];
  return raw.filter(p => p && p.id && p.id !== '_worker' && p.id !== '_csv_uploads');
}

async function loadCsvUploads(env) {
  try {
    const res = await fetch(`${JSONBIN_API}/b/${env.JSONBIN_BIN_ID}/latest`, {
      headers: { 'X-Master-Key': env.JSONBIN_MASTER_KEY },
    });
    if (!res.ok) return {};
    const data = await res.json();
    const raw = Array.isArray(data.record) ? data.record : [];
    const rec = raw.find(p => p?.id === '_csv_uploads');
    return rec?.uploads || {};
  } catch {
    return {};
  }
}

function normalizeProj(p) {
  let sheetSources = (p.sheetSources || [])
    .filter(s => s?.url)
    .map((s, i) => ({
      id: s.id || `src_${i}`,
      url: s.url,
      label: (s.label || `Лист ${i + 1}`).trim(),
    }));
  if (!sheetSources.length && (p.urls || []).length) {
    sheetSources = p.urls.map((url, i) => ({ id: `src_${i}`, url, label: `Лист ${i + 1}` }));
  }
  sheetSources = dedupeSources(sheetSources);
  const monthlySheets = [];
  const seenM = new Set();
  for (const ms of p.monthlySheets || []) {
    if (!ms?.month || !ms?.url || seenM.has(ms.month)) continue;
    seenM.add(ms.month);
    monthlySheets.push({ month: ms.month, url: ms.url });
  }
  return { ...p, sheetSources, monthlySheets };
}

async function loadSourceCsv(url, env) {
  const u = String(url || '');
  if (u.includes('planto-daily') || u === 'data/planto-daily.csv') {
    return fetchText(rawFeed(env, 'data/planto-daily.csv'));
  }
  if (u.includes('hupp-daily') || u === 'data/hupp-daily.csv') {
    return fetchText(rawFeed(env, 'data/hupp-daily.csv'));
  }
  if (u.startsWith('data/')) return fetchText(rawFeed(env, u));
  if (u.includes('docs.google.com')) {
    const variants = [sheetExportUrl(u), u];
    let last;
    for (const v of variants) {
      try { return await fetchText(v); } catch (e) { last = e; }
    }
    throw last || new Error('sheet fetch failed');
  }
  return fetchText(u);
}

function monthLabel(mk) {
  const [y, m] = String(mk).split('-');
  const names = ['', 'Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];
  return `${names[+m] || m} ${y}`;
}

/** Normalize labels so «Лист1» ≡ «Лист 1». */
function labelKey(label) {
  return String(label || '').trim().toLowerCase().replace(/\s+/g, '');
}

function isJggl(proj) {
  return /jggl|джигл/i.test(proj.id || '') || /jggl|джигл/i.test(proj.name || '');
}

function isQlosophy(proj) {
  return /qlosophy|клософ/i.test(proj.id || '') || /qlosophy|клософ/i.test(proj.name || '');
}

function isMultiSheetCsv(proj) {
  return isJggl(proj) || isQlosophy(proj);
}

function isPlatformLabel(label) {
  return /^(ios|android)$/i.test(String(label || '').trim());
}

function jgglPlatformFromName(name) {
  return /android/i.test(String(name || '')) ? 'Android' : 'iOS';
}

/** Redistribute JGGL orphan sheets («Июл 2026», App, raw CSV) into iOS/Android. */
function normalizeJgglSources(proj, sources) {
  if (!isJggl(proj)) return sources || [];
  const list = (sources || []).filter(s => !/^app$/i.test(String(s.label || '').trim()));
  const stable = [];
  const orphans = [];
  for (const s of list) {
    const label = String(s.label || '').trim();
    if (isPlatformLabel(label) || /^(waitlist|redirect|web)$/i.test(label)) stable.push(s);
    else orphans.push(s);
  }
  if (!orphans.length) return list;

  const by = new Map(stable.map(s => [labelKey(s.label), { ...s, rows: [...(s.rows || [])] }]));
  for (const src of orphans) {
    const label = String(src.label || '').trim();
    if (/^(waitlist|redirect|web)$/i.test(label)) {
      const k = labelKey(label);
      const prev = by.get(k);
      by.set(k, prev
        ? { ...prev, rows: aggregateByDate([...(prev.rows || []), ...(src.rows || [])]) }
        : { ...src, id: `upload_${k}`, label });
      continue;
    }
    if (isPlatformLabel(label)) {
      const plat = /^android$/i.test(label) ? 'Android' : 'iOS';
      const k = labelKey(plat);
      const prev = by.get(k);
      by.set(k, prev
        ? { ...prev, rows: aggregateByDate([...(prev.rows || []), ...(src.rows || [])]) }
        : { ...src, id: `upload_${k}`, label: plat });
      continue;
    }
    const buckets = { iOS: [], Android: [] };
    for (const r of src.rows || []) {
      const plat = jgglPlatformFromName(r.campaign || '');
      buckets[plat].push(r);
    }
    for (const plat of ['iOS', 'Android']) {
      if (!buckets[plat].length) continue;
      const k = labelKey(plat);
      const prev = by.get(k);
      const rows = aggregateByDate([...(prev?.rows || []), ...buckets[plat]]);
      by.set(k, prev
        ? { ...prev, rows }
        : { id: `upload_${k}`, label: plat, url: `upload://${proj.id}/${k}`, rows, uploaded: true });
    }
  }

  const order = ['ios', 'android', 'waitlist', 'redirect', 'web'];
  return [...by.values()].sort((a, b) => {
    const ka = labelKey(a.label);
    const kb = labelKey(b.label);
    const ia = order.indexOf(ka);
    const ib = order.indexOf(kb);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || ka.localeCompare(kb);
  });
}

/** Group JSONBin uploads by platform/label and merge (later file wins). */
function sourcesFromUploads(proj, uploads) {
  const bag = uploads?.[proj.id] || {};
  const entries = Object.values(bag).filter(Boolean);
  if (!entries.length) return [];

  const byLabel = new Map();
  for (const e of entries) {
    let label = (e.label || 'CSV').trim();
    if (isJggl(proj) && /^app$/i.test(label)) continue;
    if (proj.id === 'hupp' || proj.type === 'hupp') label = 'Hupp';
    const k = labelKey(label);
    if (!byLabel.has(k)) byLabel.set(k, { label, entries: [] });
    byLabel.get(k).entries.push(e);
  }

  const out = [];
  for (const { label, entries: list } of byLabel.values()) {
    const rows = mergeUploadEntries(list);
    if (!rows.length) continue;
    out.push({
      id: `upload_${labelKey(label).replace(/\s+/g, '_')}`,
      label,
      url: `upload://${proj.id}/${labelKey(label)}`,
      rows,
      uploaded: true,
    });
  }
  return out;
}

function mergeSourceLists(sheetSources, uploadSources) {
  const byLabel = new Map();
  for (const s of sheetSources || []) {
    byLabel.set(labelKey(s.label), { ...s });
  }
  for (const u of uploadSources || []) {
    const k = labelKey(u.label);
    const existing = byLabel.get(k);
    if (!existing) {
      byLabel.set(k, u);
      continue;
    }
    const merged = mergeUploadEntries([
      { uploadedAt: '1970-01-01', rows: existing.rows || [] },
      { uploadedAt: '2099-01-01', rows: u.rows || [] },
    ]);
    byLabel.set(k, { ...existing, rows: merged, uploaded: true });
  }
  return [...byLabel.values()].filter(s => (s.rows || []).length);
}

async function loadProjectPack(proj, env, uploads) {
  const p = normalizeProj(proj);
  const uploadSources = sourcesFromUploads(p, uploads);

  if (p.id === 'hupp' || p.type === 'hupp') {
    let rows = uploadSources[0]?.rows || [];
    try {
      const feed = parseSheetRows(await loadSourceCsv('data/hupp-daily.csv', env));
      const by = new Map(rows.map(r => [r.iso || r.dateStr, { ...r }]));
      for (const f of feed) {
        const key = f.iso || f.dateStr;
        const ex = by.get(key) || { ...f, spend: 0 };
        for (const k of ['purchase', 'purchases', 'installs', 'trials', 'clicks', 'impressions', 'contact_info', 'form_submit', 'contact_sent']) {
          if (f[k] && !ex[k]) ex[k] = f[k];
        }
        if (!ex.spend && f.spend) ex.spend = f.spend;
        by.set(key, ex);
      }
      rows = [...by.values()].sort((a, b) => (a.iso || '').localeCompare(b.iso || ''));
    } catch { /* feed optional */ }
    return {
      sources: [{ id: 'hupp', label: 'Hupp', url: 'upload://hupp', rows, uploaded: true }],
    };
  }

  const monthly = p.monthlySheets || [];
  const defs = (p.sheetSources || []).filter(s => {
    if (isJggl(p) && /^app$/i.test(s.label || '')) return false;
    if (String(s.url || '').startsWith('data/jggl/')) return false;
    return !!s.url;
  });

  // Monthly partitions: StreamFi/Quadcode multi-sheet overlap, OR single source + monthlySheets (Qlosophy).
  const monthlyIds = new Set(monthly.map(ms => (sourceUrlKey(ms.url) || '').split('::')[0]).filter(Boolean));
  const sourceIds = new Set(defs.map(s => (sourceUrlKey(s.url) || '').split('::')[0]).filter(Boolean));
  let overlap = 0;
  monthlyIds.forEach(id => { if (sourceIds.has(id)) overlap += 1; });
  const useMonthlyOnly = monthly.length && (
    (defs.length <= 1)
    || (!isJggl(p) && defs.length > 1 && overlap >= Math.min(monthlyIds.size, sourceIds.size))
  );

  const jobs = useMonthlyOnly
    ? monthly.map(ms => ({ id: `month_${ms.month}`, label: monthLabel(ms.month), url: ms.url }))
    : (defs.length ? defs : [{ id: 'main', label: p.name, url: (p.urls || [])[0] }]).filter(s => s.url && !String(s.url).startsWith('upload://'));

  const sheetSources = [];
  const results = await Promise.allSettled(jobs.map(async (j) => {
    const text = await loadSourceCsv(j.url, env);
    return {
      id: j.id,
      label: j.label,
      url: j.url,
      rows: aggregateByDate(parseSheetRows(text)),
    };
  }));

  results.forEach((r, i) => {
    if (r.status === 'fulfilled' && r.value.rows?.length) sheetSources.push(r.value);
    else if (r.status === 'rejected') console.log('source fail', jobs[i]?.label, r.reason?.message || r.reason);
  });

  let sources = mergeSourceLists(sheetSources, uploadSources);
  if (!sources.length && uploadSources.length) sources = uploadSources;
  sources = normalizeJgglSources(p, sources);

  let meta = null;
  if (p.id === 'planto' || p.type === 'planto') {
    try {
      meta = JSON.parse(await fetchText(rawFeed(env, 'data/planto-meta.json')));
    } catch { /* optional */ }
  }

  return { sources: dedupeSources(sources), meta };
}

let cache = { at: 0, projects: null, packs: {}, uploads: null };

export async function getDashboardState(env, { force = false, onlyIds = null } = {}) {
  const now = Date.now();
  const want = onlyIds ? new Set(onlyIds) : null;

  if (!force && cache.projects && now - cache.at < 60 * 1000) {
    if (!want) return cache;
    const missing = [...want].filter(id => !cache.packs[id]);
    if (!missing.length) return cache;
  }

  const projects = (cache.projects && !force && now - cache.at < 60 * 1000)
    ? cache.projects
    : (await loadProjects(env)).map(normalizeProj);

  const uploads = (cache.uploads && !force && now - cache.at < 60 * 1000)
    ? cache.uploads
    : await loadCsvUploads(env);

  const packs = { ...(cache.packs || {}) };
  const toLoad = want
    ? projects.filter(p => want.has(p.id) && (force || !packs[p.id]))
    : projects;

  // Sequential to stay under Cloudflare subrequest limits
  for (const p of toLoad) {
    try {
      packs[p.id] = await loadProjectPack(p, env, uploads);
    } catch (e) {
      packs[p.id] = { sources: [], error: e.message || String(e) };
    }
  }

  cache = { at: now, projects, packs, uploads };
  return cache;
}

/** Load projects list + only packs needed for the question (avoids CF subrequest limits). */
export async function getStateForQuestion(env, q) {
  const nq = String(q || '').toLowerCase().replace(/ё/g, 'е');
  if (/по всем|всех проект|общий спенд|сводк|\/digest|\/report\s*$/.test(nq)) {
    return getDashboardState(env);
  }

  const projects = (cache.projects && Date.now() - cache.at < 60 * 1000)
    ? cache.projects
    : (await loadProjects(env)).map(normalizeProj);

  const ids = [];
  const push = (pred) => {
    for (const p of projects) {
      if (pred(p) && !ids.includes(p.id)) ids.push(p.id);
    }
  };
  if (/плант|planto|plant/.test(nq)) push(p => p.id === 'planto' || /planto/i.test(p.name || ''));
  if (/джигл|jggl|jiggle/.test(nq)) push(p => /jggl/i.test(p.id || '') || /jggl/i.test(p.name || ''));
  if (/хапп|hupp/.test(nq)) push(p => p.id === 'hupp' || /hupp/i.test(p.name || ''));
  if (/стрим|streamfi|stream/.test(nq)) push(p => /stream/i.test(p.id || '') || /stream/i.test(p.name || ''));
  if (/квад|quadcode|quad/.test(nq)) push(p => /quad/i.test(p.id || '') || /quad/i.test(p.name || ''));
  if (/клософ|qlosophy/.test(nq)) push(p => /qlosophy|клософ/i.test(p.id || '') || /qlosophy|клософ/i.test(p.name || ''));
  if (/рамбл|rumble/.test(nq)) push(p => /rumble/i.test(p.id || '') || /rumble/i.test(p.name || ''));

  if (!ids.length) return getDashboardState(env);
  // Ensure projects list is in cache for QA project finder
  if (!cache.projects) cache = { ...cache, projects, at: cache.at || Date.now() };
  return getDashboardState(env, { onlyIds: ids });
}

export function allRows(pack) {
  const sources = dedupeSources(pack?.sources || []);
  const flat = sources.flatMap(s => (s.rows || []).map(r => hydrateStoredRow(r) || r));
  return flat.sort((a, b) => (a.iso || '').localeCompare(b.iso || ''));
}

function isMonthSource(s) {
  return String(s?.id || '').startsWith('month_')
    || /\b20\d{2}\b/.test(String(s?.label || ''));
}

function hydrateSourceRows(sources) {
  return (sources || []).flatMap(s => (s.rows || []).map(r => hydrateStoredRow(r) || r));
}

/**
 * Month sheets are authoritative for dates they cover.
 * Upload / «Лист 1» fills calendar gaps only — no double-count on overlap.
 */
export function mergeMonthAndGapRows(sources) {
  const list = dedupeSources(sources || []);
  if (!list.length) return [];
  const months = list.filter(isMonthSource);
  const others = list.filter(s => !isMonthSource(s));
  if (!months.length) return hydrateSourceRows(list);
  const primary = hydrateSourceRows(months);
  const covered = new Set(primary.map(r => r.iso || rowIso(r)).filter(Boolean));
  const gaps = hydrateSourceRows(others).filter(r => {
    const iso = r.iso || rowIso(r);
    return iso && !covered.has(iso);
  });
  return [...primary, ...gaps].sort((a, b) => (a.iso || '').localeCompare(b.iso || ''));
}

/**
 * Default rows for a project (matches dashboard selectProjectRows):
 * - JGGL: sum iOS + Android only (not Waitlist/Redirect)
 * - Month sheets + upload gaps by date (Qlosophy / Quadcode / any leftover «Лист 1»)
 * - otherwise: all sources
 */
export function projectRows(proj, pack) {
  const sources = dedupeSources(pack?.sources || []);
  if (!sources.length) return [];
  if (sources.length === 1) {
    return (sources[0].rows || []).map(r => hydrateStoredRow(r) || r);
  }
  if (isJggl(proj)) {
    const plats = sources.filter(s => isPlatformLabel(s.label));
    if (plats.length) {
      return plats.flatMap(s => (s.rows || []).map(r => hydrateStoredRow(r) || r))
        .sort((a, b) => (a.iso || '').localeCompare(b.iso || ''));
    }
  }
  const months = sources.filter(isMonthSource);
  const others = sources.filter(s => !isMonthSource(s));
  if (months.length && others.length) return mergeMonthAndGapRows(sources);
  if (months.length) {
    return months.flatMap(s => (s.rows || []).map(r => hydrateStoredRow(r) || r))
      .sort((a, b) => (a.iso || '').localeCompare(b.iso || ''));
  }
  if (isQlosophy(proj) || isMultiSheetCsv(proj)) {
    const primary = sources.find(s => {
      const k = labelKey(s.label);
      return k === 'main' || k === 'лист1' || k === 'sheet1';
    }) || sources[0];
    return (primary.rows || []).map(r => hydrateStoredRow(r) || r);
  }
  return allRows(pack);
}

export { sum, rowIso, aggregateByDate, dedupeSources };
