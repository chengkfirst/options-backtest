/* charts.js — 渲染，只读数据、只写 DOM
 *
 * 每个 render* 函数拿一份行数组，往一个固定 id 的节点里写 HTML/SVG。
 * 它们之间互不调用，所以改一张图不会牵动别的。
 *
 * 想加一张图 → 在这里加一个 renderXxx，再到 index.html 放一个容器、
 * 到 app.js 的 apply() 里调一次。三处，没有第四处。
 */
"use strict";

/* ── 三个小工具 ──
 * 放这里而不是 core.js，因为它们碰 DOM 或产出 SVG 标记；core.js 要保持纯。
 */

/** 取节点。 */
const $ = id => document.getElementById(id);

/** 一行 SVG 文本。默认等宽字体、次要色——图上的刻度全走这里，风格才一致。 */
const txt = (x, y, s, o = {}) => `<text x="${x}" y="${y}" fill="${o.fill || 'var(--ink-3)'}"
  font-size="${o.size || 10}"
  font-family="${o.mono !== false ? 'IBM Plex Mono, monospace' : 'IBM Plex Sans, sans-serif'}"
  text-anchor="${o.anchor || 'start'}"${o.weight ? ` font-weight="${o.weight}"` : ''}>${s}</text>`;

/** 图画不出来时写明为什么——空白的图和"这里本来就没数据"长得一模一样。 */
function blankChart(svg, why){
  const vb = (svg.getAttribute("viewBox") || "0 0 520 200").split(" ").map(Number);
  svg.innerHTML = `<text x="${vb[2] / 2}" y="${vb[3] / 2}" fill="var(--ink-3)" font-size="12"
    font-family="IBM Plex Sans, sans-serif" text-anchor="middle">${why}</text>`;
  notice(why);
}

/* ── 统计条 ── */
function renderStrip(rows){
  const s=stats(rows);
  const fees=rows.reduce((a,r)=>a+(r.fees||0),0), gross=rows.reduce((a,r)=>a+(r.gross_pnl||0),0);
  const hold=rows.length?rows.reduce((a,r)=>a+r.holding_days,0)/rows.length:0;
  const t=[["交易笔数",s.n.toLocaleString(),""],["胜率",s.n?pct(s.win):"—",""],
    ["净盈亏",s.n?money(s.sum):"—","毛 "+(s.n?money(gross):"—")],
    ["平均每笔",s.n?money(s.mean):"—","中位 "+(s.n?money(s.median):"—")],
    ["费用",s.n?money(-fees):"—",gross?((fees/Math.abs(gross))*100).toFixed(1)+"% 毛盈亏":""],
    ["平均持有",s.n?hold.toFixed(1)+" 日":"—",""]];
  $("strip").innerHTML=t.map(([k,v,note])=>{
    const cls=(k==="净盈亏"||k==="平均每笔")&&s.n?sgn(k==="净盈亏"?s.sum:s.mean):"";
    return `<div class="stat"><dt>${k}</dt><dd class="${cls}">${v}</dd>`+(note?`<div class="note">${note}</div>`:"")+`</div>`;
  }).join("");
}

/* ── 累计盈亏曲线 ── */
function renderCurve(rows, hasEquity){
  const svg=$("curve");
  if(!rows.length){blankChart(svg,"当前筛选下没有交易，画不出累计盈亏");return;}
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
    s+=txt(x(ddAt)+5,PT+11,`最大回落 ${money(dd)}`,{fill:"var(--loss)",size:10.5,mono:false});
  }
  for(const v of [hi,0,lo]) if(Math.abs(v)>1e-9||v===0)
    s+=txt(PL-7,y(v)+3.5,money(v),{anchor:"end"});
  const yrs=[...new Set(seq.map(r=>r.exit_date.slice(0,4)))];
  yrs.forEach(yr=>{const i=seq.findIndex(r=>r.exit_date.slice(0,4)===yr);
    if(i>=0)s+=txt(x(i),H-PB+15,yr,{anchor:"middle",size:9.5});});
  s+=txt(W-PR,PT+4,`期末 ${money(end)}`,{fill:col,size:12,anchor:"end",mono:false,weight:600});
  svg.innerHTML=s;
  // 这条曲线是"逐笔盈亏按了结日累加"，跟账户权益不是一回事。差别有多大取决于引擎：
  // 事件驱动有真权益曲线在上面可对照，成交表根本没有。两种情况下说法必须不同，
  // 否则在事件驱动下这句话是错的——而看图的人分不出来。
  $("curveNote").innerHTML = `按了结日累加，共 ${pts.length.toLocaleString()} 笔。` + (hasEquity
    ? `与上面的<b>权益曲线</b>口径不同：这里不含未平仓浮盈亏、也不按资金占用缩放，` +
      `两条线的形状可以差很远。看账户回撤请以权益曲线为准。`
    : `<b>不是权益曲线</b>——成交表各笔独立计算、不含资金约束，所以这里的"回落"不等于账户回撤。` +
      `想要真权益曲线，把引擎换成事件驱动。`);
}

/* ── 分档对比 ── */
function renderBuckets(rows,col,k){
  const g=bucketize(rows,col,k), t=$("buckets");
  if(!g){
    const why=`${label(col)} 的有效取值不足以分成 ${k} 档（至少要 ${k*2} 个非空取值）`;
    t.innerHTML=`<tbody><tr><td class="empty">${why}</td></tr></tbody>`;
    $("dimNote").textContent=""; notice(why); return null;}
  const S=g.map(x=>({...x,...stats(x.rows)}));
  const maxAbs=Math.max(...S.map(s=>Math.abs(s.mean)),1), maxN=Math.max(...S.map(s=>s.n));
  t.innerHTML=`<thead><tr><th>${label(col)}</th><th>笔数</th><th>胜率</th><th>平均每笔</th>
    <th>中位</th><th>合计</th><th style="text-align:left">相对强弱</th></tr></thead><tbody>`+
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
  $("dimNote").textContent=`最高档减最低档 ${money(dir)}。`+(mono?"各档单调——这类关系比单点极值可信。":"各档不单调，可能是噪音。")+
    (Math.min(...S.map(s=>s.n))<20?" 有档位少于 20 笔，该档存疑。":"");
  return S;
}

/* ── 散点 ── */
function renderScatter(rows,col){
  const svg=$("scatter");
  const pts=rows.filter(r=>r[col]!=null&&!Number.isNaN(r[col]));
  if(pts.length<5){
    blankChart(svg,`${label(col)} 上有效取值不足 5 个，画不出散点`);
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
  // 分档均值折线，压住散点噪音
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
  s+=txt(W-PR,PT+6,"蓝线＝分档均值",{fill:"var(--accent)",size:10.5,anchor:"end",mono:false});
  svg.innerHTML=s;
  $("scatNote").textContent=`${label(col)} 对单笔盈亏，${pts.length.toLocaleString()} 笔。`;
}

/* ── 直方图 ── */
function renderHist(rows){
  const svg=$("hist"), vals=rows.map(r=>r.net_pnl);
  if(!vals.length){blankChart(svg,"当前筛选下没有交易");return;}
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
  s+=txt(W-PR,PT+4,`中位 ${money(st.median)} · 均值 ${money(st.mean)}`,
    {fill:"var(--ink-2)",size:11,anchor:"end",mono:false});
  if(Math.sign(st.median)!==Math.sign(st.mean)&&st.median!==0)
    s+=txt(W-PR,PT+20,"中位与均值反号：收益由少数极值驱动",
      {fill:"var(--warn)",size:11,anchor:"end",mono:false});
  svg.innerHTML=s;
}

/* ── 年份 ── */
function renderYears(rows){
  const svg=$("years"), by={};
  for(const r of rows){const y=r.entry_date.slice(0,4);by[y]=(by[y]||0)+r.net_pnl;}
  const yrs=Object.keys(by).sort();
  if(!yrs.length){blankChart(svg,"当前筛选下没有交易");return;}
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
  s+=txt(W-PR,PT-2,`${yrs.length} 年里 ${vals.filter(v=>v>0).length} 年为正`,
    {fill:"var(--ink-2)",size:11,anchor:"end",mono:false});
  svg.innerHTML=s;
}

/* ── 交叉 ── */
function renderCross(rows,a,b,k){
  const sec=$("crossSec");
  if(!b){sec.hidden=true;return;}            // 用户没选交叉维度，不是错误
  const ga=bucketize(rows,a,k), gb=bucketize(rows,b,k);
  if(!ga||!gb){
    sec.hidden=true;
    notice(`交叉分组已跳过：${label(ga?b:a)} 的有效取值不足以分成 ${k} 档`);
    return;}
  sec.hidden=false;
  const cells=ga.map(A=>gb.map(B=>stats(A.rows.filter(r=>r[b]>=B.lo&&r[b]<B.hi))));
  const lim=Math.max(...cells.flat().filter(c=>c.n>=15).map(c=>Math.abs(c.mean)),1);
  $("cross").innerHTML=`<thead><tr><th>${label(a)} ＼ ${label(b)}</th>`+
    gb.map(B=>`<th style="text-align:center">${fmtVal(b,B.lo)}–${fmtVal(b,B.hi)}</th>`).join("")+
    `</tr></thead><tbody>`+ga.map((A,i)=>`<tr><td>${fmtVal(a,A.lo)}–${fmtVal(a,A.hi)}</td>`+
      cells[i].map(c=>{
        if(!c.n)return `<td><span class="cell" style="color:var(--ink-3)">—</span></td>`;
        const thin=c.n<15, al=thin?.10:Math.min(Math.abs(c.mean)/lim,1)*.30+.06;
        const bg=c.mean>=0?`rgba(47,122,98,${al})`:`rgba(156,74,52,${al})`;
        return `<td><span class="cell" style="background:${bg};opacity:${thin?.45:1}">
          <b class="${sgn(c.mean)}">${money(c.mean)}</b><small>${c.n} 笔</small></span></td>`;
      }).join("")+`</tr>`).join("")+`</tbody>`;
}

/* ── 两组对比 ── */
function renderAB(rows,col){
  const vals=rows.map(r=>r[col]).filter(v=>v!=null&&!Number.isNaN(v)).sort((a,b)=>a-b);
  const box=$("ab");
  if(vals.length<20){box.innerHTML=`<div class="empty" style="grid-column:1/-1">样本太少，无法切分</div>`;return;}
  const cut=quantile(vals,.5);
  const lowRows=rows.filter(r=>r[col]!=null&&r[col]<cut), hiRows=rows.filter(r=>r[col]!=null&&r[col]>=cut);
  const L=stats(lowRows), H=stats(hiRows);
  const side=(title,s,other)=>`<div class="side">
      <h3>${title}</h3>
      <div class="big ${sgn(s.mean)}">${money(s.mean)}<span style="font-size:12.5px;color:var(--ink-3);font-weight:400"> ／笔</span></div>
      <dl><dt>笔数</dt><dd>${s.n}</dd>
          <dt>胜率</dt><dd>${pct(s.win)}</dd>
          <dt>中位</dt><dd class="${sgn(s.median)}">${money(s.median)}</dd>
          <dt>合计</dt><dd class="${sgn(s.sum)}">${money(s.sum)}</dd>
          <dt>最大亏</dt><dd class="neg">${money(s.min)}</dd>
          <dt>最大盈</dt><dd class="pos">${money(s.max)}</dd></dl></div>`;
  const diff=H.mean-L.mean;
  const small=Math.min(L.n,H.n)<30;
  box.innerHTML=side(`${label(col)} 低于 ${fmtVal(col,cut)}`,L,H)+side(`${label(col)} 高于 ${fmtVal(col,cut)}`,H,L)+
    `<div class="verdict">高的一半平均每笔比低的一半 <b class="${sgn(diff)}">${money(diff)}</b>。`+
    (small?" 有一侧不足 30 笔，这个差距很可能是噪音。":
      Math.abs(diff)<Math.abs(L.mean)*0.25?" 差距不到低档均值的四分之一，未必是真的。":
      " 差距可观，值得换个时间段验证。")+`</div>`;
}

/* ── 稳健性 ── */
function renderRobust(rows){
  const sorted=[...rows].sort((a,b)=>b.net_pnl-a.net_pnl);
  const total=rows.reduce((a,r)=>a+r.net_pnl,0), by={};
  for(const r of rows){const y=r.entry_date.slice(0,4);by[y]=(by[y]||0)+r.net_pnl;}
  const yv=Object.values(by), best=yv.length?Math.max(...yv):0;
  const lines=[1,3,5,10].filter(n=>n<rows.length).map(n=>{
    const cut=sorted.slice(0,n).reduce((a,r)=>a+r.net_pnl,0);
    return [`去掉最赚的 ${n} 笔`,total-cut,rows.length-n];});
  if(yv.length>1)lines.push(["去掉最好的一年",total-best,"—"]);
  $("robust").innerHTML=`<thead><tr><th>情形</th><th>剩余净盈亏</th><th>剩余笔数</th><th>结论</th></tr></thead><tbody>
    <tr><td>全部</td><td class="n ${sgn(total)}">${money(total)}</td><td class="n">${rows.length}</td>
    <td style="text-align:left;color:var(--ink-3)">基准</td></tr>`+
    lines.map(([k,rest,n])=>{const flip=Math.sign(rest)!==Math.sign(total)&&total!==0;
      return `<tr><td>${k}</td><td class="n ${sgn(rest)}">${money(rest)}</td><td class="n">${n}</td>
        <td style="text-align:left;color:${flip?'var(--loss)':'var(--ink-3)'}">${flip?'符号翻转 · 不可外推':'方向不变'}</td></tr>`;
    }).join("")+`</tbody>`;
}

/* ── 明细 ── */
function renderDetail(rows){
  const show=[...rows].sort((a,b)=>a.entry_date<b.entry_date?-1:1).slice(0,300);
  $("detailNote").textContent=rows.length>300?`共 ${rows.length.toLocaleString()} 笔，下表显示最早 300 笔。`
    :`共 ${rows.length.toLocaleString()} 笔。`;
  $("detail").innerHTML=`<thead><tr><th>入场</th><th>出场</th><th>合约</th><th>入场价</th><th>出场价</th>
    <th>净盈亏</th><th>了结原因</th><th>持有</th><th>进场 IV</th><th>进场 delta</th></tr></thead><tbody>`+
    show.map(r=>`<tr><td>${r.entry_date}</td><td>${r.exit_date}</td>
      <td style="font-family:var(--mono);font-size:11.5px">${r.contract??"—"}</td>
      <td class="n">${r.entry_price?.toFixed(2)??"—"}</td><td class="n">${r.exit_price?.toFixed(2)??"—"}</td>
      <td class="n ${sgn(r.net_pnl)}">${money(r.net_pnl)}</td>
      <td style="text-align:left">${REASON[r.exit_reason]||r.exit_reason||"—"}</td>
      <td class="n">${r.holding_days??"—"}</td>
      <td class="n">${r.entry_iv!=null?pct(r.entry_iv):"—"}</td>
      <td class="n">${r.entry_delta!=null?r.entry_delta.toFixed(3):"—"}</td></tr>`).join("")+`</tbody>`;
}


/* ── 权益曲线（只有事件驱动引擎有）── */
function renderEquity(rows){
  const svg = $("equity");
  if(!rows || !rows.length){ blankChart(svg, "成交表引擎没有权益曲线"); return; }
  const W=1080,H=280,PL=70,PR=16,PT=18,PB=32;
  const mid = rows.map(r=>r.equity_mid), liq = rows.map(r=>r.equity_liq);
  const lo = Math.min(...liq), hi = Math.max(...mid);
  const x = i => PL + i/(rows.length-1||1)*(W-PL-PR);
  const y = v => H-PB-(v-lo)/((hi-lo)||1)*(H-PT-PB);
  const path = a => a.map((v,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  // 回撤阴影：权益低于历史峰值的部分
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
    s += txt(x(worstAt)+5, PT+11, `最大回撤 ${(worst/peak*100).toFixed(1)}%`,
             {fill:"var(--loss)", size:10.5, mono:false});
  }
  for(const v of [hi, start, lo]) s += txt(PL-7, y(v)+3.5, money(v), {anchor:"end"});
  const years = [...new Set(rows.map(r=>String(r.date).slice(0,4)))];
  years.forEach(yr=>{ const i = rows.findIndex(r=>String(r.date).slice(0,4)===yr);
    if(i>=0) s += txt(x(i), H-PB+15, yr, {anchor:"middle", size:9.5}); });
  s += txt(W-PR, PT+4, `期末 ${money(end)}`, {fill:col, size:12, anchor:"end", mono:false, weight:600});
  s += txt(W-PR, PT+20, "细线＝清算价（按 bid 估值，才是真卖得掉的）",
           {fill:"var(--ink-3)", size:10.5, anchor:"end", mono:false});
  svg.innerHTML = s;
}

/* ── 盈亏归因（横向条形，正负分列）── */
function renderAttribution(attr){
  const svg = $("attr");
  if(!attr || !attr.totals){ blankChart(svg, "没有归因数据"); return; }
  const names = ["delta","gamma","vega","theta","residual"];
  const label = {delta:"delta 标的方向", gamma:"gamma 波动", vega:"vega 隐含波动率",
                 theta:"theta 时间", residual:"残差"};
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
    s += txt(mid, H-PB+16, `${attr.high_residual} 笔残差过大，归因不可信`,
             {anchor:"middle", size:10.5, fill:"var(--warn)", mono:false});
  svg.innerHTML = s;
}

/* ── 历史情景表 ── */
function renderScenarios(rows){
  if(!rows || !rows.length){ $("scen").innerHTML = ""; return; }
  $("scen").innerHTML = `<thead><tr><th>情景</th><th>起</th><th>止</th><th>交易日</th>
      <th>区间收益</th><th>最大回撤</th><th style="text-align:left">备注</th></tr></thead><tbody>` +
    rows.map(r=>{
      const covered = r["sessions"] > 0;
      return `<tr><td>${r["scenario"]}</td><td>${r["from"]}</td><td>${r["to"]}</td>
        <td class="n">${r["sessions"]}</td>
        <td class="n ${covered ? sgn(r["return"]) : ""}">${covered ? pct(r["return"]) : "—"}</td>
        <td class="n ${covered ? "neg" : ""}">${covered ? pct(r["max drawdown"]) : "—"}</td>
        <td style="text-align:left;color:var(--ink-3)">${r["note"] || ""}</td></tr>`;
    }).join("") + `</tbody>`;
}

/* ── 权益指标条 ── */
function renderEquityStrip(m){
  if(!m){ $("equityStrip").innerHTML = ""; return; }
  const tiles = [
    ["总收益", pct(m["total_return"]), sgn(m["total_return"])],
    ["年化", pct(m["annualised"]), sgn(m["annualised"])],
    ["最大回撤", pct(m["max_drawdown"]), "neg"],
    ["夏普", m["sharpe"].toFixed(2), ""],
    ["年化波动", pct(m["annualised_vol"]), ""],
    ["平均资金占用", pct(m["mean_capital_used"]), ""],
  ];
  $("equityStrip").innerHTML = tiles.map(([k,v,cls]) =>
    `<div class="stat"><dt>${k}</dt><dd class="${cls}">${v}</dd></div>`).join("");
}
