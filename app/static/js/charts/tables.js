/* charts/tables.js — the tabular views: the strip, the buckets, the trade log.
 *
 * Moved out of the single charts.js, function bodies unchanged. Drawing only: these take
 * data and return or set SVG and HTML, and compute no statistic of their own.
 */
"use strict";

import {$} from "../dom.js";
import {REASON, fmtVal, label, money, pct, sgn} from "../fmt.js";
import {bucketize, stats} from "../stats.js";
import {notice} from "../net.js";


/* ── the stat strip ── */
export function renderStrip(rows){
  const s=stats(rows);
  const fees=rows.reduce((a,r)=>a+(r.fees||0),0), gross=rows.reduce((a,r)=>a+(r.gross_pnl||0),0);
  const hold=rows.length?rows.reduce((a,r)=>a+r.holding_days,0)/rows.length:0;
  const t=[["trades",s.n.toLocaleString(),""],["win rate",s.n?pct(s.win):"—",""],
    ["net P&L",s.n?money(s.sum):"—","gross "+(s.n?money(gross):"—")],
    ["mean per trade",s.n?money(s.mean):"—","median "+(s.n?money(s.median):"—")],
    ["fees",s.n?money(-fees):"—",gross?((fees/Math.abs(gross))*100).toFixed(1)+"% of gross":""],
    ["mean holding",s.n?hold.toFixed(1)+" d":"—",""]];
  $("strip").innerHTML=t.map(([k,v,note])=>{
    const cls=(k==="net P&L"||k==="mean per trade")&&s.n?sgn(k==="net P&L"?s.sum:s.mean):"";
    return `<div class="stat"><dt>${k}</dt><dd class="${cls}">${v}</dd>`+(note?`<div class="note">${note}</div>`:"")+`</div>`;
  }).join("");
}

/* ── comparison by bucket ── */
export function renderBuckets(rows,col,k){
  const g=bucketize(rows,col,k), t=$("buckets");
  if(!g){
    const why=`${label(col)} has too few distinct values to split into ${k} buckets (at least ${k*2} non-empty values are needed)`;
    t.innerHTML=`<tbody><tr><td class="empty">${why}</td></tr></tbody>`;
    $("dimNote").textContent=""; notice(why); return null;}
  const S=g.map(x=>({...x,...stats(x.rows)}));
  const maxAbs=Math.max(...S.map(s=>Math.abs(s.mean)),1), maxN=Math.max(...S.map(s=>s.n));
  t.innerHTML=`<thead><tr><th>${label(col)}</th><th>trades</th><th>win rate</th><th>mean</th>
    <th>median</th><th>total</th><th style="text-align:left">relative</th></tr></thead><tbody>`+
    S.map(s=>{const w=Math.abs(s.mean)/maxAbs*48, left=s.mean>=0?50:50-w;
      return `<tr><td>${fmtVal(col,s.lo)} – ${fmtVal(col,s.hi)}</td>
        <td class="n">${s.n}<span class="mini" style="width:${(s.n/maxN*34).toFixed(0)}px;margin-left:7px"></span></td>
        <td class="n">${pct(s.win)}</td>
        <td class="n ${sgn(s.mean)}">${money(s.mean)}</td>
        <td class="n ${sgn(s.median)}">${money(s.median)}</td>
        <td class="n ${sgn(s.sum)}">${money(s.sum)}</td>
        <td class="barcell"><div class="axis" style="left:50%"></div>
        <div class="bar ${s.mean>=0?'p':'n'}" style="left:${left}%;width:${w}%"></div></td></tr>`;
    }).join("")+`</tbody>`;
  const dir=S[S.length-1].mean-S[0].mean;
  const mono=S.every((s,i)=>i===0||s.mean>=S[i-1].mean)||S.every((s,i)=>i===0||s.mean<=S[i-1].mean);
  $("dimNote").textContent=`Top bucket minus bottom: ${money(dir)}. `+(mono?"The buckets are monotonic, which is more trustworthy than a single extreme.":"The buckets are not monotonic, so this may be noise.")+
    (Math.min(...S.map(s=>s.n))<20?" One bucket has fewer than 20 trades and is doubtful.":"");
  return S;
}

/* ── crossed buckets ── */
export function renderCross(rows,a,b,k){
  const sec=$("crossSec");
  if(!b){sec.hidden=true;return;}            // no cross dimension chosen; not an error
  const ga=bucketize(rows,a,k), gb=bucketize(rows,b,k);
  if(!ga||!gb){
    sec.hidden=true;
    notice(`crossed buckets skipped: ${label(ga?b:a)} has too few distinct values for ${k} buckets`);
    return;}
  sec.hidden=false;
  const cells=ga.map(A=>gb.map(B=>stats(A.rows.filter(r=>r[b]>=B.lo&&r[b]<B.hi))));
  const lim=Math.max(...cells.flat().filter(c=>c.n>=15).map(c=>Math.abs(c.mean)),1);
  $("cross").innerHTML=`<thead><tr><th>${label(a)} \\ ${label(b)}</th>`+
    gb.map(B=>`<th style="text-align:center">${fmtVal(b,B.lo)}–${fmtVal(b,B.hi)}</th>`).join("")+
    `</tr></thead><tbody>`+ga.map((A,i)=>`<tr><td>${fmtVal(a,A.lo)}–${fmtVal(a,A.hi)}</td>`+
      cells[i].map(c=>{
        if(!c.n)return `<td><span class="cell" style="color:var(--text-muted)">—</span></td>`;
        const thin=c.n<15, al=thin?.10:Math.min(Math.abs(c.mean)/lim,1)*.30+.06;
        // Mixed from the tokens rather than written as rgba(). The two literals here were
        // the **light theme's** gain and loss from an older palette, so in dark mode every
        // cell was tinted a colour that belonged to a stylesheet that no longer existed.
        const bg=`color-mix(in srgb, var(${c.mean>=0?"--success":"--danger"}) `
               +`${(al*100).toFixed(1)}%, transparent)`;
        return `<td><span class="cell" style="background:${bg};opacity:${thin?.45:1}">
          <b class="${sgn(c.mean)}">${money(c.mean)}</b><small>${c.n}</small></span></td>`;
      }).join("")+`</tr>`).join("")+`</tbody>`;
}

/* ── robustness ── */
export function renderRobust(rows){
  const sorted=[...rows].sort((a,b)=>b.net_pnl-a.net_pnl);
  const total=rows.reduce((a,r)=>a+r.net_pnl,0), by={};
  for(const r of rows){const y=r.entry_date.slice(0,4);by[y]=(by[y]||0)+r.net_pnl;}
  const yv=Object.values(by), best=yv.length?Math.max(...yv):0;
  const lines=[1,3,5,10].filter(n=>n<rows.length).map(n=>{
    const cut=sorted.slice(0,n).reduce((a,r)=>a+r.net_pnl,0);
    return [`without the best ${n} trades`,total-cut,rows.length-n];});
  if(yv.length>1)lines.push(["without the best year",total-best,"—"]);
  $("robust").innerHTML=`<thead><tr><th>case</th><th>net P&L left</th><th>trades left</th><th>verdict</th></tr></thead><tbody>
    <tr><td>all of them</td><td class="n ${sgn(total)}">${money(total)}</td><td class="n">${rows.length}</td>
    <td style="text-align:left;color:var(--ink-3)">the baseline</td></tr>`+
    lines.map(([k,rest,n])=>{const flip=Math.sign(rest)!==Math.sign(total)&&total!==0;
      return `<tr><td>${k}</td><td class="n ${sgn(rest)}">${money(rest)}</td><td class="n">${n}</td>
        <td style="text-align:left;color:${flip?'var(--loss)':'var(--ink-3)'}">${flip?'sign flips · does not generalise':'direction holds'}</td></tr>`;
    }).join("")+`</tbody>`;
}

/* ── the trade log ── */
export function renderDetail(rows){
  const show=[...rows].sort((a,b)=>a.entry_date<b.entry_date?-1:1).slice(0,300);
  $("detailNote").textContent=rows.length>300?`${rows.length.toLocaleString()} trades; the first 300 are shown.`
    :`${rows.length.toLocaleString()} trades.`;
  $("detail").innerHTML=`<thead><tr><th>entry</th><th>exit</th><th>contract</th><th>entry px</th><th>exit px</th>
    <th>net P&L</th><th>exit reason</th><th>held</th><th>IV in</th><th>delta in</th></tr></thead><tbody>`+
    show.map(r=>`<tr><td>${r.entry_date}</td><td>${r.exit_date}</td>
      <td style="font-family:var(--mono);font-size:11.5px">${r.contract??"—"}</td>
      <td class="n">${r.entry_price?.toFixed(2)??"—"}</td><td class="n">${r.exit_price?.toFixed(2)??"—"}</td>
      <td class="n ${sgn(r.net_pnl)}">${money(r.net_pnl)}</td>
      <td style="text-align:left">${REASON[r.exit_reason]||r.exit_reason||"—"}</td>
      <td class="n">${r.holding_days??"—"}</td>
      <td class="n">${r.entry_iv!=null?pct(r.entry_iv):"—"}</td>
      <td class="n">${r.entry_delta!=null?r.entry_delta.toFixed(3):"—"}</td></tr>`).join("")+`</tbody>`;
}

/* ── the historical scenarios table ── */
export function renderScenarios(rows){
  if(!rows || !rows.length){ $("scen").innerHTML = ""; return; }
  $("scen").innerHTML = `<thead><tr><th>scenario</th><th>from</th><th>to</th><th>sessions</th>
      <th>return</th><th>max drawdown</th><th style="text-align:left">note</th></tr></thead><tbody>` +
    rows.map(r=>{
      const covered = r["sessions"] > 0;
      return `<tr><td>${r["scenario"]}</td><td>${r["from"]}</td><td>${r["to"]}</td>
        <td class="n">${r["sessions"]}</td>
        <td class="n ${covered ? sgn(r["return"]) : ""}">${covered ? pct(r["return"]) : "—"}</td>
        <td class="n ${covered ? "neg" : ""}">${covered ? pct(r["max drawdown"]) : "—"}</td>
        <td style="text-align:left;color:var(--ink-3)">${r["note"] || ""}</td></tr>`;
    }).join("") + `</tbody>`;
}
