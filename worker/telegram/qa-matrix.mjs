/**
 * Full query matrix for Telegram QA — run locally against live JSONBin.
 *
 *   JSONBIN_MASTER_KEY=... node telegram/qa-matrix.mjs
 *
 * Exit 1 if any case fails.
 */
import { getDashboardState, projectRows, sum, rowIso } from './data.js';
import { answerQuestion, __test } from './qa.js';
import { buildDigest, buildReport } from './reports.js';

const env = {
  JSONBIN_BIN_ID: process.env.JSONBIN_BIN_ID || '6a2d1063f5f4af5e29eaccbd',
  JSONBIN_MASTER_KEY: process.env.JSONBIN_MASTER_KEY,
  GITHUB_REPO: process.env.GITHUB_REPO || 'lusnikovvaceslav9-oss/elixir-dashboard',
  GITHUB_BRANCH: process.env.GITHUB_BRANCH || 'main',
};

if (!env.JSONBIN_MASTER_KEY) {
  console.error('JSONBIN_MASTER_KEY required');
  process.exit(1);
}

const state = await getDashboardState(env, { force: true });
const today = new Date();
const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

function expect(name, ok, detail = '') {
  return { name, ok: !!ok, detail: detail || '' };
}

function ans(q) {
  return answerQuestion(q, state);
}

function has(text, ...parts) {
  const t = String(text || '').toLowerCase();
  return parts.every(p => t.includes(String(p).toLowerCase()));
}

function notHas(text, ...parts) {
  const t = String(text || '').toLowerCase();
  return parts.every(p => !t.includes(String(p).toLowerCase()));
}

function moneyLike(text) {
  return /[\d\s]+([.,]\d+)?\s*₽|\$[\d\s]+([.,]\d+)?/.test(text);
}

const cases = [];

// ── Unit: parseRange ──────────────────────────────────────────────
{
  const r = __test.parseRange('отчет за день планто');
  cases.push(expect('range: отчет за день → day', r?.kind === 'day' && r.start === todayIso, JSON.stringify(r)));
}
{
  const r = __test.parseRange('спенд сегодня планта');
  cases.push(expect('range: сегодня', r?.label === 'сегодня'));
}
{
  const r = __test.parseRange('спенд вчера планта');
  cases.push(expect('range: вчера', r?.label === 'вчера'));
}
{
  const r = __test.parseRange('спенд за июль планта');
  cases.push(expect('range: июль', r?.kind === 'month' && r.label.includes('июль')));
}
{
  const r = __test.parseRange('спенд за неделю джигл');
  cases.push(expect('range: неделя', r?.label?.includes('7')));
}
{
  const r = __test.parseRange('спенд 01.07–22.07 планта');
  cases.push(expect('range: dates', r?.kind === 'range' && r.start?.endsWith('-07-01')));
}
{
  const r = __test.parseRange('спенд за 22.07 планта');
  cases.push(expect('range: single day', r?.kind === 'day' && r.start?.endsWith('-07-22'), JSON.stringify(r)));
}
{
  const r = __test.parseRange('спенд планта');
  cases.push(expect('range: none for bare project', r === null));
}

// ── Unit: metrics ─────────────────────────────────────────────────
cases.push(expect('metric: cpi phrasing', __test.findMetric('Сколько стоит инстал у джигла') === 'cpi'));
cases.push(expect('metric: installs count', __test.findMetric('инсталлы джигл') === 'installs'));
cases.push(expect('metric: spend default', __test.findMetric('планто') === 'spend'));
cases.push(expect('metric: cpc', __test.findMetric('cpc хапп') === 'cpc'));
cases.push(expect('metric: clicks', __test.findMetric('клики планта за июль') === 'clicks'));
cases.push(expect('report intent', __test.isReportIntent('отчет за день планто')));
cases.push(expect('not report for spend', !__test.isReportIntent('спенд планта за июль')));

// ── Projects recognition ──────────────────────────────────────────
const projNames = {
  planto: ['планта', 'планто', 'planto', 'plant'],
  jggl: ['джигл', 'джигла', 'jggl', 'jiggle'],
  hupp: ['хапп', 'хаппа', 'hupp'],
};
for (const [key, aliases] of Object.entries(projNames)) {
  for (const a of aliases) {
    const hit = __test.findProject(a, state.projects);
    cases.push(expect(`project: ${a}→${key}`, hit?.project && (hit.project.id.includes(key) || __test.norm(hit.project.name).includes(key) || key === 'planto' && hit.project.id === 'planto'), hit?.project?.id));
  }
}

// ── Live answers: Planto ──────────────────────────────────────────
{
  const a = ans('планто');
  cases.push(expect('planto bare → spend all', has(a, 'спенд', 'planto') && moneyLike(a) && has(a, 'весь период')));
}
{
  const a = ans('отчет за день планто');
  cases.push(expect('planto day report', has(a, 'отчёт', 'planto') && notHas(a, 'весь период') && moneyLike(a) && (has(a, 'за день') || has(a, 'сегодня') || has(a, 'последний день')), a.slice(0, 120)));
}
{
  const a = ans('отчёт за день планта');
  cases.push(expect('planto day report ё', has(a, 'отчёт', 'planto') && notHas(a, 'весь период'), a.slice(0, 120)));
}
{
  const a = ans('спенд планта за июль');
  cases.push(expect('planto july spend', has(a, 'июль') && moneyLike(a) && !has(a, 'строк нет')));
}
{
  const a = ans('спенд сегодня планта');
  cases.push(expect('planto today', has(a, 'сегодня') || has(a, 'последний день') || has(a, 'строк нет'), a.slice(0, 120)));
}
{
  const a = ans('цена установки планта за июль');
  cases.push(expect('planto cpi july', has(a, 'cpi', 'июль') && moneyLike(a)));
}
{
  const a = ans('клики планта за июль');
  cases.push(expect('planto clicks july', has(a, 'клики', 'июль') && /\d/.test(a)));
}

// ── JGGL ──────────────────────────────────────────────────────────
{
  const a = ans('спенд джигл за июль');
  cases.push(expect('jggl july spend', has(a, 'jggl', 'июль') && moneyLike(a) && !has(a, 'строк нет') && !has(a, 'июл 2026')));
}
{
  const a = ans('Сколько стоит инстал у джигла');
  cases.push(expect('jggl cpi', has(a, 'cpi', 'jggl') && moneyLike(a) && !has(a, '→ 0\n') && !/^инсталлы/m.test(a)));
}
{
  const a = ans('инсталлы джигл');
  cases.push(expect('jggl installs >0', has(a, 'инсталлы', 'jggl') && !has(a, '→ 0')));
}
{
  const a = ans('спенд джигл android за июль');
  cases.push(expect('jggl android', has(a, 'android', 'июль') && moneyLike(a)));
}
{
  const a = ans('спенд джигл ios за июль');
  cases.push(expect('jggl ios', has(a, 'ios', 'июль') && moneyLike(a)));
}
{
  const a = ans('спенд джигл waitlist за июль');
  cases.push(expect('jggl waitlist july empty-or-data', has(a, 'waitlist') && (has(a, 'строк нет') || moneyLike(a))));
}
{
  const a = ans('отчёт джигл за июль');
  cases.push(expect('jggl july report', has(a, 'отчёт', 'jggl', 'июль') && has(a, 'спенд') && (has(a, 'cpi') || has(a, 'инсталлы')), a.slice(0, 160)));
}

// ── Hupp ──────────────────────────────────────────────────────────
{
  const a = ans('спенд хапп за июль');
  cases.push(expect('hupp july', has(a, 'hupp', 'июль') && moneyLike(a)));
  const m = a.match(/→\s*([\d\s]+)/);
  const n = m ? Number(m[1].replace(/\s/g, '')) : 0;
  cases.push(expect('hupp july not inflated', n > 0 && n < 8000, String(n)));
}
{
  const a = ans('спенд хаппа');
  cases.push(expect('hupp bare', has(a, 'hupp') && moneyLike(a)));
}

// ── All projects ──────────────────────────────────────────────────
{
  const a = ans('спенд по всем проектам');
  cases.push(expect('all spend', has(a, 'planto') && has(a, 'итого')));
}
{
  const a = ans('спенд по всем за июль');
  cases.push(expect('all july', has(a, 'июль') && has(a, 'planto')));
}

// ── Edge / help ───────────────────────────────────────────────────
{
  const a = ans('foobar xyz');
  cases.push(expect('unknown project', has(a, 'не распознал') || has(a, 'укажи проект')));
}
{
  const a = ans('/help');
  cases.push(expect('help', has(a, 'метрик') && has(a, 'пример')));
}
{
  const a = ans('что умеешь');
  cases.push(expect('capabilities', has(a, 'метрик')));
}

// ── Commands: report / digest builders ────────────────────────────
{
  const d = buildDigest(state);
  cases.push(expect('digest builds', has(d, 'дайджест') && has(d, 'planto')));
}
{
  const r = buildReport(state, 'планто');
  cases.push(expect('report planto', has(r, 'отчёт') && has(r, 'planto')));
}
{
  const r = buildReport(state, 'несуществующий');
  cases.push(expect('report unknown', has(r, 'не найден')));
}

// ── Consistency: july planto spend in band ────────────────────────
{
  const p = state.projects.find(x => x.id === 'planto');
  const pack = state.packs.planto;
  const rows = projectRows(p, pack).filter(r => (rowIso(r) || '').startsWith('2026-07'));
  const sp = sum(rows, 'spend');
  cases.push(expect('data: planto july ~100k', sp > 50_000 && sp < 200_000, sp.toFixed(0)));
}
{
  const p = state.projects.find(x => /jggl/i.test(x.id));
  const pack = state.packs[p.id];
  const rows = projectRows(p, pack).filter(r => (rowIso(r) || '').startsWith('2026-07'));
  const sp = sum(rows, 'spend');
  const inst = sum(rows, 'installs') || sum(rows, 'qregs');
  cases.push(expect('data: jggl july platforms', sp > 5_000 && sp < 30_000, sp.toFixed(0)));
  cases.push(expect('data: jggl installs from results', inst > 1000, String(inst)));
}
{
  const p = state.projects.find(x => x.id === 'hupp');
  const pack = state.packs[p.id];
  const rows = projectRows(p, pack);
  const sp = sum(rows, 'spend');
  cases.push(expect('data: hupp ~3.5k not 14k', sp > 1000 && sp < 8000, sp.toFixed(0)));
}
{
  const p = state.projects.find(x => /qlosoph/i.test(x.id));
  const pack = state.packs[p.id];
  const rows = projectRows(p, pack).filter(r => (rowIso(r) || '').startsWith('2026-07'));
  const sp = sum(rows, 'spend');
  const regs = sum(rows, 'qregs') || sum(rows, 'regs');
  const cpr = regs ? sp / regs : 0;
  cases.push(expect('data: qlosophy july regs ~400', regs >= 390 && regs <= 420, String(regs)));
  cases.push(expect('data: qlosophy july cpr ~5.42', cpr > 5 && cpr < 6, cpr.toFixed(2)));
  const a = ans('цена регистрации Qlosophy за июль');
  cases.push(expect('qlosophy cpr july matches table', has(a, 'cost/reg', 'июль') && /\$5[.,]4/.test(a), a.slice(0, 120)));
  const a2 = ans('сколько регистраций Qlosophy за июль');
  cases.push(expect('qlosophy regs july 400', has(a2, 'регистрац') && /400/.test(a2), a2.slice(0, 120)));
}

// ── Extra natural phrasings ───────────────────────────────────────
const extras = [
  ['сколько потратили на планта в июле', ['спенд', 'planto', 'июль']],
  ['какой расход у хаппа', ['спенд', 'hupp']],
  ['клики по джиглу android за неделю', ['клики', 'android']],
  ['показы джигл за июль', ['показы', 'jggl']],
  ['cpm планта за июль', ['cpm', 'planto']],
  ['ctr джигл', ['ctr', 'jggl']],
  ['сводка планто за вчера', ['отчёт', 'planto']],
  ['дневной отчёт хапп', ['отчёт', 'hupp']],
  ['стата за день джигл', ['отчёт', 'jggl']],
];
for (const [q, must] of extras) {
  const a = ans(q);
  cases.push(expect(`phrase: ${q}`, must.every(m => has(a, m)) && !has(a, 'укажи проект'), a.slice(0, 100)));
}

// ── Print results ─────────────────────────────────────────────────
const failed = cases.filter(c => !c.ok);
const passed = cases.filter(c => c.ok);
console.log(`\nQA matrix: ${passed.length}/${cases.length} passed\n`);
for (const c of cases) {
  const mark = c.ok ? '✓' : '✗';
  console.log(`${mark} ${c.name}${c.ok ? '' : ' — ' + c.detail}`);
}
if (failed.length) {
  console.log(`\nFAILED ${failed.length}:`);
  failed.forEach(f => console.log(' -', f.name, f.detail));
  process.exit(1);
}
console.log('\nAll good.');
