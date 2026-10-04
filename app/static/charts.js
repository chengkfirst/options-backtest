/* charts.js — rendering: it reads data and writes DOM, nothing else
 *
 * Each render* function takes an array of rows and writes HTML/SVG into a node with a
 * fixed id. None of them calls another, so changing one chart cannot disturb the rest.
 *
 * To add a chart: add a renderXxx here, put a container in index.html, and call it once
 * in apply(). Three places, and no fourth.
 */
"use strict";

/* ── three small helpers ──
 * Here rather than in core.js because they touch the DOM or emit SVG markup; core.js
 * stays pure.
 */

/** Get a node. */
const $ = id => document.getElementById(id);

/** One line of SVG text. Monospace and a secondary colour by default — every tick label
 *  on every chart goes through this, which is what keeps them consistent. */
const txt = (x, y, s, o = {}) => `<text x="${x}" y="${y}" fill="${o.fill || 'var(--ink-3)'}"
  font-size="${o.size || 10}"
  font-family="${o.mono !== false ? 'IBM Plex Mono, monospace' : 'IBM Plex Sans, sans-serif'}"
  text-anchor="${o.anchor || 'start'}"${o.weight ? ` font-weight="${o.weight}"` : ''}>${s}</text>`;

/** When a chart cannot be drawn, say why — a blank chart looks exactly like "there was
 *  never any data here". */
function blankChart(svg, why){
  const vb = (svg.getAttribute("viewBox") || "0 0 520 200").split(" ").map(Number);
  svg.innerHTML = `<text x="${vb[2] / 2}" y="${vb[3] / 2}" fill="var(--ink-3)" font-size="12"
    font-family="IBM Plex Sans, sans-serif" text-anchor="middle">${why}</text>`;
  notice(why);
}

/* ── the stat strip ── */
function renderStrip(rows){
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

/* ── the cumulative P&L curve ── */
function renderCurve(rows, hasEquity){
  const svg=$("curve");
  if(!rows.length){blankChart(svg,"no trades under the current filter, so no curve");return;}
  const seq=[...rows].sort((a,b)=>a.exit_date<b.exit_date?-1:1);
  let cum=0, pts=seq.map(r=>{cum+=r.net_pnl;return{d:r.exit_date,v:cum};});
  const W=1080,H=260,PL=62,PR=16,PT=18,PB=30;
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
    s+=txt(x(ddAt)+5,PT+11,`largest fall ${money(dd)}`,{fill:"var(--loss)",size:10.5,mono:false});
  }
  for(const v of [hi,0,lo]) if(Math.abs(v)>1e-9||v===0)
    s+=txt(PL-7,y(v)+3.5,money(v),{anchor:"end"});
  const yrs=[...new Set(seq.map(r=>r.exit_date.slice(0,4)))];
  yrs.forEach(yr=>{const i=seq.findIndex(r=>r.exit_date.slice(0,4)===yr);
    if(i>=0)s+=txt(x(i),H-PB+15,yr,{anchor:"middle",size:9.5});});
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

/* ── comparison by bucket ── */
function renderBuckets(rows,col,k){
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

/* ── the scatter ── */
function renderScatter(rows,col){
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

/* ── the histogram ── */
function renderHist(rows){
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
function renderYears(rows){
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

/* ── crossed buckets ── */
function renderCross(rows,a,b,k){
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
        if(!c.n)return `<td><span class="cell" style="color:var(--ink-3)">—</span></td>`;
        const thin=c.n<15, al=thin?.10:Math.min(Math.abs(c.mean)/lim,1)*.30+.06;
        const bg=c.mean>=0?`rgba(47,122,98,${al})`:`rgba(156,74,52,${al})`;
        return `<td><span class="cell" style="background:${bg};opacity:${thin?.45:1}">
          <b class="${sgn(c.mean)}">${money(c.mean)}</b><small>${c.n}</small></span></td>`;
      }).join("")+`</tr>`).join("")+`</tbody>`;
}

/* ── the two halves ── */
function renderAB(rows,col){
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

/* ── robustness ── */
function renderRobust(rows){
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
function renderDetail(rows){
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


/* ── the equity curve (event-driven engine only) ── */
function renderEquity(rows){
  const svg = $("equity");
  if(!rows || !rows.length){ blankChart(svg, "the trade-table engine has no equity curve"); return; }
  const W=1080,H=280,PL=70,PR=16,PT=18,PB=32;
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
             {fill:"var(--loss)", size:10.5, mono:false});
  }
  for(const v of [hi, start, lo]) s += txt(PL-7, y(v)+3.5, money(v), {anchor:"end"});
  const years = [...new Set(rows.map(r=>String(r.date).slice(0,4)))];
  years.forEach(yr=>{ const i = rows.findIndex(r=>String(r.date).slice(0,4)===yr);
    if(i>=0) s += txt(x(i), H-PB+15, yr, {anchor:"middle", size:9.5}); });
  s += txt(W-PR, PT+4, `final ${money(end)}`, {fill:col, size:12, anchor:"end", mono:false, weight:600});
  s += txt(W-PR, PT+20, "the thin line is liquidation value (marked at the bid, what could be sold)",
           {fill:"var(--ink-3)", size:10.5, anchor:"end", mono:false});
  svg.innerHTML = s;
}

/* ── P&L attribution (horizontal bars, positive and negative apart) ── */
function renderAttribution(attr){
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

/* ── the historical scenarios table ── */
function renderScenarios(rows){
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

/* ── the equity metrics strip ── */
function renderEquityStrip(m){
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
