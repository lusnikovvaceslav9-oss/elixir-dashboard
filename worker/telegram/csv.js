/** Minimal CSV + daily-row helpers for the Telegram bot (Worker). */

export function detectDelimiter(text) {
  const lines = String(text || '').replace(/^\uFEFF/, '').split(/\r?\n/).filter(l => l.trim()).slice(0, 12);
  if (!lines.length) return ',';
  const score = (d) => {
    let best = 0;
    lines.forEach(line => {
      let n = 0, inQ = false;
      for (const c of line) {
        if (c === '"') { inQ = !inQ; continue; }
        if (!inQ && c === d) n += 1;
      }
      if (n > best) best = n;
    });
    return best;
  };
  return [[',', score(',')], [';', score(';')], ['\t', score('\t')]].sort((a, b) => b[1] - a[1])[0][0];
}

export function parseCSV(text) {
  const delim = detectDelimiter(text);
  const rows = [];
  let row = [], cur = '', inQ = false;
  const src = String(text || '').replace(/^\uFEFF/, '');
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (c === '"') {
      if (inQ && src[i + 1] === '"') { cur += '"'; i += 1; }
      else inQ = !inQ;
      continue;
    }
    if (!inQ && c === delim) { row.push(cur); cur = ''; continue; }
    if (!inQ && (c === '\n' || c === '\r')) {
      if (c === '\r' && src[i + 1] === '\n') i += 1;
      row.push(cur); cur = '';
      if (row.some(x => String(x).trim())) rows.push(row);
      row = [];
      continue;
    }
    cur += c;
  }
  if (cur.length || row.length) { row.push(cur); if (row.some(x => String(x).trim())) rows.push(row); }
  return rows;
}

/** Parse date → { y, m, d, iso, dateStr, date } without timezone drift. */
export function parseDateParts(str) {
  if (!str) return null;
  str = String(str).trim();
  let y, m, d;
  let match = str.match(/^(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})/);
  if (match) {
    y = +match[1]; m = +match[2]; d = +match[3];
  } else {
    match = str.match(/^(\d{1,2})[\/\.](\d{1,2})[\/\.](\d{4})/);
    if (!match) return null;
    const a = +match[1], b = +match[2];
    y = +match[3];
    d = a > 12 ? a : b > 12 ? b : a;
    m = a > 12 ? b : b > 12 ? a : b;
  }
  if (!y || m < 1 || m > 12 || d < 1 || d > 31) return null;
  const iso = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  const dateStr = `${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')}.${y}`;
  // UTC noon avoids DST edge cases when sorting
  const date = new Date(Date.UTC(y, m - 1, d, 12, 0, 0));
  return { y, m, d, iso, dateStr, date };
}

function num(v) {
  if (v == null || v === '') return 0;
  const n = Number(String(v).replace(/\s/g, '').replace(',', '.').replace(/[^\d.\-]/g, ''));
  return Number.isFinite(n) ? n : 0;
}

function findCol(headers, needles, exclude = []) {
  const hs = headers.map(h => String(h || '').toLowerCase());
  for (let i = 0; i < hs.length; i++) {
    const h = hs[i];
    if (exclude.some(e => h.includes(e))) continue;
    if (needles.some(n => h.includes(n))) return i;
  }
  return -1;
}

/** Map spreadsheet/CSV rows → daily metrics. */
export function parseSheetRows(text) {
  const table = parseCSV(text);
  if (table.length < 2) return [];
  const headers = table[0];
  const di = findCol(headers, ['дата', 'date', 'день', 'reporting starts', 'reporting'], ['daypart', 'ends']);
  if (di < 0) return [];
  const si = findCol(headers, ['spend', 'спенд', 'расход', 'cost', 'затрат', 'amount spent'], ['cpi', 'cpc', 'cpm', 'install', 'trial', 'per result', 'per link', 'per landing']);
  const cli = findCol(headers, ['link clicks', 'click', 'клик'], ['all)', 'ctr', 'cpc']);
  const impi = findCol(headers, ['impression', 'показ', 'impr']);
  const ini = findCol(headers, ['install', 'инстал', 'установ'], ['cost', 'цена', 'campaign']);
  // Meta Ads: Results = mobile app installs (stored as qregs in dashboard)
  const resi = findCol(headers, ['results', 'результат', 'qreg', 'целев', 'qual', 'mobile app install'], ['indicator', 'cost', 'цена', 'per']);
  const tri = findCol(headers, ['trial', 'триал']);
  const soli = findCol(headers, ['sold', 'продан']);
  const fbi = findCol(headers, ['fb', 'bill', 'бил']);
  const puri = findCol(headers, ['purchase', 'покуп']);
  const regi = findCol(headers, ['reg', 'регистр'], ['qreg', 'цел']);
  const qri = resi >= 0 ? resi : findCol(headers, ['qreg', 'целев', 'qual']);
  const camp = findCol(headers, ['campaign', 'кампани']);

  const out = [];
  for (let r = 1; r < table.length; r++) {
    const row = table[r];
    const parts = parseDateParts(row[di]);
    if (!parts) continue;
    const installsRaw = ini >= 0 ? num(row[ini]) : 0;
    const qregs = qri >= 0 ? num(row[qri]) : 0;
    const installs = installsRaw || qregs;
    out.push({
      date: parts.date,
      dateStr: parts.dateStr,
      iso: parts.iso,
      spend: si >= 0 ? num(row[si]) : 0,
      clicks: cli >= 0 ? num(row[cli]) : 0,
      impressions: impi >= 0 ? num(row[impi]) : 0,
      installs,
      trials: tri >= 0 ? num(row[tri]) : 0,
      sold: soli >= 0 ? num(row[soli]) : 0,
      fb: fbi >= 0 ? num(row[fbi]) : 0,
      purchase: puri >= 0 ? num(row[puri]) : 0,
      purchases: puri >= 0 ? num(row[puri]) : 0,
      regs: regi >= 0 ? num(row[regi]) : 0,
      qregs,
      campaign: camp >= 0 ? String(row[camp] || '') : '',
    });
  }
  return out.sort((a, b) => (a.iso || '').localeCompare(b.iso || ''));
}

/** Normalize a stored upload row (JSONBin) into a daily row. */
export function hydrateStoredRow(r) {
  if (!r) return null;
  let parts = null;
  if (r.iso) parts = parseDateParts(r.iso);
  if (!parts && r.dateStr) parts = parseDateParts(r.dateStr);
  if (!parts && r.date) parts = parseDateParts(String(r.date).slice(0, 10));
  if (!parts) return null;
  return {
    ...r,
    date: parts.date,
    dateStr: parts.dateStr,
    iso: parts.iso,
    spend: num(r.spend),
    clicks: num(r.clicks),
    impressions: num(r.impressions),
    installs: num(r.installs) || num(r.qregs),
    trials: num(r.trials),
    sold: num(r.sold),
    fb: num(r.fb),
    purchase: num(r.purchase || r.purchases),
    purchases: num(r.purchases || r.purchase),
    regs: num(r.regs),
    qregs: num(r.qregs) || num(r.installs),
    campaign: r.campaign || '',
  };
}

export function aggregateByDate(rows) {
  const map = new Map();
  for (const r of rows || []) {
    const key = r.iso || r.dateStr;
    if (!key) continue;
    if (!map.has(key)) {
      map.set(key, { ...r });
      continue;
    }
    const ex = map.get(key);
    for (const k of Object.keys(r)) {
      if (k === 'date' || k === 'dateStr' || k === 'iso' || k === 'campaign' || k === 'adset') continue;
      if (typeof r[k] === 'number') ex[k] = (ex[k] || 0) + r[k];
    }
  }
  return [...map.values()].sort((a, b) => (a.iso || '').localeCompare(b.iso || ''));
}

export function rowIso(r) {
  if (r?.iso) return r.iso;
  if (r?.dateStr) {
    const p = parseDateParts(r.dateStr);
    return p?.iso || '';
  }
  const dt = r?.date instanceof Date ? r.date : null;
  if (!dt || isNaN(dt)) return '';
  return `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, '0')}-${String(dt.getUTCDate()).padStart(2, '0')}`;
}

export function sum(rows, key) {
  return (rows || []).reduce((a, r) => a + (Number(r[key]) || 0), 0);
}

export function sourceUrlKey(url) {
  const u = String(url || '').trim();
  if (!u) return '';
  if (u.startsWith('upload://') || u.startsWith('data/')) return u;
  const m = u.match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
  if (!m) return u;
  const gid = (u.match(/[#&?]gid=(\d+)/) || [])[1] || '0';
  return `${m[1]}::${gid}`;
}

export function dedupeSources(sources) {
  const seen = new Set();
  const out = [];
  for (const s of sources || []) {
    const key = sourceUrlKey(s.url) || `label:${String(s.label || '').toLowerCase()}` || `id:${s.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(s);
  }
  return out;
}

/** Later uploads win for the same date(+campaign). Then aggregate by day. */
export function mergeUploadEntries(entries) {
  const sorted = [...(entries || [])].sort((a, b) =>
    String(a.uploadedAt || '').localeCompare(String(b.uploadedAt || ''))
  );
  const byKey = new Map();
  for (const e of sorted) {
    let rows = [];
    if (e.text) rows = parseSheetRows(e.text);
    else if (Array.isArray(e.rows)) rows = e.rows.map(hydrateStoredRow).filter(Boolean);
    for (const r of rows) {
      const key = `${r.iso || r.dateStr}\t${r.campaign || ''}`;
      byKey.set(key, r);
    }
  }
  return aggregateByDate([...byKey.values()]);
}
