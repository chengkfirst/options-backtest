/* charts/attribution.js — where the money came from, by Greek.
 *
 * Moved out of the single charts.js, function bodies unchanged. Drawing only: these take
 * data and return or set SVG and HTML, and compute no statistic of their own.
 */
"use strict";

import {$} from "../dom.js";
import {money} from "../fmt.js";
import {blankChart, txt} from "./svg.js";


/* ── P&L attribution (horizontal bars, positive and negative apart) ── */
export function renderAttribution(attr){
  const svg = $("attr");
  if(!attr || !attr.totals){ blankChart(svg, "no attribution data"); return; }
  const names = ["delta","gamma","vega","theta","residual"];
  const label = {delta:"delta · direction", gamma:"gamma · movement",
                 vega:"vega · implied vol", theta:"theta · time", residual:"residual"};
  const vals = names.map(n=>attr.totals[n] || 0);
  const lim = Math.max(...vals.map(Math.abs), 1);
  const W=520,H=200,PL=118,PR=54,PT=14,PB=26;
  const mid = PL + (W-PL-PR)/2;
  const half = (W-PL-PR)/2;
  const rowH = (H-PT-PB)/names.length;
  let s = `<line x1="${mid}" x2="${mid}" y1="${PT}" y2="${H-PB}" stroke="var(--line)"/>`;
  names.forEach((n,i)=>{
    const v = vals[i], w = Math.abs(v)/lim*half*0.92;
    const yTop = PT + i*rowH + rowH*0.18, h = rowH*0.62;
    s += `<rect x="${(v>=0?mid:mid-w).toFixed(1)}" y="${yTop.toFixed(1)}"
            width="${w.toFixed(1)}" height="${h.toFixed(1)}"
            fill="${v>=0?'var(--gain)':'var(--loss)'}" opacity="${n==='residual'?.35:.65}"/>`;
    s += txt(PL-8, yTop+h*0.72, label[n], {anchor:"end", size:11, mono:false,
             fill: n==='residual' ? "var(--ink-3)" : "var(--ink-2)"});
    s += txt(W-PR+6, yTop+h*0.72, money(v), {size:11, fill:v>=0?"var(--gain)":"var(--loss)"});
  });
  if(attr.high_residual)
    s += txt(mid, H-PB+16, `${attr.high_residual} trades have too large a residual to trust`,
             {anchor:"middle", size:10.5, fill:"var(--warn)", mono:false});
  svg.innerHTML = s;
}
