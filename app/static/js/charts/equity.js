/* charts/equity.js — the equity curve, its metric strip, and cumulative P&L.
 *
 * Moved out of the single charts.js, function bodies unchanged. Drawing only: these take
 * data and return or set SVG and HTML, and compute no statistic of their own.
 */
"use strict";

import {$} from "../dom.js";
import {money, num, pct, sgn} from "../fmt.js";

import {blankChart, fit, txt} from "./svg.js";


/* ── the cumulative P&L curve ── */
export function renderCurve(rows, hasEquity){
  const svg=$("curve");
  if(!rows.length){blankChart(svg,"no trades under the current filter, so no curve");return;}
  const seq=[...rows].sort((a,b)=>a.exit_date<b.exit_date?-1:1);
  let cum=0, pts=seq.map(r=>{cum+=r.net_pnl;return{d:r.exit_date,v:cum};});
  const {W, H} = fit(svg, {height: 260});
  const PL=62,PR=16,PT=18,PB=30;
  const lo=Math.min(0,...pts.map(p=>p.v)), hi=Math.max(0,...pts.map(p=>p.v));
  const x=i=>PL+i/(pts.length-1||1)*(W-PL-PR);
  const y=v=>H-PB-(v-lo)/((hi-lo)||1)*(H-PT-PB);
  let peak=-Infinity,dd=0,ddAt=0;
  pts.forEach((p,i)=>{if(p.v>peak)peak=p.v;const d=p.v-peak;if(d<dd){dd=d;ddAt=i;}});
  const line=pts.map((p,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const area=`${line}L${x(pts.length-1).toFixed(1)},${y(0).toFixed(1)}L${x(0).toFixed(1)},${y(0).toFixed(1)}Z`;
  const end=pts[pts.length-1].v, col=end>=0?"var(--gain)":"var(--loss)";
  let s=`<path d="${area}" fill="${col}" opacity=".10"/>`;
  s+=`<line x1="${PL}" x2="${W-PR}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line)"/>`;
  s+=`<path d="${line}" fill="none" stroke="${col}" stroke-width="1.6"/>`;
  s+=`<circle cx="${x(pts.length-1).toFixed(1)}" cy="${y(end).toFixed(1)}" r="3.5" fill="${col}"/>`;
  if(dd<-1){
    s+=`<line x1="${x(ddAt).toFixed(1)}" x2="${x(ddAt).toFixed(1)}" y1="${PT}" y2="${H-PB}"
        stroke="var(--loss)" stroke-width="1" stroke-dasharray="2 4" opacity=".55"/>`;
    s+=txt(x(ddAt)+5,PT+11,`largest fall ${money(dd)}`,{fill:"var(--loss)",size:11,mono:false});
  }
  for(const v of [hi,0,lo]) if(Math.abs(v)>1e-9||v===0)
    s+=txt(PL-7,y(v)+3.5,money(v),{anchor:"end"});
  const yrs=[...new Set(seq.map(r=>r.exit_date.slice(0,4)))];
  yrs.forEach(yr=>{const i=seq.findIndex(r=>r.exit_date.slice(0,4)===yr);
    if(i>=0)s+=txt(x(i),H-PB+15,yr,{anchor:"middle",size:11});});
  s+=txt(W-PR,PT+4,`final ${money(end)}`,{fill:col,size:12,anchor:"end",mono:false,weight:600});
  svg.innerHTML=s;
  // This curve is per-trade P&L accumulated by exit date, which is not account equity.
  // How far apart they are depends on the engine: the event-driven one has a real equity
  // curve above to compare against, and the trade table has none at all. The wording has
  // to differ between the two, or it is wrong under the event-driven engine — and nobody
  // looking at the chart could tell.
  $("curveNote").innerHTML = `Accumulated by exit date over ${pts.length.toLocaleString()} trades. ` + (hasEquity
    ? `A different basis from the <b>equity curve</b> above: this excludes unrealised P&L ` +
      `and is not scaled by capital used, so the two shapes can diverge widely. For ` +
      `account drawdown, read the equity curve.`
    : `<b>Not an equity curve</b> — the trade table computes each trade independently with ` +
      `no capital constraint, so a fall here is not an account drawdown. For a real equity ` +
      `curve, switch to the event-driven engine.`);
}

/* ── the equity curve (event-driven engine only) ── */
export function renderEquity(rows){
  const svg = $("equity");
  if(!rows || !rows.length){ blankChart(svg, "the trade-table engine has no equity curve"); return; }
  const {W, H} = fit(svg, {height: 280});
  const PL=70,PR=16,PT=18,PB=32;
  const mid = rows.map(r=>r.equity_mid), liq = rows.map(r=>r.equity_liq);
  const lo = Math.min(...liq), hi = Math.max(...mid);
  const x = i => PL + i/(rows.length-1||1)*(W-PL-PR);
  const y = v => H-PB-(v-lo)/((hi-lo)||1)*(H-PT-PB);
  const path = a => a.map((v,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  // The drawdown shading: where equity sits below its running peak
  let peak=-Infinity, dd=[];
  mid.forEach(v=>{ peak=Math.max(peak,v); dd.push(v-peak); });
  const worst = Math.min(...dd), worstAt = dd.indexOf(worst);
  const start = mid[0], end = mid[mid.length-1];
  const col = end>=start ? "var(--gain)" : "var(--loss)";
  let s = `<path d="${path(mid)}L${x(rows.length-1)},${y(start)}L${x(0)},${y(start)}Z"
             fill="${col}" opacity=".08"/>`;
  s += `<line x1="${PL}" x2="${W-PR}" y1="${y(start)}" y2="${y(start)}"
          stroke="var(--line)" stroke-dasharray="3 3"/>`;
  s += `<path d="${path(liq)}" fill="none" stroke="var(--ink-3)" stroke-width="1" opacity=".55"/>`;
  s += `<path d="${path(mid)}" fill="none" stroke="${col}" stroke-width="1.7"/>`;
  if(worst < -1){
    s += `<line x1="${x(worstAt).toFixed(1)}" x2="${x(worstAt).toFixed(1)}" y1="${PT}" y2="${H-PB}"
            stroke="var(--loss)" stroke-width="1" stroke-dasharray="2 4" opacity=".5"/>`;
    s += txt(x(worstAt)+5, PT+11, `max drawdown ${(worst/peak*100).toFixed(1)}%`,
             {fill:"var(--loss)", size:11, mono:false});
  }
  for(const v of [hi, start, lo]) s += txt(PL-7, y(v)+3.5, money(v), {anchor:"end"});
  const years = [...new Set(rows.map(r=>String(r.date).slice(0,4)))];
  years.forEach(yr=>{ const i = rows.findIndex(r=>String(r.date).slice(0,4)===yr);
    if(i>=0) s += txt(x(i), H-PB+15, yr, {anchor:"middle", size:11}); });
  s += txt(W-PR, PT+4, `final ${money(end)}`, {fill:col, size:12, anchor:"end", mono:false, weight:600});
  s += txt(W-PR, PT+20, "the thin line is liquidation value (marked at the bid, what could be sold)",
           {fill:"var(--ink-3)", size:11, anchor:"end", mono:false});
  svg.innerHTML = s;
}

/* ── the equity metrics strip ── */
export function renderEquityStrip(m){
  if(!m){ $("equityStrip").innerHTML = ""; return; }
  const tiles = [
    ["total return", pct(m["total_return"]), sgn(m["total_return"])],
    ["annualised", pct(m["annualised"]), sgn(m["annualised"])],
    ["max drawdown", pct(m["max_drawdown"]), "neg"],
    // `num` rather than .toFixed: the Sharpe is null when it cannot be computed (one
    // return, or zero volatility), and calling .toFixed on that threw a TypeError that
    // took the whole strip down with it.
    ["Sharpe", num(m["sharpe"]), ""],
    ["annualised vol", pct(m["annualised_vol"]), ""],
    ["mean capital used", pct(m["mean_capital_used"]), ""],
  ];
  $("equityStrip").innerHTML = tiles.map(([k,v,cls]) =>
    `<div class="stat"><dt>${k}</dt><dd class="${cls}">${v}</dd></div>`).join("");
}
