/** Report / digest text builders. */

import { sum, rowIso, projectRows } from './data.js';
import { findProjectByName } from './qa.js';

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
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function addDaysIso(iso, n) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d + n);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

function filterIso(rows, start, end) {
  return (rows || []).filter(r => {
    const iso = rowIso(r);
    return iso && iso >= start && iso <= end;
  });
}

function projectBlock(proj, pack, { yesterday, d7 }) {
  const cur = proj.currency || '₽';
  const rows = projectRows(proj, pack);
  const yRows = filterIso(rows, yesterday, yesterday);
  const wRows = filterIso(rows, d7, yesterday);
  const allSpend = sum(rows, 'spend');
  const ySpend = sum(yRows, 'spend');
  const wSpend = sum(wRows, 'spend');
  const lines = [
    `*${escapeMd(proj.name)}*`,
    `вчера: ${fmtMoney(ySpend, cur)} · 7д: ${fmtMoney(wSpend, cur)} · всего: ${fmtMoney(allSpend, cur)}`,
  ];
  const cl = sum(wRows, 'clicks');
  const im = sum(wRows, 'impressions');
  if (cl || im) lines.push(`7д: клики ${fmtInt(cl)} · показы ${fmtInt(im)}`);
  if (proj.budgetRemainder != null && Number.isFinite(+proj.budgetRemainder)) {
    lines.push(`остаток бюджета: ${fmtMoney(+proj.budgetRemainder, cur)}`);
  }
  const ue = pack?.meta?.unit_economics;
  if (ue?.revenue_total != null) {
    lines.push(`доход: ${fmtMoney(ue.revenue_total, cur)}${ue.roas_d7 != null ? ` · ROAS D7 ${ue.roas_d7}%` : ''}`);
  }
  if (pack?.error) lines.push(`⚠ ${pack.error}`);
  return lines.join('\n');
}

function escapeMd(s) {
  return String(s || '').replace(/([_*`\[])/g, '\\$1');
}

export function buildDigest(state) {
  const yesterday = addDaysIso(todayIso(), -1);
  const d7 = addDaysIso(yesterday, -6);
  const blocks = [`📊 *Дайджест Elixir*\n${yesterday} · 7 дней`];
  for (const p of state.projects || []) {
    if (getStatus(p) === 'stop') continue;
    const pack = state.packs[p.id];
    if (!pack) continue;
    blocks.push(projectBlock(p, pack, { yesterday, d7 }));
  }
  return blocks.join('\n\n');
}

function getStatus(p) {
  return String(p.status || 'run').toLowerCase();
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
    `📋 *Отчёт · ${escapeMd(proj.name)}*`,
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
