/** Rule-based Q&A over dashboard data (ported from elixir.html chat bot). */

import { sum, rowIso, allRows, projectRows } from './data.js';
import { computeLiveBudgetRemainder } from './budget.js';

// Совпадает с DIGEST_TZ в reports.js — «сегодня/вчера» должны согласовываться с /digest.
const QA_TZ = 'Asia/Bangkok';

const METRIC_ALIASES = [
  { key: 'budget', labels: [
    'остаток бюджета', 'остатки бюджета', 'остатки по бюджету', 'бюджетный остаток',
    'сколько осталось бюджета', 'осталось бюджета', 'остаток', 'остатки',
    'budget remainder', 'budget left', 'remaining budget',
  ] },
  { key: 'spend', labels: ['спенд', 'spend', 'расход', 'расхода', 'потратили', 'потрачено', 'потратил', 'затраты', 'затрат', 'сколько ушло', 'сколько слили'] },
  { key: 'impressions', labels: ['показы', 'impressions', 'imp', 'показов', 'показ'] },
  { key: 'clicks', labels: ['клики', 'clicks', 'кликов', 'клик', 'клика'] },
  // CPI before installs — «сколько стоит инстал» must not become count
  { key: 'cpi', labels: [
    'cpi', 'cost per install', 'цена инсталла', 'цена инстала', 'цена установки',
    'стоимость инсталла', 'стоимость инстала', 'стоимость установки',
    'сколько стоит инстал', 'сколько стоит инсталл', 'сколько стоит установка',
    'почем инстал', 'почём инстал', 'cost/install', 'cost install',
  ] },
  { key: 'installs', labels: ['инсталлы', 'installs', 'установки', 'установок', 'инсталлов', 'инстал'] },
  { key: 'regs', labels: ['регистрации', 'регистраций', 'регистрация', 'регистрацию', 'regs', 'registrations', 'регистры'] },
  { key: 'qregs', labels: ['целевые', 'qregs', 'qreg', 'результаты', 'results'] },
  { key: 'trials', labels: ['триалы', 'trials', 'триал', 'триалов'] },
  { key: 'purchase', labels: ['purchase', 'покупки', 'purchases', 'покупок'] },
  { key: 'cpr', labels: ['цена регистрации', 'стоимость регистрации', 'cost per reg', 'cost/reg', 'цена рег'] },
  { key: 'cpc', labels: ['cpc', 'спс', 'цена клика', 'стоимость клика'] },
  { key: 'cpm', labels: ['cpm', 'спм', 'цена тысячи'] },
  { key: 'ctr', labels: ['ctr', 'кликабельность'] },
  { key: 'roas', labels: ['roas', 'роас'] },
  { key: 'revenue', labels: ['доход', 'выручка', 'revenue', 'заработано', 'заработок', 'заработали', 'денег', 'деньги'] },
];

const PROJECT_ALIASES = {
  planto: ['planto', 'плант', 'планта', 'планто', 'plant'],
  hupp: ['hupp', 'хапп', 'хапа', 'хаппа', 'хаппу'],
  jggl: ['jggl', 'джигл', 'джиггл', 'jiggle'],
  streamfi: ['streamfi', 'стримф', 'стримфай', 'stream', 'стрим'],
  qlosophy: ['qlosophy', 'клософ', 'qlosof'],
  quadcode: ['quadcode', 'квадкод', 'quad', 'квад'],
  rumbleads: ['rumbleads', 'rumble', 'рамбл'],
};

const MONTHS = [
  { re: /январ/i, mo: 1, name: 'январь' },
  { re: /феврал/i, mo: 2, name: 'февраль' },
  { re: /март/i, mo: 3, name: 'март' },
  { re: /апрел/i, mo: 4, name: 'апрель' },
  { re: /ма[йяе]/i, mo: 5, name: 'май' },
  { re: /июн/i, mo: 6, name: 'июнь' },
  { re: /июл/i, mo: 7, name: 'июль' },
  { re: /август/i, mo: 8, name: 'август' },
  { re: /сентябр/i, mo: 9, name: 'сентябрь' },
  { re: /октябр/i, mo: 10, name: 'октябрь' },
  { re: /ноябр/i, mo: 11, name: 'ноябрь' },
  { re: /декабр/i, mo: 12, name: 'декабрь' },
];

function norm(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/[—–−]/g, '-') // normalize dashes so «01.07–22.07» stays a range
    .replace(/[^a-zа-я0-9.$%\s\-_\/]/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function stem(s) {
  let t = norm(s);
  if (t.length > 4) t = t.replace(/(ами|ями|ов|ев|ах|ях|ом|ем|ой|ей|ую|юю|ая|яя|ые|ие|ых|их|ам|ям|у|ю|а|я|е|и|ы)$/i, '');
  return t;
}

function fmtMoney(n, cur) {
  if (!n && n !== 0) return '—';
  const abs = Math.abs(n);
  const s = abs >= 1000 ? abs.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') : abs.toFixed(2);
  return cur === '$' ? '$' + s : s + ' ' + cur;
}

function fmtInt(n) {
  return Math.round(n || 0).toLocaleString('ru-RU');
}

function isoLabel(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y}`;
}

function todayParts() {
  const iso = new Intl.DateTimeFormat('en-CA', {
    timeZone: QA_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
  const [y, m, d] = iso.split('-').map(Number);
  return { y, m, d, iso };
}

function addDaysIso(iso, n) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d + n);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

function projectAliases(p) {
  const name = norm(p.name);
  const idRoot = norm(String(p.id || '').split('_')[0]);
  const out = new Set([name, idRoot, stem(name), stem(idRoot)].filter(Boolean));
  Object.entries(PROJECT_ALIASES).forEach(([key, list]) => {
    if (idRoot.includes(key) || name.includes(key) || list.some(a => name.includes(a) || idRoot.includes(a))) {
      list.forEach(a => out.add(norm(a)));
    }
  });
  return [...out].filter(a => a.length >= 3);
}

function tokenHit(nq, alias) {
  if (!alias || alias.length < 3) return false;
  if (nq.includes(alias)) return true;
  return nq.split(/\s+/).some(tok => {
    const st = stem(tok);
    return st.length >= 3 && (st === alias || alias.startsWith(st) || st.startsWith(alias));
  });
}

function findProject(q, projects) {
  const nq = norm(q);
  if (/по всем|всех проект|все проект|общий спенд|всего потрат|по кажд|сводк|по проектам|про проектам/.test(nq)) {
    return { all: true };
  }
  let best = null, bestScore = 0;
  for (const p of projects || []) {
    let score = 0;
    for (const a of projectAliases(p)) {
      if (!tokenHit(nq, a)) continue;
      score = Math.max(score, a.length + (nq.includes(a) ? 5 : 0));
    }
    if (score > bestScore) { bestScore = score; best = p; }
  }
  if (best) return { project: best };
  // Ignore generic words so «остаток бюджета про проектам» is not «unknown project»
  const stripped = nq
    .replace(/\b(остатк\w*|бюджет\w*|спенд|spend|клик\w*|показ\w*|инстал\w*|триал\w*|доход\w*|проект\w*|период\w*|сегодня|вчера|недел\w*|месяц\w*|июл\w*|июн\w*|по|у|для|про|на|в|за|из|от|с|и|или|все|всем|всех)\b/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (stripped.length >= 4 && /[a-zа-я]{4,}/i.test(stripped)) {
    return { unknownMention: true };
  }
  return null;
}

function isBudgetIntent(q) {
  const nq = norm(q);
  return /остатк|осталось бюджета|budget\s*(remainder|left|remaining)|бюджетн\w*\s*остат/.test(nq)
    || (/бюджет/.test(nq) && /остат|сколько\s+остал|остал/.test(nq));
}

function findMetric(q) {
  const nq = norm(q);
  if (isBudgetIntent(nq)) return 'budget';
  if (/\b(cpi|cost\s*per\s*install|cost\s*\/\s*install)\b/.test(nq)
    || /(сколько\s+стоит|цена|стоимость|почем|почём).{0,20}(инстал|установ)/.test(nq)
    || /(инстал|установ).{0,12}(стоит|цена|стоимость)/.test(nq)) {
    return 'cpi';
  }
  if (/(цена|стоимость|cost).{0,16}(рег|регистр)/.test(nq) || /cost\s*\/?\s*reg/.test(nq)) {
    return 'cpr';
  }
  if (/регистрац|regs?\b|registration/.test(nq)) return 'regs';
  let best = null;
  for (const m of METRIC_ALIASES) {
    for (const label of m.labels) {
      const lab = norm(label);
      if (lab.length < 2) continue;
      const hit = nq.includes(lab) || (lab.length >= 4 && nq.split(/\s+/).some(t => stem(t) === stem(lab)));
      if (hit && (!best || lab.length > best.len)) best = { key: m.key, len: lab.length };
    }
  }
  return best ? best.key : 'spend';
}

function formatBudgetLine(proj, pack) {
  const cur = proj.currency || '₽';
  const budget = computeLiveBudgetRemainder(proj, pack);
  if (!budget || !Number.isFinite(budget.remainder)) {
    return `• ${proj.name}: не задан`;
  }
  const rem = budget.remainder;
  if (rem < 0) return `• ${proj.name}: −${fmtMoney(Math.abs(rem), cur)} (перерасход)`;
  if (rem === 0) return `• ${proj.name}: ${fmtMoney(0, cur)} (закончился)`;
  return `• ${proj.name}: ${fmtMoney(rem, cur)}`;
}

function formatBudgetAnswer(projects, state, focus = null) {
  const list = focus ? [focus] : (projects || []);
  const lines = [];
  for (const p of list) {
    if (String(p.status || 'run').toLowerCase() === 'stop') continue;
    const pack = state.packs[p.id];
    if (!pack && !focus) continue;
    lines.push(formatBudgetLine(p, pack || state.packs[p.id]));
  }
  if (!lines.length) return 'Нет проектов с данными по бюджету.';
  if (focus) {
    return `остаток бюджета · ${focus.name}\n${lines[0].replace(/^•\s*/, '→ ')}`;
  }
  return `остаток бюджета · сейчас\n${lines.join('\n')}`;
}

/** REG in campaign sheets is often qregs; uploads may omit regs. Prefer the fuller signal. */
function regCount(rows) {
  const q = sum(rows, 'qregs') || 0;
  const r = sum(rows, 'regs') || 0;
  if (q > r) return q;
  return r || q;
}

/** Meta JGGL: installs in Results→qregs. Fallback to regs. */
function installCount(rows) {
  const inst = sum(rows, 'installs');
  if (inst) return inst;
  return regCount(rows);
}

function isReportIntent(q) {
  const nq = norm(q);
  // Avoid \\b with Cyrillic — JS word boundaries are ASCII-only
  return /отч[её]т|дайджест|сводк|статистик|стата|дневн|суточн|итог за|как дела|что по |\/report|\/digest/.test(nq);
}

function findSource(q, pack) {
  if (!pack?.sources?.length) return null;
  const nq = norm(q);
  for (const s of pack.sources) {
    const label = norm(s.label || s.id || '');
    if (!label) continue;
    if (/^(ios|android|waitlist|redirect|web|hupp|app|main|лист)/i.test(label)
      || /waitlist|redirect|web/.test(label)) {
      if (nq.includes(label) || tokenHit(nq, stem(label))) return s;
    }
    if (/ios|айос|iphone|айфон/.test(nq) && /ios|айос|iphone|айфон/.test(label)) return s;
    if (/android|андроид/.test(nq) && /android|андроид/.test(label)) return s;
    if (/waitlist|вейтлист/.test(nq) && /waitlist/.test(label)) return s;
    if (/redirect|редирект/.test(nq) && /redirect/.test(label)) return s;
  }
  return null;
}

function parseRange(q) {
  const nq = norm(q);
  const today = todayParts().iso;
  const dayRange = (iso, label) => ({ start: iso, end: iso, label, kind: 'day' });

  if (/весь период|за все время|все время|с начала/.test(nq)) return null;

  if (/сегодня|за сегодня/.test(nq)) return dayRange(today, 'сегодня');
  if (/вчера|за вчера/.test(nq)) return dayRange(addDaysIso(today, -1), 'вчера');

  // «за день» / «дневной» / «суточный» → сегодня (если нет данных — later fallback to latest day)
  if (/(^|\s)(за\s+день|за\s+сутки|дневн|суточн)(\s|$)/.test(nq)
    || /отч[её]т\s+за\s+день|стата\s+за\s+день|сводк\w*\s+за\s+день/.test(nq)) {
    return dayRange(today, 'за день');
  }

  const daysM = nq.match(/за\s+(\d+)\s*дн/);
  if (daysM) {
    const n = +daysM[1];
    return { start: addDaysIso(today, -(n - 1)), end: today, label: `последние ${n} дн.`, kind: 'window' };
  }
  if (/за\s*7\s*дн|за неделю|неделю|за\s*нед/.test(nq)) {
    return { start: addDaysIso(today, -6), end: today, label: 'последние 7 дн.', kind: 'window' };
  }
  if (/за\s*30\s*дн|за месяц(?!\s*назад)/.test(nq)) {
    return { start: addDaysIso(today, -29), end: today, label: 'последние 30 дн.', kind: 'window' };
  }

  // Date range before single day (otherwise «01.07–22.07» becomes only 01.07)
  const rangeM = nq.match(/(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s*[-—–до]+\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?/);
  if (rangeM) {
    const y1 = rangeM[3] ? (+rangeM[3] < 100 ? 2000 + +rangeM[3] : +rangeM[3]) : todayParts().y;
    const y2 = rangeM[6] ? (+rangeM[6] < 100 ? 2000 + +rangeM[6] : +rangeM[6]) : y1;
    const s = `${y1}-${String(+rangeM[2]).padStart(2, '0')}-${String(+rangeM[1]).padStart(2, '0')}`;
    const e = `${y2}-${String(+rangeM[5]).padStart(2, '0')}-${String(+rangeM[4]).padStart(2, '0')}`;
    return { start: s, end: e, label: `${isoLabel(s)} — ${isoLabel(e)}`, kind: 'range' };
  }

  // single date: 22.07 / 22.07.2026 / 2026-07-22
  const oneIso = nq.match(/\b(20\d{2})-(\d{1,2})-(\d{1,2})\b/);
  if (oneIso) {
    const iso = `${oneIso[1]}-${String(+oneIso[2]).padStart(2, '0')}-${String(+oneIso[3]).padStart(2, '0')}`;
    return dayRange(iso, isoLabel(iso));
  }
  const oneRu = nq.match(/(?:^|\s)(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?:\s|$)/);
  if (oneRu) {
    const y = oneRu[3] ? (+oneRu[3] < 100 ? 2000 + +oneRu[3] : +oneRu[3]) : todayParts().y;
    const iso = `${y}-${String(+oneRu[2]).padStart(2, '0')}-${String(+oneRu[1]).padStart(2, '0')}`;
    return dayRange(iso, isoLabel(iso));
  }

  for (const m of MONTHS) {
    if (!m.re.test(nq)) continue;
    let year = todayParts().y;
    const ym = nq.match(/(20\d{2})/);
    if (ym) year = +ym[1];
    else if (m.mo > todayParts().m) year -= 1;
    const last = new Date(year, m.mo, 0).getDate();
    const s = `${year}-${String(m.mo).padStart(2, '0')}-01`;
    const e = `${year}-${String(m.mo).padStart(2, '0')}-${String(last).padStart(2, '0')}`;
    return { start: s, end: e, label: `${m.name} ${year}`, kind: 'month' };
  }
  return null;
}

function filterRows(rows, range) {
  if (!range) return rows || [];
  return (rows || []).filter(r => {
    const iso = rowIso(r);
    return iso && iso >= range.start && iso <= range.end;
  });
}

/** If day-range empty, fall back to latest available day in rows. */
function resolveRows(rows, range) {
  const filtered = filterRows(rows, range);
  if (filtered.length || !range || range.kind !== 'day') {
    return { rows: filtered, range, fallback: false };
  }
  const isos = (rows || []).map(rowIso).filter(Boolean).sort();
  if (!isos.length) return { rows: filtered, range, fallback: false };
  const latest = isos[isos.length - 1];
  return {
    rows: filterRows(rows, { start: latest, end: latest }),
    range: { start: latest, end: latest, label: `последний день · ${isoLabel(latest)}`, kind: 'day' },
    fallback: true,
  };
}

function metricValue(key, proj, rows, pack) {
  const cur = proj?.currency || '₽';
  const sp = sum(rows, 'spend');
  const cl = sum(rows, 'clicks');
  const im = sum(rows, 'impressions');
  const inst = installCount(rows);
  if (key === 'spend') return fmtMoney(sp, cur);
  if (key === 'clicks') return fmtInt(cl);
  if (key === 'impressions') return fmtInt(im);
  if (key === 'installs') return fmtInt(inst);
  if (key === 'regs') return fmtInt(regCount(rows));
  if (key === 'qregs') return fmtInt(sum(rows, 'qregs') || sum(rows, 'regs'));
  if (key === 'cpi') return inst ? fmtMoney(sp / inst, cur) : '—';
  if (key === 'cpr') {
    const rg = regCount(rows);
    return rg ? fmtMoney(sp / rg, cur) : '—';
  }
  if (key === 'trials') return fmtInt(sum(rows, 'trials'));
  if (key === 'purchase') return fmtInt(sum(rows, 'purchase') || sum(rows, 'purchases'));
  if (key === 'cpc') return cl ? fmtMoney(sp / cl, cur) : '—';
  if (key === 'cpm') return im ? fmtMoney(sp / im * 1000, cur) : '—';
  if (key === 'ctr') return im ? (cl / im * 100).toFixed(2) + '%' : '—';
  if (key === 'revenue') {
    const rev = pack?.meta?.unit_economics?.revenue_total;
    return rev != null ? fmtMoney(rev, cur) : 'нет данных о доходе';
  }
  if (key === 'roas') {
    const r = pack?.meta?.unit_economics?.roas_d7;
    return r != null ? r + '%' : '—';
  }
  if (key === 'budget') {
    const budget = computeLiveBudgetRemainder(proj, pack);
    if (!budget || !Number.isFinite(budget.remainder)) return 'не задан';
    if (budget.remainder < 0) return `−${fmtMoney(Math.abs(budget.remainder), cur)} (перерасход)`;
    return fmtMoney(budget.remainder, cur);
  }
  return '—';
}

function metricLabel(key) {
  if (key === 'cpi') return 'CPI';
  if (key === 'cpr') return 'cost/reg';
  if (key === 'regs') return 'регистрации';
  if (key === 'qregs') return 'целевые';
  if (key === 'budget') return 'остаток бюджета';
  return (METRIC_ALIASES.find(m => m.key === key)?.labels[0]) || key;
}

function formatSummary(proj, pack, rows, range, source) {
  const cur = proj.currency || '₽';
  const sp = sum(rows, 'spend');
  const cl = sum(rows, 'clicks');
  const im = sum(rows, 'impressions');
  const inst = installCount(rows);
  const regs = regCount(rows);
  const trials = sum(rows, 'trials');
  const lines = [
    `отчёт · ${proj.name}${source ? ' · ' + source.label : ''}`,
    `Период: ${range ? range.label : 'весь период'}`,
    `→ спенд ${fmtMoney(sp, cur)}`,
  ];
  const bits = [];
  if (im) bits.push(`показы ${fmtInt(im)}`);
  if (cl) bits.push(`клики ${fmtInt(cl)}`);
  if (inst) bits.push(`инсталлы ${fmtInt(inst)}`);
  if (regs && regs !== inst) bits.push(`регистрации ${fmtInt(regs)}`);
  if (trials) bits.push(`триалы ${fmtInt(trials)}`);
  if (cl) bits.push(`CPC ${fmtMoney(sp / cl, cur)}`);
  if (inst) bits.push(`CPI ${fmtMoney(sp / inst, cur)}`);
  if (regs) bits.push(`cost/reg ${fmtMoney(sp / regs, cur)}`);
  if (im) bits.push(`CPM ${fmtMoney(sp / im * 1000, cur)}`);
  if (bits.length) lines.push(bits.join(' · '));
  const ue = pack?.meta?.unit_economics;
  if (ue?.revenue_total != null) {
    lines.push(`доход ${fmtMoney(ue.revenue_total, cur)}${ue.roas_d7 != null ? ` · ROAS D7 ${ue.roas_d7}%` : ''}`);
  }
  return lines.join('\n');
}

function emptyHint(proj, pack, source, range, baseRows) {
  const all = source ? (source.rows || []) : projectRows(proj, pack);
  const isos = all.map(rowIso).filter(Boolean).sort();
  const any = allRows(pack).map(rowIso).filter(Boolean).sort();
  let hint = '';
  if (isos.length) hint = `\nДанные есть с ${isoLabel(isos[0])} по ${isoLabel(isos[isos.length - 1])}.`;
  else if (any.length) hint = `\nПо другим листам данные с ${isoLabel(any[0])} по ${isoLabel(any[any.length - 1])}.`;
  return `По ${proj.name}${source ? ' · ' + source.label : ''} за ${range ? range.label : 'период'} строк нет.${hint}`;
}

export function answerQuestion(q, state) {
  const nq = norm(q);
  if (/помощ|что умеешь|команд|пример|как спрос|\/help/.test(nq)) {
    return 'Спрашивай своими словами.\n\nМетрики: спенд, остаток бюджета, клики, показы, инсталлы, CPI/CPC/CPM, доход, ROAS.\nПериоды: сегодня, вчера, за день, 7 дней, июль, 01.07–22.07.\nОтчёты: «отчёт за день планта», «сводка джигл за июль».\n\nПримеры:\n• остаток бюджета по проектам\n• сколько осталось у планто\n• сколько потратили на планта в июле\n• сколько стоит инстал у джигла\n\nКоманды: /report, /digest, /refresh, /help';
  }

  // Скрытые от обычных пользователей проекты недоступны и через Q&A — в боте нет отдельной admin-роли.
  const projects = (state.projects || []).filter(p => !p.hiddenFromUsers);
  const target = findProject(q, projects);
  const report = isReportIntent(q);
  const metric = report ? 'spend' : findMetric(q);
  let range = parseRange(q);

  if (target?.unknownMention && metric !== 'budget') {
    return `Не распознал проект.\nДоступны: ${projects.map(p => p.name).join(', ')}`;
  }

  // Остатки бюджета — без привязки к периоду CSV
  if (metric === 'budget') {
    if (target?.project) return formatBudgetAnswer(projects, state, target.project);
    return formatBudgetAnswer(projects, state, null);
  }

  if (target?.all || (!target?.project && /по всем|всех|итого|всего|сводк/.test(nq))) {
    const lines = [];
    let totalRub = 0, totalUsd = 0;
    for (const p of projects) {
      const pack = state.packs[p.id];
      if (!pack) continue;
      const base = projectRows(p, pack);
      const { rows, range: usedRange } = resolveRows(base, range);
      if (!rows.length && metric !== 'revenue') continue;
      if (report) {
        const sp = sum(rows, 'spend');
        lines.push(`• ${p.name}: ${fmtMoney(sp, p.currency || '₽')}`);
      } else {
        const val = metricValue(metric, p, rows, pack);
        lines.push(`• ${p.name}: ${val}`);
      }
      if (metric === 'spend' || report) {
        const sp = sum(rows, 'spend');
        if ((p.currency || '₽') === '$') totalUsd += sp;
        else totalRub += sp;
      }
      if (!range && usedRange) range = usedRange;
    }
    if (!lines.length) return 'Данные ещё не загружены или период пустой.';
    const periodLabel = range ? range.label : 'весь период';
    let head = `${report ? 'отчёт' : metricLabel(metric)} · ${periodLabel}\n` + lines.join('\n');
    if (metric === 'spend' || report) {
      const parts = [];
      if (totalRub) parts.push(fmtMoney(totalRub, '₽'));
      if (totalUsd) parts.push(fmtMoney(totalUsd, '$'));
      if (parts.length) head += `\n\nИтого: ${parts.join(' + ')}`;
    }
    return head;
  }

  if (!target?.project) {
    return `Укажи проект: ${projects.map(p => p.name).slice(0, 6).join(', ')}.\nИли: спенд по всем проектам / остаток бюджета по проектам`;
  }

  const proj = target.project;
  const pack = state.packs[proj.id];
  if (!pack || pack.error) return `Нет данных по ${proj.name}${pack?.error ? ': ' + pack.error : ''}.`;

  const source = findSource(q, pack);
  const baseRows = source ? (source.rows || []) : projectRows(proj, pack);
  const { rows: filtered, range: usedRange } = resolveRows(baseRows, range);
  range = usedRange || range;

  if (!filtered.length && metric !== 'revenue' && metric !== 'budget') {
    return emptyHint(proj, pack, source, range, baseRows);
  }

  if (report) {
    return formatSummary(proj, pack, filtered, range, source);
  }

  const val = metricValue(metric, proj, filtered, pack);
  let extra = '';
  if ((metric === 'spend' || metric === 'cpi' || metric === 'installs') && filtered.length) {
    const parts = range?.kind === 'day' ? [] : [`${filtered.length} дн.`];
    if (metric === 'cpi' || metric === 'installs') {
      const inst = installCount(filtered);
      const sp = sum(filtered, 'spend');
      if (inst) parts.push(`инсталлы ${fmtInt(inst)}`);
      if (metric === 'cpi' && sp) parts.push(`спенд ${fmtMoney(sp, proj.currency || '₽')}`);
    } else {
      const im = sum(filtered, 'impressions');
      const cl = sum(filtered, 'clicks');
      if (im) parts.push(`показы ${fmtInt(im)}`);
      if (cl) parts.push(`клики ${fmtInt(cl)}`);
    }
    if (parts.length) extra = '\n' + parts.join(' · ');
  }
  return `${metricLabel(metric)} · ${proj.name}${source ? ' · ' + source.label : ''}\nПериод: ${range ? range.label : 'весь период'}\n→ ${val}${extra}`;
}

export function findProjectByName(name, projects) {
  return findProject(name, projects)?.project || null;
}

/** Exported for tests */
export const __test = {
  norm, findMetric, findProject, parseRange, isReportIntent, isBudgetIntent, installCount, filterRows, resolveRows,
};
