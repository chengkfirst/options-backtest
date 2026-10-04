/* stats.js — statistics over a set of trade rows.
 *
 * **This file is on notice.** The design plan's third principle is that the backend
 * computes every number and the interface only renders it, so that what is on screen is
 * what the system reproduces. These functions predate that rule: they re-derive sums,
 * win rates and quantiles in the browser from the trade rows, which means a figure on
 * screen can disagree with the same figure computed by `backtest.research.metrics` and
 * nothing would say so.
 *
 * They stay for one reason: brushing a time range and bucketing by a column need
 * statistics over a **subset**, and there is no endpoint for a subset yet. Phase 1 of the
 * frontend plan adds `POST /api/stats`, which runs the same Python the full-run numbers
 * come from; when it lands, the callers move to it and what remains here is quantile
 * boundaries for drawing, not reported metrics.
 *
 * Until then: nothing new may compute a reported metric here.
 */
"use strict";

export function quantile(sorted, q){
  if(!sorted.length) return NaN;
  const p = (sorted.length - 1) * q, lo = Math.floor(p), hi = Math.ceil(p);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (p - lo);
}

export function stats(rows){
  const p = rows.map(r => r.net_pnl).sort((a, b) => a - b), n = p.length;
  if(!n) return {n: 0, sum: 0, mean: 0, median: 0, win: 0, min: 0, max: 0};
  const sum = p.reduce((a, b) => a + b, 0);
  return {n, sum, mean: sum / n, median: quantile(p, .5),
          win: p.filter(v => v > 0).length / n, min: p[0], max: p[n - 1]};
}

/** Equal-frequency buckets over one column, or null when there are too few values for
 *  the requested count — an empty bucket is a bucket that says nothing. */
export function bucketize(rows, col, k){
  const vals = rows.map(r => r[col]).filter(v => v != null && !Number.isNaN(v))
                   .sort((a, b) => a - b);
  if(vals.length < k * 2) return null;
  const edges = [vals[0]];
  for(let i = 1; i < k; i++){
    const e = quantile(vals, i / k);
    if(e > edges[edges.length - 1]) edges.push(e);
  }
  edges.push(vals[vals.length - 1] + 1e-12);
  if(edges.length < 3) return null;
  const g = edges.slice(0, -1).map((lo, i) => ({lo, hi: edges[i + 1], rows: []}));
  for(const r of rows){
    const v = r[col];
    if(v == null || Number.isNaN(v)) continue;
    let i = g.findIndex(x => v >= x.lo && v < x.hi);
    if(i < 0) i = g.length - 1;
    g[i].rows.push(r);
  }
  return g.filter(x => x.rows.length);
}
