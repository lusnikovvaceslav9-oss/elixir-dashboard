/** Live budget remainder from project snapshot / Meta / sheet. */

import { sum, projectRows, rowIso, aggregateByDate } from './data.js';

/**
 * If pack has a sheet-provided remainder (e.g. Quadcode July tab with «остаток»), prefer it.
 */
export function sheetBudgetRemainder(pack) {
  const raw = pack?.sheetRemainder;
  if (raw == null || raw === '') return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/**
 * Spend used for manual budget tracking — aligned with dashboard:
 * - aggregate by date (no campaign double-count)
 * - if snapshot asOf is set and older months exist, count only from that month
 *   (avoids June spend eating a July budget pool)
 */
function spendForManualBudget(proj, pack) {
  let rows = projectRows(proj, pack) || [];
  rows = aggregateByDate(rows.map(r => {
    if (r?.iso) return r;
    const iso = rowIso(r);
    return iso ? { ...r, iso } : r;
  }));

  const asOf = String(proj.budgetRemainderAsOf || '').slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(asOf)) {
    const monthStart = `${asOf.slice(0, 7)}-01`;
    const hasOlder = rows.some(r => (r.iso || '') && r.iso < monthStart);
    const since = rows.filter(r => (r.iso || '') >= monthStart);
    if (hasOlder && since.length) rows = since;
  }

  return { rows, spent: sum(rows, 'spend') };
}

/**
 * Live budget remainder from snapshot:
 * budgetTotal = spentAtInput + remainderEntered → remainderNow = total − currentSpend.
 * Mirrors elixir.html reconcileBudgetSnapshot when the spend base shrinks.
 * Prefers pack.sheetRemainder when a sheet tab literally stores «остаток».
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

  const fromSheet = sheetBudgetRemainder(pack);
  if (fromSheet != null) {
    return {
      remainder: fromSheet,
      total: null,
      spent: null,
      currency: cur,
      source: 'sheet',
    };
  }

  const remEntered = Number(proj.budgetRemainder);
  if (!Number.isFinite(remEntered)) return null;

  let spentAt = Number(proj.budgetRemainderSpentAt);
  let total = Number(proj.budgetTotal);
  const { spent } = spendForManualBudget(proj, pack);

  if (Number.isFinite(spentAt) && Number.isFinite(total) && total > 0) {
    // Dashboard reconcile: spend base shrank (scope/dedupe) → re-anchor to entered remainder
    if (spentAt > spent + 0.5) {
      spentAt = spent;
      total = spent + remEntered;
    }
    return {
      remainder: total - spent,
      total,
      spent,
      spentAt,
      currency: cur,
      source: 'manual',
    };
  }

  return { remainder: remEntered, total: null, spent, currency: cur, source: 'manual_raw' };
}
