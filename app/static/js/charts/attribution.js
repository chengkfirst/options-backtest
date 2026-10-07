/* charts/attribution.js — where the money came from, by Greek.
 *
 * **A waterfall, because attribution is an identity.** Each Greek's contribution plus the
 * residual has to add up to the result; a chart that does not show them adding up is
 * showing five unrelated numbers. The earlier form was a proportional bar per Greek —
 * |value| as a share of the sum of |values| — which cannot express that theta alone was
 * larger than the whole loss, because every bar was scaled against a denominator that has
 * no meaning. On a run that lost 31,220 it drew theta's −46,302 as "most of the bar" and
 * gamma's +18,239 as "some of the bar", and the reader had to do the arithmetic that the
 * chart existed to do.
 *
 * It also went unused: the run view rendered its own five-row table and this module was
 * only ever reached from the states gallery.
 *
 * Drawing only: takes data, sets SVG, computes no statistic beyond the running total the
 * shape is made of.
 */
"use strict";

import {$} from "../dom.js";
import {money, moneyShort} from "../fmt.js";
import {blankChart, fit, txt} from "./svg.js";

const STEPS = [
  {key: "delta",    name: "delta",    means: "direction"},
  {key: "gamma",    name: "gamma",    means: "movement"},
  {key: "vega",     name: "vega",     means: "implied vol"},
  {key: "theta",    name: "theta",    means: "time"},
  {key: "residual", name: "residual", means: "unplaced"},
];

/** The waterfall.
 *
 * `net` is the run's actual net P&L when the caller has it. The parts are an
 * approximation — a second-order expansion with a residual term — so they can miss the
 * result, and the gap is stated rather than hidden by drawing the final bar at wherever
 * the parts happened to land.
 */
export function renderAttribution(attr, net = null){
  const svg = $("attr");
  if(!attr || !attr.totals){ blankChart(svg, "no attribution data"); return; }

  const vals = STEPS.map(s => attr.totals[s.key] || 0);
  const sum = vals.reduce((a, b) => a + b, 0);

  // Every level the shape passes through, so nothing is drawn off the top.
  const levels = [0];
  let acc = 0;
  for(const v of vals){ acc += v; levels.push(acc); }
  if(net != null) levels.push(net);
  const lo = Math.min(...levels), hi = Math.max(...levels);
  const pad = (hi - lo) * 0.12 || 1;

  const {W, H} = fit(svg, {height: 320});
  const PL = 62, PR = 16, PT = 30, PB = 46;
  const Y = v => PT + (hi + pad - v) / ((hi - lo) + pad * 2) * (H - PT - PB);
  const n = STEPS.length + 1;                       // the steps, plus the result bar
  const slot = (W - PL - PR) / n;
  const bw = Math.min(slot * 0.56, 66);

  let s = "";
  // The zero line, which is the thing every bar is read against.
  s += `<line x1="${PL}" x2="${W - PR}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}"
          stroke="var(--border-strong)"/>`;

  acc = 0;
  STEPS.forEach((step, i) => {
    const v = vals[i];
    const from = acc, to = acc + v;
    acc = to;
    const x = PL + i * slot + (slot - bw) / 2;
    const yTop = Y(Math.max(from, to)), yBot = Y(Math.min(from, to));
    const h = Math.max(yBot - yTop, 1.5);           // a zero contribution still gets a mark

    // Each Greek has its own colour from the token table, so the same quantity is the
    // same colour here, on the exposure tab and on a trade card. Sign is carried by which
    // side of the zero line the step sits on and by the signed label, not by hue.
    s += `<rect x="${x.toFixed(1)}" y="${yTop.toFixed(1)}" width="${bw.toFixed(1)}"
            height="${h.toFixed(1)}" rx="1"
            fill="var(--greek-${step.key})" opacity="${step.key === "residual" ? ".45" : ".9"}"/>`;

    // The connector to the next step. It is what makes this a waterfall rather than five
    // floating bars: the eye follows the running total across.
    if(i < STEPS.length - 1 || net != null)
      s += `<line x1="${x.toFixed(1)}" x2="${(x + slot).toFixed(1)}"
              y1="${Y(to).toFixed(1)}" y2="${Y(to).toFixed(1)}"
              stroke="var(--border-strong)" stroke-dasharray="2 2"/>`;

    // The value above the step when it rises, below when it falls, so the label never
    // sits on top of the bar it belongs to.
    const up = v >= 0;
    s += txt(x + bw / 2, (up ? yTop - 7 : yBot + 14), moneyShort(v),
             {anchor: "middle", size: 11});
    s += txt(x + bw / 2, H - PB + 16, step.name, {anchor: "middle", size: 11, mono: false,
             fill: "var(--text-secondary)"});
    s += txt(x + bw / 2, H - PB + 29, step.means, {anchor: "middle", size: 10, mono: false,
             fill: "var(--text-muted)"});
  });

  // The result: a full-height bar from zero, not another step, because it is the total
  // rather than a contribution to it.
  const xr = PL + STEPS.length * slot + (slot - bw) / 2;
  const shown = net != null ? net : sum;
  const yTop = Y(Math.max(shown, 0)), yBot = Y(Math.min(shown, 0));
  s += `<rect x="${xr.toFixed(1)}" y="${yTop.toFixed(1)}" width="${bw.toFixed(1)}"
          height="${Math.max(yBot - yTop, 1.5).toFixed(1)}" rx="1"
          fill="${shown >= 0 ? "var(--success)" : "var(--danger)"}" opacity=".9"/>`;
  s += txt(xr + bw / 2, (shown >= 0 ? yTop - 7 : yBot + 14), moneyShort(shown),
           {anchor: "middle", size: 11, fill: shown >= 0 ? "var(--success)" : "var(--danger)"});
  s += txt(xr + bw / 2, H - PB + 16, "result", {anchor: "middle", size: 11, mono: false,
           fill: "var(--text-secondary)"});

  // The scale: both ends and zero. `money` is signed, so a negative floor reads as one —
  // an absolute value through a signed formatter is how a loss gets printed as "+31,220".
  s += txt(PL - 8, Y(hi) + 4, money(hi), {anchor: "end", size: 11});
  s += txt(PL - 8, Y(0) + 4, "0", {anchor: "end", size: 11});
  if(lo < 0) s += txt(PL - 8, Y(lo) + 4, money(lo), {anchor: "end", size: 11});

  // Whether the parts actually add up to the result. A second-order expansion need not,
  // and a gap of any size is a fact about how much of this chart can be trusted.
  if(net != null){
    const gap = net - sum;
    const share = Math.abs(net) > 0 ? Math.abs(gap) / Math.abs(net) : 0;
    s += txt(W - PR, PT - 12,
      Math.abs(gap) < 1
        ? "the parts add up to the result"
        : `the parts miss the result by ${money(gap)} (${(share * 100).toFixed(1)}%)`,
      {anchor: "end", size: 11, mono: false,
       fill: share > 0.05 ? "var(--warning)" : "var(--text-muted)"});
  }

  svg.innerHTML = s;
}
