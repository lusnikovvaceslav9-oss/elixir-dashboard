/** Daily digest / report text builders. */

import { sum, rowIso, projectRows } from './data.js';
import { findProjectByName } from './qa.js';

const DIGEST_TZ = 'Asia/Bangkok';

function fmtMoney(n, cur) {
  if (!n && n !== 0) return '—';
  const abs = Math.abs(n);
  const s = abs >= 1000 ? abs.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') : abs.toFixed(2);
  return cur === '$' ? '$' + s : s + ' ' + cur;
}

function fmtInt(n) {
  return Math.round(n || 0).toLocaleString('ru-RU');
}

function todayIso() {
  // Daily digests follow Bangkok calendar (UTC+7), not Worker UTC.
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: DIGEST_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

function addDaysIso(iso, n) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + n));
  return `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, '0')}-${String(dt.getUTCDate()).padStart(2, '0')}`;
}

function formatDayRu(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  const months = [
    '', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
  ];
  return `${d} ${months[m] || m} ${y}`;
}

function filterIso(rows, start, end) {
  return (rows || []).filter(r => {
    const iso = rowIso(r);
    return iso && iso >= start && iso <= end;
  });
}

function escapeMd(s) {
  return String(s || '').replace(/([_*`\[])/g, '\\$1');
}

function getStatus(p) {
  return String(p.status || 'run').toLowerCase();
}

function isJggl(proj) {
  return /jggl|джигл/i.test(proj.id || '') || /jggl|джигл/i.test(proj.name || '');
}

function isQlosophy(proj) {
  return /qlosophy|клософ/i.test(proj.id || '') || /qlosophy|клософ/i.test(proj.name || '');
}

function isQuadcode(proj) {
  return /quadcode|qvadkod|квадкод/i.test(proj.id || '') || /quadcode|qvadkod|квадкод/i.test(proj.name || '');
}

function isHupp(proj) {
  return /^hupp$/i.test(proj.id || '') || /^hupp$/i.test(proj.name || '');
}

function isPlanto(proj) {
  return /^planto$/i.test(proj.id || '') || /planto|планто/i.test(proj.name || '');
}

/** REG in campaign sheets is often qregs; prefer fuller signal. */
function regCount(rows) {
  const q = sum(rows, 'qregs') || 0;
  const r = sum(rows, 'regs') || 0;
  return q > r ? q : (r || q);
}

/** JGGL: installs column, else Results→qregs. */
function installCount(rows) {
  const inst = sum(rows, 'installs');
  if (inst) return inst;
  return regCount(rows);
}

function purchaseCount(rows) {
  return sum(rows, 'purchases') || sum(rows, 'purchase') || 0;
}

/**
 * Live budget remainder from snapshot:
 * budgetTotal = spentAtInput + remainderEntered → remainderNow = total − currentSpend.
 * Falls back to metaBudget + manual delta when present.
 */
export function computeLiveBudgetRemainder(proj, pack) {
  if (!proj) return null;
  const cur = proj.currency || '₽';

  const mb = proj.metaBudget;
  if (mb && mb.remainder != null && Number.isFinite(Number(mb.remainder))
    && mb.cap != null && Number.isFinite(Number(mb.cap)) && Number(mb.cap) > 0) {
    const delta = Number(proj.manualBudget?.remainderDelta) || 0;
    return {
      remainder: Number(mb.remainder) + delta,
      total: Number(mb.cap) + (Number(proj.manualBudget?.capDelta) || 0),
      spent: Number(mb.spentInCap) || 0,
      currency: cur,
      source: 'meta',
    };
  }

  const remEntered = Number(proj.budgetRemainder);
  if (!Number.isFinite(remEntered)) return null;
  const spentAt = Number(proj.budgetRemainderSpentAt);
  const total = Number(proj.budgetTotal);
  const rows = pack ? projectRows(proj, pack) : [];
  const spent = sum(rows, 'spend');
  if (Number.isFinite(total) && Number.isFinite(spentAt) && total > 0) {
    return { remainder: total - spent, total, spent, currency: cur, source: 'manual' };
  }
  return { remainder: remEntered, total: null, spent, currency: cur, source: 'manual_raw' };
}

/**
 * Digest project kinds. Quadcode intentionally omitted for now.
 * Returns null → skip project in daily digest.
 */
function digestKind(proj) {
  if (isQuadcode(proj)) return null;
  if (isJggl(proj)) return 'jggl';
  if (isQlosophy(proj)) return 'qlosophy';
  if (isHupp(proj)) return 'hupp';
  if (isPlanto(proj)) return 'planto';
  return null;
}

function formatProjectDay(proj, yRows, pack) {
  const cur = proj.currency || '₽';
  const spend = sum(yRows, 'spend');
  if (!(spend > 0)) return null;

  const kind = digestKind(proj);
  if (!kind) return null;

  const name = escapeMd(proj.name || proj.id);
  let line2 = '';

  if (kind === 'jggl') {
    const installs = installCount(yRows);
    const cpi = installs > 0 ? spend / installs : null;
    line2 = `Spend ${fmtMoney(spend, cur)} · installs ${fmtInt(installs)} · CPI ${cpi != null ? fmtMoney(cpi, cur) : '—'}`;
  } else if (kind === 'qlosophy') {
    const regs = regCount(yRows);
    const cpr = regs > 0 ? spend / regs : null;
    line2 = `Spend ${fmtMoney(spend, cur)} · REG ${fmtInt(regs)} · CPR ${cpr != null ? fmtMoney(cpr, cur) : '—'}`;
  } else if (kind === 'hupp') {
    const pur = purchaseCount(yRows);
    const cpp = pur > 0 ? spend / pur : null;
    line2 = `Spend ${fmtMoney(spend, cur)} · purchases ${fmtInt(pur)} · CPP ${cpp != null ? fmtMoney(cpp, cur) : '—'}`;
  } else if (kind === 'planto') {
    const trials = sum(yRows, 'trials') || 0;
    const cpt = trials > 0 ? spend / trials : null;
    line2 = `Spend ${fmtMoney(spend, cur)} · trials ${fmtInt(trials)} · CPT ${cpt != null ? fmtMoney(cpt, cur) : '—'}`;
    const budget = computeLiveBudgetRemainder(proj, pack);
    if (budget && Number.isFinite(budget.remainder)) {
      const rem = budget.remainder;
      line2 += rem < 0
        ? ` · остаток −${fmtMoney(Math.abs(rem), cur)} (перерасход)`
        : ` · остаток ${fmtMoney(rem, cur)}`;
    }
  }

  return {
    text: `*${name}*\n${line2}`,
    spend,
    currency: cur,
  };
}

export function buildDigest(state) {
  const yesterday = addDaysIso(todayIso(), -1);
  const blocks = [];
  let totalRub = 0;
  let totalUsd = 0;

  for (const p of state.projects || []) {
    if (getStatus(p) === 'stop') continue;
    if (!digestKind(p)) continue;
    const pack = state.packs[p.id];
    if (!pack) continue;
    const rows = projectRows(p, pack);
    const yRows = filterIso(rows, yesterday, yesterday);
    const block = formatProjectDay(p, yRows, pack);
    if (!block) continue;
    blocks.push(block.text);
    if (block.currency === '$') totalUsd += block.spend;
    else totalRub += block.spend;
  }

  const head = `Elixir · вчера · ${formatDayRu(yesterday)}`;
  if (!blocks.length) {
    return `${head}\n\nВчера спенда не было.`;
  }

  const totals = [];
  if (totalUsd) totals.push(fmtMoney(totalUsd, '$'));
  if (totalRub) totals.push(fmtMoney(totalRub, '₽'));

  return [head, '', ...blocks.flatMap((b, i) => (i ? ['', b] : [b])), '', `Итого: ${totals.join(' + ')}`].join('\n');
}

/** Projects whose live remainder just hit ≤ 0 and need an admin alert.
 *  Сейчас следим за Planto (ручной снимок бюджета в дашборде). */
export function collectBudgetExhaustedAlerts(state) {
  const alerts = [];
  const clears = [];
  for (const p of state.projects || []) {
    if (getStatus(p) === 'stop') continue;
    if (!isPlanto(p)) continue;
    const pack = state.packs[p.id];
    if (!pack) continue;
    const budget = computeLiveBudgetRemainder(p, pack);
    if (!budget || !Number.isFinite(budget.remainder)) continue;

    const already = !!(p.budgetAlert?.exhausted && p.budgetAlert?.sentAt);
    if (budget.remainder <= 0) {
      if (!already) {
        alerts.push({
          projectId: p.id,
          name: p.name || p.id,
          remainder: budget.remainder,
          currency: budget.currency,
          total: budget.total,
          spent: budget.spent,
        });
      }
    } else if (already) {
      clears.push(p.id);
    }
  }
  return { alerts, clears };
}

export function formatBudgetExhaustedMessage(alert) {
  const cur = alert.currency || '₽';
  const rem = alert.remainder;
  const remTxt = rem < 0
    ? `перерасход ${fmtMoney(Math.abs(rem), cur)}`
    : `остаток ${fmtMoney(0, cur)}`;
  return [
    `⚠ *Бюджет закончился*`,
    `*${escapeMd(alert.name)}* · ${remTxt}`,
    alert.total != null ? `Лимит ${fmtMoney(alert.total, cur)} · потрачено ${fmtMoney(alert.spent, cur)}` : null,
  ].filter(Boolean).join('\n');
}

/** Legacy-style block kept for /report <project>. */
function projectBlock(proj, pack, { yesterday, d7 }) {
  const cur = proj.currency || '₽';
  const rows = projectRows(proj, pack);
  const yRows = filterIso(rows, yesterday, yesterday);
  const wRows = filterIso(rows, d7, yesterday);
  const day = formatProjectDay(proj, yRows, pack);
  if (day) return day.text;

  const ySpend = sum(yRows, 'spend');
  const wSpend = sum(wRows, 'spend');
  return [
    `*${escapeMd(proj.name)}*`,
    `вчера: ${fmtMoney(ySpend, cur)} · 7д: ${fmtMoney(wSpend, cur)}`,
  ].join('\n');
}

export function buildReport(state, arg = '') {
  const yesterday = addDaysIso(todayIso(), -1);
  const d7 = addDaysIso(yesterday, -6);
  const q = String(arg || '').trim();
  if (!q || /всех|все|all/i.test(q)) return buildDigest(state);

  const proj = findProjectByName(q, state.projects);
  if (!proj) {
    return `Проект не найден: ${q}\nДоступны: ${(state.projects || []).map(p => p.name).join(', ')}`;
  }
  const pack = state.packs[proj.id];
  if (!pack) return `Нет данных по ${proj.name}`;
  const cur = proj.currency || '₽';
  const rows = projectRows(proj, pack);
  const lines = [
    `Отчёт · ${escapeMd(proj.name)}`,
    projectBlock(proj, pack, { yesterday, d7 }),
  ];
  if (pack.sources?.length > 1) {
    lines.push('\nЛисты:');
    for (const s of pack.sources) {
      lines.push(`· ${s.label}: ${fmtMoney(sum(s.rows || [], 'spend'), cur)} (${(s.rows || []).length} дн.)`);
    }
  }
  const isos = rows.map(rowIso).filter(Boolean).sort();
  if (isos.length) lines.push(`\nДанные: ${isos[0]} → ${isos[isos.length - 1]}`);
  return lines.join('\n');
}
