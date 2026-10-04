/* charts/distribution.js — how the per-trade outcomes are spread.
 *
 * Moved out of the single charts.js, function bodies unchanged. Drawing only: these take
 * data and return or set SVG and HTML, and compute no statistic of their own.
 */
"use strict";

import {$} from "../dom.js";
import {fmtVal, label, money, pct, sgn} from "../fmt.js";
import {bucketize, quantile, stats} from "../stats.js";
import {blankChart, txt} from "./svg.js";


/* ── the histogram ── */
export function renderHist(rows){
  const svg=$("hist"), vals=rows.map(r=>r.net_pnl);
  if(!vals.length){blankChart(svg,"no trades under the current filter");return;}
  const W=520,H=250,PL=44,PR=14,PT=16,PB=34,k=28;
  const lo=Math.min(...vals),hi=Math.max(...vals),step=(hi-lo)/k||1;
  const bins=Array.from({length:k},(_,i)=>({lo:lo+i*step,c:0}));
  for(const v of vals){let i=Math.floor((v-lo)/step);if(i>=k)i=k-1;if(i<0)i=0;bins[i].c++;}
  const maxC=Math.max(...bins.map(b=>b.c));
  const X=v=>PL+(v-lo)/((hi-lo)||1)*(W-PL-PR), Y=c=>H-PB-(c/maxC)*(H-PT-PB);
  const bw=(W-PL-PR)/k;
  let s=bins.filter(b=>b.c).map(b=>`<rect x="${X(b.lo).toFixed(1)}" y="${Y(b.c).toFixed(1)}"
    width="${(bw-1).toFixed(1)}" height="${(H-PB-Y(b.c)).toFixed(1)}"
    fill="${b.lo>=0?'var(--gain)':'var(--loss)'}" opacity=".55"/>`).join("");
  s+=`<line x1="${X(0).toFixed(1)}" x2="${X(0).toFixed(1)}" y1="${PT}" y2="${H-PB}"
    stroke="var(--ink-3)" stroke-dasharray="3 3"/>`;
  s+=`<line x1="${PL}" x2="${W-PR}" y1="${H-PB}" y2="${H-PB}" stroke="var(--line)"/>`;
  for(const v of [lo,0,hi]) s+=txt(X(v),H-PB+15,money(v),{anchor:"middle"});
  s+=txt(PL-6,Y(maxC)+4,maxC,{anchor:"end"})+txt(PL-6,H-PB+4,"0",{anchor:"end"});
  const st=stats(rows);
  s+=txt(W-PR,PT+4,`median ${money(st.median)} · mean ${money(st.mean)}`,
    {fill:"var(--ink-2)",size:11,anchor:"end",mono:false});
  if(Math.sign(st.median)!==Math.sign(st.mean)&&st.median!==0)
    s+=txt(W-PR,PT+20,"median and mean have opposite signs: a few extremes drive this",
      {fill:"var(--warn)",size:11,anchor:"end",mono:false});
  svg.innerHTML=s;
}

/* ── by year ── */
export function renderYears(rows){
  const svg=$("years"), by={};
  for(const r of rows){const y=r.entry_date.slice(0,4);by[y]=(by[y]||0)+r.net_pnl;}
  const yrs=Object.keys(by).sort();
  if(!yrs.length){blankChart(svg,"no trades under the current filter");return;}
  const W=520,H=220,PL=52,PR=14,PT=18,PB=28;
  const vals=yrs.map(y=>by[y]), lim=Math.max(...vals.map(Math.abs),1);
  const bw=(W-PL-PR)/yrs.length, y0=PT+(H-PT-PB)/2, h=v=>Math.abs(v)/lim*((H-PT-PB)/2);
  let s=`<line x1="${PL}" x2="${W-PR}" y1="${y0}" y2="${y0}" stroke="var(--line)"/>`;
  yrs.forEach((yr,i)=>{const v=by[yr],bh=h(v);
    s+=`<rect x="${(PL+i*bw+bw*.18).toFixed(1)}" y="${(v>=0?y0-bh:y0).toFixed(1)}"
      width="${(bw*.64).toFixed(1)}" height="${Math.max(bh,1).toFixed(1)}"
      fill="${v>=0?'var(--gain)':'var(--loss)'}" opacity=".7"/>`;
    s+=txt(PL+i*bw+bw/2,H-PB+14,yr.slice(2),{anchor:"middle",size:9.5});});
  s+=txt(PL-6,y0-((H-PT-PB)/2)+4,money(lim),{anchor:"end"})+txt(PL-6,y0+4,"0",{anchor:"end"});
  s+=txt(W-PR,PT-2,`${vals.filter(v=>v>0).length} of ${yrs.length} years positive`,
    {fill:"var(--ink-2)",size:11,anchor:"end",mono:false});
  svg.innerHTML=s;
}

/* ── the scatter ── */
export function renderScatter(rows,col){
  const svg=$("scatter");
  const pts=rows.filter(r=>r[col]!=null&&!Number.isNaN(r[col]));
  if(pts.length<5){
    blankChart(svg,`fewer than 5 values of ${label(col)}, so no scatter`);
    $("scatNote").textContent="";return;}
  const W=520,H=250,PL=52,PR=14,PT=14,PB=34;
  const xs=pts.map(p=>p[col]), ys=pts.map(p=>p.net_pnl);
  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  const X=v=>PL+(v-x0)/((x1-x0)||1)*(W-PL-PR);
  const Y=v=>H-PB-(v-y0)/((y1-y0)||1)*(H-PT-PB);
  let s=`<line x1="${PL}" x2="${W-PR}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}"
    stroke="var(--line)" stroke-dasharray="3 3"/>`;
  const r=pts.length>900?1.3:pts.length>350?1.8:2.4;
  s+=pts.map(p=>`<circle cx="${X(p[col]).toFixed(1)}" cy="${Y(p.net_pnl).toFixed(1)}" r="${r}"
    fill="${p.net_pnl>=0?'var(--gain)':'var(--loss)'}" opacity=".32"/>`).join("");
  // The bucket-mean line, to see through the scatter's noise
  const g=bucketize(pts,col,6);
  if(g){
    const m=g.map(b=>({x:(b.lo+b.hi)/2,y:stats(b.rows).mean}));
    s+=`<path d="${m.map((p,i)=>`${i?"L":"M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("")}"
      fill="none" stroke="var(--accent)" stroke-width="1.8"/>`;
    s+=m.map(p=>`<circle cx="${X(p.x).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="3"
      fill="var(--accent)"/>`).join("");
  }
  s+=`<line x1="${PL}" x2="${W-PR}" y1="${H-PB}" y2="${H-PB}" stroke="var(--line)"/>`;
  s+=txt(PL,H-PB+15,fmtVal(col,x0))+txt(W-PR,H-PB+15,fmtVal(col,x1),{anchor:"end"});
  s+=txt(PL-6,Y(y1)+4,money(y1),{anchor:"end"})+txt(PL-6,Y(0)+4,"0",{anchor:"end"})
    +txt(PL-6,Y(y0)+4,money(y0),{anchor:"end"});
  s+=txt(W-PR,PT+6,"the line is the bucket mean",{fill:"var(--accent)",size:10.5,anchor:"end",mono:false});
  svg.innerHTML=s;
  $("scatNote").textContent=`${label(col)} against per-trade P&L, ${pts.length.toLocaleString()} trades.`;
}

/* ── the two halves ── */
export function renderAB(rows,col){
  const vals=rows.map(r=>r[col]).filter(v=>v!=null&&!Number.isNaN(v)).sort((a,b)=>a-b);
  const box=$("ab");
  if(vals.length<20){box.innerHTML=`<div class="empty" style="grid-column:1/-1">too small a sample to split</div>`;return;}
  const cut=quantile(vals,.5);
  const lowRows=rows.filter(r=>r[col]!=null&&r[col]<cut), hiRows=rows.filter(r=>r[col]!=null&&r[col]>=cut);
  const L=stats(lowRows), H=stats(hiRows);
  const side=(title,s,other)=>`<div class="side">
      <h3>${title}</h3>
      <div class="big ${sgn(s.mean)}">${money(s.mean)}<span style="font-size:12.5px;color:var(--ink-3);font-weight:400"> /trade</span></div>
      <dl><dt>trades</dt><dd>${s.n}</dd>
          <dt>win rate</dt><dd>${pct(s.win)}</dd>
          <dt>median</dt><dd class="${sgn(s.median)}">${money(s.median)}</dd>
          <dt>total</dt><dd class="${sgn(s.sum)}">${money(s.sum)}</dd>
          <dt>worst</dt><dd class="neg">${money(s.min)}</dd>
          <dt>best</dt><dd class="pos">${money(s.max)}</dd></dl></div>`;
  const diff=H.mean-L.mean;
  const small=Math.min(L.n,H.n)<30;
  box.innerHTML=side(`${label(col)} below ${fmtVal(col,cut)}`,L,H)+side(`${label(col)} above ${fmtVal(col,cut)}`,H,L)+
    `<div class="verdict">The upper half is <b class="${sgn(diff)}">${money(diff)}</b> per trade against the lower.`+
    (small?" One side has fewer than 30 trades, so this gap is probably noise.":
      Math.abs(diff)<Math.abs(L.mean)*0.25?" The gap is under a quarter of the lower half's mean, so it may not be real.":
      " The gap is substantial and worth checking on another period.")+`</div>`;
}
