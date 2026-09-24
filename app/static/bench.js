/* bench.js — 回测台的接线
 *
 * 职责：探测服务、收表单、发请求、存运行记录、把结果交给 charts.js 画。
 * 不含任何统计公式（在 core.js）和绘图代码（在 charts.js）。
 *
 * 一条贯穿全文的规矩：**不允许静默失败**。任何出错、任何被跳过的东西都经由
 * notice() 写到页面顶部的问题带——浏览器里看到一片空白却不知道为什么，
 * 比直接崩还糟。所以本文件没有空的 catch，也没有不声不响的提前返回。
 */
"use strict";

let META = null;          // /api/meta 的内容
let LIVE = false;         // 服务在不在
let RUNS = [];            // 跑过的每一次
let CURRENT = null;       // 当前查看哪一次
let BASE = null;          // 钉住的基准运行 id（= 服务端返回的 id 字段）
let RAW = [], VIEW = [];  // 当前这次的交易行（charts.js 读这两个）
let ask = null, ctl = null;

/* ── 问题带 ── */
const ISSUES = [];
function notice(msg){
  if(!ISSUES.includes(msg)) ISSUES.push(msg);
  paintIssues();
}
function clearIssues(){ ISSUES.length = 0; paintIssues(); }
function paintIssues(){
  const box = $("issues");
  box.hidden = ISSUES.length === 0;
  box.innerHTML = ISSUES.length
    ? `<b>${ISSUES.length} 条提示</b><ul>${ISSUES.map(m =>
        `<li>${String(m).replace(/</g,"&lt;").replace(/\n/g,"<br>")}</li>`).join("")}</ul>`
    : "";
}
window.addEventListener("error", e =>
  notice(`页面出错：${e.message}（${(e.filename||"").split("/").pop()}:${e.lineno}）`));
window.addEventListener("unhandledrejection", e =>
  notice(`异步出错：${e.reason?.message || e.reason}`));

/* ── 启动：探测服务 ── */
(async function boot(){
  try{
    const r = await fetch("/api/health");
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    LIVE = true;
  }catch(e){
    LIVE = false;
    $("mode").textContent = "离线 — 没连上本地服务，跑不了回测";
    $("mode").className = "mode offline";
    $("run").disabled = true;
    $("runHint").innerHTML = `先在终端启动服务：<br><code>/opt/anaconda3/bin/python3 app/server.py</code>`;
    notice(`没连上本地服务（${e.message}）。在项目目录下运行 `
      + `/opt/anaconda3/bin/python3 app/server.py，然后刷新本页。`);
    return;
  }
  $("mode").textContent = "已连上本地服务";
  $("mode").className = "mode live";
  await loadMeta();
})();

async function loadMeta(){
  try{
    const r = await fetch("/api/meta");
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    META = await r.json();
  }catch(e){
    notice(`取回可用信号失败：${e.message}。界面仍可用，但预设和提示会缺。`);
    return;
  }
  $("symbol").innerHTML = META.symbols.map(s =>
    `<option${s === META.defaults.symbol ? " selected" : ""}>${s}</option>`).join("");
  $("sigCount").textContent = `${(META.signal_docs || []).length} 个`;
  $("sigSearch").addEventListener("input", renderSignalRef);
  renderSignalRef();
  $("planCode").value    = META.defaults.plan_code;
  $("planHelpBody").textContent = META.plan_help;
  $("entryCode").value   = META.defaults.entry_code;
  $("signalsCode").value = META.defaults.signals_code;
  $("codeHelpBody").textContent = META.code_help;
  $("gapList").textContent = META.gaps.join("\n");
  updateModeHint();
  updateSizing();
  updateSpanHint();
}

/* ── 信号参考 ──────────────────────────────────────────
   以前这里是把信号名按字母表平铺成一排 code 标签，外加六个玩具策略预设
   （"sma_20 > sma_50" 之类）。两样都删了：
   - 一排名字没有信息量，使用者还是得去猜 rv_20 是按什么口径年化的
   - 教科书预设会暗示这是推荐用法，而它们不是任何人真会用的东西
   换成按类别分组、带含义与单位、可搜索、点一下插入光标处的参考面板。      */
function renderSignalRef(){
  const docs = META.signal_docs || [];
  if(!docs.length){ $("sigRef").innerHTML = ""; return; }
  const q = ($("sigSearch").value || "").trim().toLowerCase();
  const hit = d => !q || [d["信号"], d["量的是什么"], d["类别"]]
    .some(v => String(v).toLowerCase().includes(q));
  const groups = {};
  for(const d of docs.filter(hit)) (groups[d["类别"]] ??= []).push(d);

  if(!Object.keys(groups).length){
    $("sigRef").innerHTML = `<p class="hint">没有匹配 “${q}” 的信号。</p>`;
    return;
  }
  $("sigRef").innerHTML = Object.entries(groups).map(([fam, list]) => `
    <div class="sig-group">
      <div class="sig-fam">${fam}</div>
      ${list.map(d => `
        <button type="button" class="sig" data-name="${d["信号"]}"
                title="${d["注意"] || ""}">
          <code>${d["信号"]}</code>
          <span class="sig-what">${d["量的是什么"]}</span>
          <span class="sig-unit">${d["单位"]}</span>
          ${d["注意"] ? '<span class="sig-warn" aria-label="有注意事项">!</span>' : ""}
        </button>`).join("")}
    </div>`).join("");

  $("sigRef").querySelectorAll(".sig").forEach(b =>
    b.addEventListener("click", () => insertSignal(b.dataset.name)));
}

/* 插到当前正在编辑的那个框的光标处，而不是覆盖整行——覆盖会丢掉已经写好的条件。 */
function insertSignal(name){
  const codeMode = !$("entryCode").closest("[data-pane]").hidden;
  const el = codeMode ? $("entryCode") : $("entry");
  const a = el.selectionStart ?? el.value.length, b = el.selectionEnd ?? a;
  el.value = el.value.slice(0, a) + name + el.value.slice(b);
  el.focus();
  el.selectionStart = el.selectionEnd = a + name.length;
}

/* ── 表单 ── */
function updateSpanHint(){
  const f = $("form");
  const a = new Date(f.start.value), b = new Date(f.end.value);
  if(isNaN(a) || isNaN(b)) { $("spanHint").textContent = ""; return; }
  const years = (b - a) / 31557600000;
  if(years <= 0){ $("spanHint").textContent = "起始日晚于结束日，跑不了。"; return; }
  $("spanHint").textContent =
    `约 ${years.toFixed(1)} 年，预计 ${Math.max(3, Math.round(years * 4))} 秒。`
    + (years > 6 ? " 调参阶段建议先用短区间，定下来再跑全程。" : "");
}
["start","end"].forEach(n => $("form")[n].addEventListener("change", updateSpanHint));

$("engine").addEventListener("change", updateEngineHint);
function updateEngineHint(){
  const driven = $("engine").value === "event_driven";
  $("concurrentWrap").hidden = !driven;
  // 两个引擎跑同一套规则，笔数通常对不上——事件驱动受并发上限和现金约束，信号来了
  // 也可能开不了仓。这是对的，但看到"135 笔 vs 88 笔"的人第一反应是哪个引擎错了，
  // 所以必须在选引擎的地方就说清楚，而不是让人自己猜。
  $("engineHint").innerHTML = driven
    ? "逐日推进，知道任何时刻账上有多少钱。<b>有权益曲线、回撤、夏普</b>，慢一些。"
      + "<br>受并发上限和现金约束，<b>笔数通常少于成交表</b>——信号来了也可能开不了仓。"
    : "各笔独立计算，快两个数量级，适合扫参数。<b>没有权益曲线，因此没有回撤和夏普。</b>"
      + "<br>不受资金约束，枚举所有满足条件的机会，<b>笔数是上限</b>，不是实际能做到的。";
}
updateEngineHint();

$("strikeMode").addEventListener("change", e => {
  const byDelta = e.target.value === "delta";
  $("deltaWrap").hidden = !byDelta;
  $("moneyWrap").hidden = byDelta;
});
document.querySelectorAll("[data-strat]").forEach(tab =>
  tab.addEventListener("click", () => {
    document.querySelectorAll("[data-strat]").forEach(t => t.classList.toggle("on", t === tab));
    document.querySelectorAll("[data-strat-pane]").forEach(p =>
      p.hidden = p.dataset.stratPane !== tab.dataset.strat);
    updateModeHint();
  }));

document.querySelectorAll("[data-mode]").forEach(tab =>
  tab.addEventListener("click", () => {
    document.querySelectorAll("[data-mode]").forEach(t => t.classList.toggle("on", t === tab));
    document.querySelectorAll("[data-pane]").forEach(p =>
      p.hidden = p.dataset.pane !== tab.dataset.mode);
  }));
$("codeHelp").addEventListener("click", () => {
  const b = $("codeHelpBody"); b.hidden = !b.hidden;
});
/* ── ⑥ 仓位 ──────────────────────────────────────────────
   以前这里只有一个"每次张数"输入框，而且摆在"标的与区间"里——位置和语义都错了。
   固定张数是所有方法里最差的一个：权益翻倍之后同样 1 张只占一半风险，
   权益腰斩之后又变成两倍。它作为默认值只是因为最好懂，不是因为它对。   */
const SIZING_WHY = {
  risk_pct: "每笔亏光时正好亏掉权益的这个比例。买方最大损失就是权利金，所以这个数是确定的。",
  fixed: "最好懂，也最差：随着权益变化，每笔的风险占比会漂移，而你不会察觉。",
  fixed_notional: "投入固定，但风险不固定——便宜的合约会买到很多张。回测里容易爆仓。",
  vol_target: "标的波动大时少买。它控的是组合波动，不是单笔亏损。",
  kelly: "参数用回测期内已了结的交易估——实盘当下你不知道未来的胜率，所以结果偏乐观，只能当上界看。",
};
function updateSizing(){
  const m = $("sizingMethod").value;
  document.querySelectorAll("[data-sz]").forEach(el => { el.hidden = el.dataset.sz !== m; });
  $("sizingHint").textContent = SIZING_WHY[m] || "";
}
$("sizingMethod").addEventListener("change", updateSizing);

$("planHelp").addEventListener("click", () => {
  const b = $("planHelpBody"); b.hidden = !b.hidden;
});

/* 计划模式只能配事件驱动。与其让人点了跑回测才吃一个错误，不如当场说清楚，
   并且自动把引擎切过去——这不是"帮你做决定"，是这两个选项本来就绑死的。 */
function updateModeHint(){
  const planMode = document.querySelector("[data-strat].on")?.dataset.strat === "plan";
  // 组件本身**不隐藏**，只切换它显示哪一面。藏起来等于把使用者的组件地图弄丢了
  // ——实测就是这样：默认视图下七个组件有五个根本看不见。
  document.querySelectorAll("[data-comp]").forEach(fs => {
    fs.querySelector(".comp-plan").hidden   = !planMode;
    fs.querySelector(".comp-params").hidden = planMode;
    fs.classList.toggle("by-plan", planMode);
  });
  document.querySelectorAll('[data-strat-pane="params"]').forEach(el => { el.hidden = planMode; });
  if(planMode && $("engine").value !== "event_driven"){
    $("engine").value = "event_driven";
    $("engine").dispatchEvent(new Event("change"));
    notice("交易计划只能用事件驱动引擎，已替你切过去。");
  }
  $("engine").disabled = planMode;
  $("engineLock").hidden = !planMode;
  // 计划模式下仓位由计划自己给（risk_pct / notional / contracts），
  // 再摆一套参数框出来只会让人不知道哪个生效。
  updateSizing();
}

function formPayload(){
  const f = $("form"), o = {};
  for(const el of f.elements) if(el.name) o[el.name] = el.value;
  // 两层 tab：外层选策略模式（计划 / 参数），内层选入场写法（代码 / 表达式）。
  // 以前只有一个 .tab.on，现在必须分别取——取错会把计划当参数发出去，
  // 而后端收到的是一份"看起来完整"的参数，不会报错，只会跑出另一个策略的结果。
  o.strategy_mode = document.querySelector("[data-strat].on")?.dataset.strat || "params";
  // 只发当前方法真正用到的参数。全发的话后端分不清"没填"和"填了但不用这个方法"。
  const m = o.sizing_method || "risk_pct";
  o.sizing = { method: m, ...(
    m === "risk_pct"       ? { risk_pct: +o.risk_pct } :
    m === "fixed"          ? { contracts: +o.quantity } :
    m === "fixed_notional" ? { notional: +o.notional } :
    m === "vol_target"     ? { target_vol: +o.target_vol, risk_pct: +o.risk_pct_vt } :
                             { kelly_fraction: +o.kelly_fraction }) };
  o.entry_mode    = document.querySelector("[data-mode].on")?.dataset.mode  || "code";
  return o;
}

/* ── 跑 ── */
$("run").addEventListener("click", runBacktest);
async function runBacktest(){
  if(!LIVE){ notice("没连上本地服务，跑不了回测。"); return; }
  clearIssues();
  const btn = $("run");
  btn.disabled = true; btn.classList.add("busy"); btn.textContent = "跑着…";
  const note = document.createElement("div");
  note.className = "busy-note";
  note.innerHTML = `<span class="spin"></span><span>正在跑回测。第一次拉数据会慢些，之后走缓存。</span>`;
  $("issues").after(note);

  const t0 = performance.now();
  try{
    const r = await fetch("/api/run", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(formPayload()),
    });
    const data = await r.json().catch(() => ({error:`服务返回的不是 JSON（HTTP ${r.status}）`}));
    if(!r.ok || data.error){
      notice(data.detail ? `${data.error}\n\n${data.detail}` : (data.error || `HTTP ${r.status}`));
      return;
    }
    RUNS.push(data);
    BASE = BASE ?? data.id;
    show(data.id);
    if(!data.rows.length)
      notice("这次没有产生任何交易。入场条件可能太严，或者区间内选不到合约。");
  }catch(e){
    notice(`请求失败：${e.message}。服务可能已经停了——回终端看看。`);
  }finally{
    note.remove();
    btn.disabled = false; btn.classList.remove("busy"); btn.textContent = "跑回测";
    $("runHint").textContent = `上次用时 ${((performance.now()-t0)/1000).toFixed(1)} 秒。`;
  }
}

/* ── 本次运行的诊断 ────────────────────────────────────────
   与组件缺口的区别：这些每次都不一样，而且可查。只给个次数没用——
   "候选被丢弃 45 次"不告诉你该不该在意；要看到原因分布才知道是信号太严
   还是链上根本选不出合约。                                            */
const DIAG_LABEL = {
  candidate_dropped: "候选被丢弃",
  selection_failed:  "选不出合约",
  not_tradable:      "无法成交",
  quote_missing:     "盯市缺报价",
  range_truncated:   "区间被截断",
  fill_deferred:     "成交顺延",
};
function renderDiagnostics(diags){
  const box = $("diagSec");
  if(!box) return;
  box.hidden = !diags.length;
  if(!diags.length) return;
  $("diagBody").innerHTML = diags.map(d => `
    <details class="diag">
      <summary><span class="diag-n">${d.count.toLocaleString()}</span>
        ${DIAG_LABEL[d.kind] || d.kind}
        <span class="diag-span">${d.first} → ${d.last}</span></summary>
      <ul>${d.reasons.map(r =>
        `<li><span class="diag-n">${r.n}</span>${String(r.why).replace(/</g,"&lt;")}</li>`).join("")}</ul>
    </details>`).join("");
}

/* ── 运行记录 ── */
function cfgLine(c){
  const s = c.select, x = c.exit;
  const strike = s.delta != null ? `Δ${s.delta}` : `虚值${(s.moneyness*100).toFixed(0)}%`;
  const entry = typeof c.entry === "string" ? c.entry : "（自写代码）";
  const eng = c.engine === "event_driven" ? "事件驱动" : "成交表";
  return `[${eng}] ${entry} · ${s.dte}DTE ${strike} · TP${x.take_profit ?? "—"} `
       + `SL${x.stop_loss ?? "—"} 持有${x.hold_days ?? "—"}`;
}
function paintRuns(){
  $("runsSec").hidden = RUNS.length === 0;
  const base = RUNS.find(r => r.id === BASE);
  const baseSum = base ? stats(base.rows).sum : null;
  $("runs").innerHTML = `<thead><tr>
      <th>#</th><th style="text-align:left">配置</th><th>笔数</th><th>胜率</th>
      <th>净盈亏</th><th>平均每笔</th><th>基准</th></tr></thead><tbody>` +
    RUNS.map(r => {
      const s = stats(r.rows);
      const d = (baseSum !== null && r.id !== BASE) ? s.sum - baseSum : null;
      return `<tr data-run="${r.id}" class="${r.id === CURRENT ? "on" : ""}">
        <td class="n">${r.id}</td>
        <td><span class="cfg" title="${cfgLine(r.config)}">${cfgLine(r.config)}</span></td>
        <td class="n">${s.n}</td>
        <td class="n">${s.n ? pct(s.win) : "—"}</td>
        <td class="n ${sgn(s.sum)}">${s.n ? money(s.sum) : "—"}
          ${d !== null ? `<span class="delta ${sgn(d)}">${money(d)} 对基准</span>` : ""}</td>
        <td class="n ${sgn(s.mean)}">${s.n ? money(s.mean) : "—"}</td>
        <td><button class="pin ${r.id === BASE ? "on" : ""}" data-pin="${r.id}">
          ${r.id === BASE ? "基准" : "设为基准"}</button></td></tr>`;
    }).join("") + `</tbody>`;
  $("runs").querySelectorAll("tbody tr").forEach(tr =>
    tr.addEventListener("click", e => {
      if(e.target.dataset.pin) return;
      show(+tr.dataset.run);
    }));
  $("runs").querySelectorAll(".pin").forEach(b =>
    b.addEventListener("click", e => { e.stopPropagation(); BASE = +b.dataset.pin; paintRuns(); }));
}

/* ── 显示某一次 ── */
function show(runId){
  const run = RUNS.find(r => r.id === runId);
  if(!run){ notice(`找不到第 ${runId} 次运行的结果。`); return; }
  CURRENT = runId;
  RAW = run.rows;
  $("placeholder").hidden = true;
  $("result").hidden = false;
  paintRuns();

  const b = $("banner");
  // 硬边界：只有一条，就摆在数字上方。它决定这组数字能不能用。
  b.hidden = !run.boundary;
  if(run.boundary)
    b.innerHTML = `<b>这组数字的边界</b><span>${run.boundary.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")}</span>`;
  // 真出问题的才进提示条。组件缺口不进——它是静态的，位置在左边栏各层旁边。
  for(const m of (run.problems || [])) notice(m);
  renderDiagnostics(run.diagnostics || []);
  // 运行事件不再往提示条里塞一遍。它们已经在诊断面板里，而且那里有原因分布
  // 和日期范围——提示条里的"candidate_dropped：45 次"只是同一件事的劣化版本。
  // 提示条留给真正需要你立刻知道的：出错、以及这次没产生任何交易。

  fillDimSelects();
  apply();
}

function numericCols(rows){
  if(!rows.length) return [];
  return Object.keys(rows[0]).filter(c =>
    !EXCLUDE.has(c) && typeof rows[0][c] === "number" && Number.isFinite(rows[0][c]));
}
function fillDimSelects(){
  const cols = numericCols(RAW);
  if(!cols.length){ notice("这次的交易明细里没有可分组的数值列。"); return; }
  const keep = [$("dim").value, $("dim2").value];
  const opts = cols.map(c => `<option value="${c}">${label(c)}</option>`).join("");
  $("dim").innerHTML = opts;
  $("dim2").innerHTML = `<option value="">（不交叉）</option>` + opts;
  $("dim").value  = cols.includes(keep[0]) ? keep[0] : (cols.includes("entry_iv") ? "entry_iv" : cols[0]);
  $("dim2").value = cols.includes(keep[1]) ? keep[1] : "";
  const rs = [...new Set(RAW.map(r => r.exit_reason).filter(Boolean))].sort();
  $("reason").innerHTML = `<option value="">全部</option>` +
    rs.map(r => `<option value="${r}">${REASON[r] || r}</option>`).join("");
}

function apply(){
  const reason = $("reason").value;
  VIEW = reason ? RAW.filter(r => r.exit_reason === reason) : RAW;
  const col = $("dim").value, col2 = $("dim2").value, k = +$("bins").value;
  renderStrip(VIEW);
  if(!VIEW.length){
    for(const id of ["buckets","cross","robust","detail"]) $(id).innerHTML = "";
    for(const id of ["curve","hist","years","scatter"]) blankChart($(id), "当前筛选下没有交易");
    $("ab").innerHTML = `<div class="empty" style="grid-column:1/-1">当前筛选下没有交易</div>`;
    $("crossSec").hidden = true;
    notice(`按「${REASON[reason] || reason}」筛选后没有交易。`);
    return;
  }
  const run = RUNS.find(r => r.id === CURRENT);
  // 事件驱动独有的三块。成交表没有就藏起来，不显示一堆零。
  const hasEquity = !!(run && run.equity && run.equity.length);
  $("equitySec").hidden = !hasEquity;
  $("scenSec").hidden = !(run && run.scenarios && run.scenarios.length);
  if(hasEquity){ renderEquity(run.equity); renderEquityStrip(run.equity_metrics); }
  if(run && run.scenarios) renderScenarios(run.scenarios);
  $("attrSec").hidden = !(run && run.attribution);
  if(run && run.attribution) renderAttribution(run.attribution);

  renderCurve(VIEW, hasEquity); renderHist(VIEW); renderYears(VIEW); renderRobust(VIEW); renderDetail(VIEW);
  renderBuckets(VIEW, col, k); renderScatter(VIEW, col); renderAB(VIEW, col);
  renderCross(VIEW, col, col2, k);
  $("aiOut").textContent = "";
}
["dim","dim2","bins","reason"].forEach(id => $(id).addEventListener("change", apply));

/* ── AI 读一遍 ── */
function digest(rows, col, k){
  const run = RUNS.find(r => r.id === CURRENT);
  const s = stats(rows), B = bucketize(rows, col, k) || [];
  const by = {}; for(const r of rows){ const y = r.entry_date.slice(0,4); by[y] = (by[y]||0) + r.net_pnl; }
  const top5 = [...rows].sort((a,b)=>b.net_pnl-a.net_pnl).slice(0,5).reduce((a,r)=>a+r.net_pnl,0);
  const reasons = {};
  for(const r of rows){ const k2 = r.exit_reason || "?";
    (reasons[k2] = reasons[k2] || {n:0, sum:0}).n++; reasons[k2].sum += r.net_pnl; }
  const hold = rows.reduce((a,r)=>a+r.holding_days,0)/rows.length;
  const c = run.config;
  return [
    `策略：入场 ${typeof c.entry === "string" ? c.entry : "（用户自写代码）"} · `
      + `${c.select.dte}DTE · ${c.select.delta != null ? "delta "+c.select.delta : "虚值 "+c.select.moneyness}`
      + ` · 止盈 ${c.exit.take_profit} 止损 ${c.exit.stop_loss} 最多持有 ${c.exit.hold_days} 个交易日`,
    `标的与区间：${c.symbol} ${c.start} ~ ${c.end}`,
    `数据来源：${(run.boundary||"").includes("合成") ? "合成期权链（标的价真实，期权价由 Black-Scholes 生成）" : "真实数据"}`,
    ``,
    `整体：${s.n} 笔，胜率 ${pct(s.win)}，净盈亏 ${money(s.sum)}，平均每笔 ${money(s.mean)}，`
      + `中位 ${money(s.median)}，平均持有 ${hold.toFixed(1)} 个交易日`,
    `最大单笔盈 ${money(s.max)}，最大单笔亏 ${money(s.min)}，最赚的 5 笔合计 ${money(top5)}`,
    ``,
    `按了结原因：`,
    ...Object.entries(reasons).map(([k2,v]) =>
      `  ${REASON[k2]||k2}：${v.n} 笔，合计 ${money(v.sum)}，平均 ${money(v.sum/v.n)}`),
    ``,
    `按「${label(col)}」分 ${B.length} 档（等频）：`,
    ...B.map(b => { const t = stats(b.rows);
      return `  ${fmtVal(col,b.lo)} – ${fmtVal(col,b.hi)}：${t.n} 笔，胜率 ${pct(t.win)}，`
           + `平均 ${money(t.mean)}，中位 ${money(t.median)}`; }),
    ``,
    `逐年净盈亏：`,
    ...Object.keys(by).sort().map(y => `  ${y}：${money(by[y])}`),
  ].join("\n");
}
const PROMPT_RULES = `你在读一份期权策略回测的统计摘要，读者是策略作者本人，正在用回测决定策略参数。

用中文回答，不超过 220 字，分成三段，每段用「—— 小标题」开头：

—— 看到什么
最值得注意的一两个事实。具体数字，不要复述全部统计。

—— 值得追的线索
哪个方向值得进一步验证，为什么。如果分档呈单调关系，指出来；如果只是单点突出，说明那更可能是噪音。

—— 不能信的地方
样本量、集中度、数据来源带来的限制。如果数据是合成期权链，必须指出：关于期权本身的结论（选什么 delta、止盈设多少）不可信，只有基于标的价格的结论（何时进场）可信。

不要客套，不要建议"进一步研究"这种空话，直接说判断。`;

$("ask").addEventListener("click", async () => {
  if(!ask){ notice("AI 总结不可用，无法发起请求。"); return; }
  if(!VIEW.length){ notice("当前筛选下没有交易，没有东西可以让 Claude 读。"); return; }
  const out = $("aiOut"), btn = $("ask"), stop = $("stop");
  ctl = new AbortController();
  btn.disabled = true; stop.hidden = false;
  out.innerHTML = `<em>Claude 正在读…（首次回应通常要十几秒）</em>`;
  try{
    await ask(PROMPT_RULES + "\n\n===== 统计摘要 =====\n" + digest(VIEW, $("dim").value, +$("bins").value),
      { signal: ctl.signal, modelTier:"default", onText: ({text}) => { out.textContent = text; } });
  }catch(e){
    const msg = {cancelled:"已停止。", not_granted:"你拒绝了这次调用。",
      rate_limited:"调用太频繁，过一会儿再试。", prompt_too_large:"数据太多，先缩小筛选范围。",
      refused:"这次请求被拒绝了。", session_expired:"登录状态过期，刷新页面再试。"}[e.code];
    if(e.code === "not_granted"){
      out.textContent = "";
      document.querySelector(".ai").hidden = true;
      notice("你拒绝了 AI 总结的授权，该功能已在本次会话中隐藏。");
      return;
    }
    const shown = msg || `出错了（${e.code}）：${e.message}`;
    out.textContent = e.text || "";
    out.innerHTML += (e.text ? "\n\n" : "") + `<em>${shown}</em>`;
    if(e.code !== "cancelled") notice(`AI 总结失败：${shown}`);
  }finally{ btn.disabled = false; stop.hidden = true; ctl = null; }
});
$("stop").addEventListener("click", () => ctl?.abort());

(async () => {
  if(!window.claude?.use){
    document.querySelector(".ai").hidden = true;
    return;   // 本地打开时没有 Claude 运行时，这是预期状态，不算问题
  }
  try{ ask = await window.claude.use("sample"); }
  catch(e){ ask = null; notice(`AI 总结探测失败：${e?.message || e}`); }
  if(!ask) document.querySelector(".ai").hidden = true;
})();
