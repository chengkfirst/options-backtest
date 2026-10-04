/* bench.js — the bench's wiring
 *
 * Its job: probe the service, collect the form, send the request, keep the run records,
 * and hand results to charts.js. It contains no statistical formula (those are in
 * core.js) and no drawing code (that is in charts.js).
 *
 * One rule runs through the whole file: **no silent failure**. Every error and every
 * skipped thing goes through notice() into the problem strip at the top of the page —
 * seeing a blank in the browser without knowing why is worse than a crash. So there is
 * no empty catch here, and no quiet early return.
 */
"use strict";

let META = null;          // the contents of /api/meta
let LIVE = false;         // is the service up
let RUNS = [];            // every run so far
let CURRENT = null;       // which one is being viewed
let BASE = null;          // the pinned baseline run id (the server's `id` field)
let RAW = [], VIEW = [];  // this run's trade rows (charts.js reads these two)
let ask = null, ctl = null;

/* ── the problem strip ── */
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
    ? `<b>${ISSUES.length} notice${ISSUES.length>1?"s":""}</b><ul>${ISSUES.map(m =>
        `<li>${String(m).replace(/</g,"&lt;").replace(/\n/g,"<br>")}</li>`).join("")}</ul>`
    : "";
}
window.addEventListener("error", e =>
  notice(`page error: ${e.message} (${(e.filename||"").split("/").pop()}:${e.lineno})`));
window.addEventListener("unhandledrejection", e =>
  notice(`async error: ${e.reason?.message || e.reason}`));

/* ── startup: probe the service ── */
(async function boot(){
  try{
    const r = await fetch("/api/health");
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    LIVE = true;
  }catch(e){
    LIVE = false;
    $("mode").textContent = "offline — no local service, so nothing can be run";
    $("mode").className = "mode offline";
    $("run").disabled = true;
    $("runHint").innerHTML = `Start the service in a terminal first:<br><code>/opt/anaconda3/bin/python3 app/server.py</code>`;
    notice(`could not reach the local service (${e.message}). Run `
      + `/opt/anaconda3/bin/python3 app/server.py in the project directory, then reload.`);
    return;
  }
  $("mode").textContent = "connected to the local service";
  $("mode").className = "mode live";
  await loadMeta();
})();

async function loadMeta(){
  try{
    const r = await fetch("/api/meta");
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    META = await r.json();
  }catch(e){
    notice(`could not fetch the available signals: ${e.message}. The interface still works, but the defaults and hints will be missing.`);
    return;
  }
  $("symbol").innerHTML = META.symbols.map(s =>
    `<option${s === META.defaults.symbol ? " selected" : ""}>${s}</option>`).join("");
  $("sigCount").textContent = `${(META.signal_docs || []).length}`;
  $("sigSearch").addEventListener("input", renderSignalRef);
  renderSignalRef();
  $("planCode").value = META.defaults.plan_code;
  $("planHelpBody").textContent = META.plan_help;
  $("signalsCode").value = META.defaults.signals_code;
  switchMode();                     // generate the seven components' forms
  fillDefaults();
  updateSpanHint();
}

/* Fill the generated controls with the backend's defaults. The code fields
   (entry_code / review_code) get their templates here too — their default content lives
   in the backend, not in the HTML. */
function fillDefaults(){
  const d = META.defaults || {};
  for(const [k, v] of Object.entries(d)){
    const el = $("f_" + k);
    if(el && v != null && el.value === "") el.value = v;
    else if(el && el.tagName !== "TEXTAREA" && v != null) el.value = v;
  }
  if($("f_entry_code") && !$("f_entry_code").value) $("f_entry_code").value = d.entry_code || "";
  if($("f_review_code") && !$("f_review_code").value) $("f_review_code").value = d.review_code || "";
  bindFields();
}

/* ── the signal reference ──────────────────────────────
   Grouped by family, with meanings and units, searchable, and a click inserts at the
   cursor. Names alone carry no information — the user would still have to guess what
   convention rv_20 is annualised on. */
function renderSignalRef(){
  const docs = META.signal_docs || [];
  if(!docs.length){ $("sigRef").innerHTML = ""; return; }
  const q = ($("sigSearch").value || "").trim().toLowerCase();
  const hit = d => !q || [d["signal"], d["measures"], d["family"]]
    .some(v => String(v).toLowerCase().includes(q));
  const groups = {};
  for(const d of docs.filter(hit)) (groups[d["family"]] ??= []).push(d);

  if(!Object.keys(groups).length){
    $("sigRef").innerHTML = `<p class="hint">No signal matches “${q}”.</p>`;
    return;
  }
  $("sigRef").innerHTML = Object.entries(groups).map(([fam, list]) => `
    <div class="sig-group">
      <div class="sig-fam">${fam}</div>
      ${list.map(d => `
        <button type="button" class="sig" data-name="${d["signal"]}"
                title="${d["caveat"] || ""}">
          <code>${d["signal"]}</code>
          <span class="sig-what">${d["measures"]}</span>
          <span class="sig-unit">${d["unit"]}</span>
          ${d["caveat"] ? '<span class="sig-warn" aria-label="has a caveat">!</span>' : ""}
        </button>`).join("")}
    </div>`).join("");

  $("sigRef").querySelectorAll(".sig").forEach(b =>
    b.addEventListener("click", () => insertSignal(b.dataset.name)));
}

/* Insert at the cursor of whichever box is being edited, rather than replacing the
   line — replacing would discard a condition already written. */
function insertSignal(name){
  // Into whichever box has focus. The form is generated, so this follows focus rather
  // than a fixed id — a fixed id would point at an element that no longer exists after
  // the components change.
  const el = document.activeElement?.matches?.("textarea, input[type=text]")
    ? document.activeElement
    : ($("f_entry_code") || $("planCode") || $("f_entry"));
  if(!el) return;
  const a = el.selectionStart ?? el.value.length, b = el.selectionEnd ?? a;
  el.value = el.value.slice(0, a) + name + el.value.slice(b);
  el.focus();
  el.selectionStart = el.selectionEnd = a + name.length;
}

/* ── the seven components' forms, generated from the backend definition ───────
   What a component is and what it asks for is defined once, in
   backtest/components.py. The interface draws from it rather than hand-coding — two
   definitions always drift. */
const STATUS_MARK = { done: "", partial: "partial", missing: "not built" };

function renderComponents(){
  const planMode = strategyMode() === "plan";
  $("comps").innerHTML = (META.form || []).map(L => `
    <section class="layer">
      <header class="layer-head">
        <span class="layer-n" title="${esc(L.what)}">${L.layer}</span>
      </header>
      ${L.components.map(c => compBlock(c, planMode)).join("")}
    </section>`).join("");
  bindFields();
}

function compBlock(c, planMode){
  // The interface gives controls and values. Reasoning like "what this component
  // decides in a trade" or "why the stop and the size fight" belongs in the docs and
  // the code comments — repeated on every open it gets skipped after twice, and it
  // squeezes out the numbers that matter. Hover for the title when needed.
  const taken = planMode && c.in_plan;
  const body = taken
    ? `<p class="comp-note">→ <code>${c.in_plan}</code></p>`
    : c.inputs.length
      ? `<div class="fields">${c.inputs.map(fieldHtml).join("")}</div>`
      : `<p class="comp-note">${esc(shortGap(c))}</p>`;
  const badge = STATUS_MARK[c.status]
    ? `<span class="comp-badge ${c.status}">${STATUS_MARK[c.status]}</span>` : "";
  return `<article class="comp ${taken ? "by-plan" : ""}" data-comp="${c.key}">
      <h4 class="comp-h" title="${esc(c.role)}">${c.name}${badge}</h4>
      ${body}
    </article>`;
}

/* A component with no controls says one line about why — one line, not a paragraph.
   What is missing and how to find it lives in requirements.md and components.py. */
function shortGap(c){
  const g = (c.gap || "").replace(/\*\*/g, "");
  const first = g.split(/[;.]/)[0] || "nothing configurable yet";
  return first.length > 52 ? first.slice(0, 52) + "…" : first;
}

function fieldHtml(f){
  const id = "f_" + f.name;
  const common = `id="${id}" name="${f.name}"` + (f.help ? ` title="${esc(f.help)}"` : "");
  let ctl;
  if(f.kind === "select"){
    ctl = `<select ${common}>${f.options.map(([v, t]) =>
      `<option value="${v}"${v === f.default ? " selected" : ""}>${t}</option>`).join("")}</select>`;
  }else if(f.kind === "code"){
    ctl = `<textarea ${common} rows="6" spellcheck="false" class="code"></textarea>`;
  }else if(f.kind === "text"){
    ctl = `<input type="text" ${common} value="${f.default ?? ""}" spellcheck="false" class="mono">`;
  }else{
    const step = f.kind === "int" ? 1 : (f.kind === "ratio" ? 0.05 : 100);
    ctl = `<input type="number" ${common} value="${f.default ?? ""}" step="${step}"`
        + (f.lo != null ? ` min="${f.lo}"` : "") + (f.hi != null ? ` max="${f.hi}"` : "") + `>`;
  }
  const wide = f.kind === "code" || f.kind === "text";
  return `<div class="field${wide ? " wide" : ""}"${f.depends ? ` data-depends="${f.depends}"` : ""}>
      <label for="${id}"${f.help ? ` title="${esc(f.help)}"` : ""}>${f.label}${
        f.unit ? ` <span class="unit">${esc(f.unit)}</span>` : ""}</label>
      ${ctl}
    </div>`;
}

/* depends: show only the fields the current values make use of. Showing all of them
   leaves the backend unable to tell "not filled in" from "filled in but this method does
   not use it". */
function bindFields(){
  const apply = () => {
    document.querySelectorAll("[data-depends]").forEach(el => {
      const [name, want] = el.dataset.depends.split("=");
      const src = $("f_" + name);
      el.hidden = !src || String(src.value) !== want;
    });
  };
  $("comps").querySelectorAll("select").forEach(sel =>
    sel.addEventListener("change", apply));
  apply();
}

function strategyMode(){
  return document.querySelector("[data-strat].on")?.dataset.strat || "params";
}

function switchMode(){
  const planMode = strategyMode() === "plan";
  $("planWrap").hidden = !planMode;
  $("engineLock").hidden = !planMode;
  $("engine").disabled = planMode;
  if(planMode && $("engine").value !== "event_driven"){
    $("engine").value = "event_driven";
    $("engine").dispatchEvent(new Event("change"));
  }
  renderComponents();
}
document.querySelectorAll("[data-strat]").forEach(tab =>
  tab.addEventListener("click", () => {
    document.querySelectorAll("[data-strat]").forEach(t => t.classList.toggle("on", t === tab));
    switchMode();
  }));

const esc = t => String(t).replace(/</g, "&lt;");
const mark = t => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");

/* ── the form ── */
function updateSpanHint(){
  const f = $("form");
  const a = new Date(f.start.value), b = new Date(f.end.value);
  if(isNaN(a) || isNaN(b)) { $("spanHint").textContent = ""; return; }
  const years = (b - a) / 31557600000;
  if(years <= 0){ $("spanHint").textContent = "the start is after the end, so this cannot run."; return; }
  $("spanHint").textContent =
    `about ${years.toFixed(1)} years, roughly ${Math.max(3, Math.round(years * 4))} seconds.`
    + (years > 6 ? " While tuning, use a shorter range first and run the whole period once it is settled." : "");
}
["start","end"].forEach(n => $("form")[n].addEventListener("change", updateSpanHint));

$("engine").addEventListener("change", updateEngineHint);
function updateEngineHint(){
  const driven = $("engine").value === "event_driven";
  $("concurrentWrap").hidden = !driven;
  // The two engines run the same rules and usually disagree on the trade count — the
  // event-driven one is bound by the concurrency limit and by cash, so a signal may
  // arrive with no room to open. That is correct, but the first reaction to "135 vs 88"
  // is to wonder which engine is wrong, so it has to be said where the engine is chosen
  // rather than left to be guessed.
  $("engineHint").innerHTML = driven
    ? "Steps day by day and knows how much money is in the account at any moment. <b>Has an equity curve, drawdown and Sharpe</b>, and is slower."
      + "<br>Bound by the concurrency limit and by cash, so its <b>trade count is usually below the trade table's</b> — a signal can arrive with no room to open."
    : "Computes each trade independently, two orders of magnitude faster, and good for sweeps. <b>No equity curve, so no drawdown and no Sharpe.</b>"
      + "<br>Unconstrained by capital, it enumerates every qualifying opportunity, so its <b>trade count is an upper bound</b>, not what could actually be done.";
}
updateEngineHint();




$("planHelp").addEventListener("click", () => {
  const b = $("planHelpBody"); b.hidden = !b.hidden;
});

/* Plan mode only works with the event-driven engine. Rather than letting someone hit
   an error after pressing run, say so at once and switch the engine — this is not
   deciding for the user, these two options were always bound together. */

function formPayload(){
  const f = $("form"), o = {};
  for(const el of f.elements) if(el.name) o[el.name] = el.value;
  o.strategy_mode = strategyMode();
  // entry_mode is a real field on the form, so this no longer guesses from whether the
  // code box is empty.
  // Sizing: send only the parameters the current method uses. Sending all of them leaves
  // the backend unable to tell "not filled in" from "not used".
  const m = CONTRACT.sizing_methods.includes(o.sizing_method) ? o.sizing_method : "risk_pct";
  o.sizing = { method: m, ...(
    m === "risk_pct"       ? { risk_pct: +o.risk_pct } :
    m === "fixed"          ? { contracts: +o.quantity } :
    m === "fixed_notional" ? { notional: +o.notional } :
                             { target_vol: +o.target_vol, risk_pct: +o.risk_pct }) };
  return o;
}


/* ── running ── */
$("run").addEventListener("click", runBacktest);
async function runBacktest(){
  if(!LIVE){ notice("no local service, so nothing can be run."); return; }
  clearIssues();
  const btn = $("run");
  btn.disabled = true; btn.classList.add("busy"); btn.textContent = "running…";
  const note = document.createElement("div");
  note.className = "busy-note";
  note.innerHTML = `<span class="spin"></span><span>Running. The first fetch is slower; after that it is cached.</span>`;
  $("issues").after(note);

  const t0 = performance.now();
  try{
    const r = await fetch("/api/run", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(formPayload()),
    });
    const data = await r.json().catch(() => ({error:`the service's response was not JSON (HTTP ${r.status})`}));
    if(!r.ok || data.error){
      notice(data.detail ? `${data.error}\n\n${data.detail}` : (data.error || `HTTP ${r.status}`));
      return;
    }
    checkContract(data);
    RUNS.push(data);
    BASE = BASE ?? data.id;
    show(data.id);
    if(!data.rows.length)
      notice("this run produced no trades. The entry condition may be too strict, or nothing in the range could be selected.");
  }catch(e){
    notice(`the request failed: ${e.message}. The service may have stopped — check the terminal.`);
  }finally{
    note.remove();
    btn.disabled = false; btn.classList.remove("busy"); btn.textContent = "run";
    $("runHint").textContent = `last run took ${((performance.now()-t0)/1000).toFixed(1)} seconds.`;
  }
}

/* ── the contract-selection reference ─────────────────────
   The shape of four quantities over the moneyness x tenor surface is the substance of
   the intuition about choosing a contract. Without seeing the shape there is only
   memory. The numbers come from that day's real chain, not a static formula table. */
const TRADEOFF_GRIDS = [
  {key:"theta_pct_day",  title:"premium burnt per day",  fmt:v=>(v*100).toFixed(1)+"%",
   why:"the cost of the underlying not moving; short-dated and out of the money is a cliff", worse:"high"},
  {key:"breakeven_move", title:"move needed to break even", fmt:v=>(v*100).toFixed(1)+"%",
   why:"in the direction of the thesis; a smaller thesis cannot carry this contract", worse:"high"},
  {key:"delta_now",      title:"delta now",       fmt:v=>v.toFixed(2),
   why:"the directional exposure at entry",       worse:null},
  {key:"delta_if_move",  title:"delta after 3% and ten days", fmt:v=>v.toFixed(2),
   why:"what the exposure becomes once the thesis plays out <- more important than the column to its left", worse:null},
];

async function loadTradeoff(){
  const f = $("form");
  const url = `/api/tradeoff?symbol=${encodeURIComponent(f.symbol.value)}`
            + `&date=${encodeURIComponent(f.end.value)}`
            + `&right=${(f.right?.value || "call").toUpperCase()[0]}`;
  $("tradeoffBody").innerHTML = `<p class="hint">computing…</p>`;
  try{
    const r = await fetch(url);
    const d = await r.json();
    if(d.error){ $("tradeoffBody").innerHTML = `<p class="hint">${esc(d.error)}</p>`; return; }
    $("toWhen").textContent = `${d.day} · ${d.symbol} ${d.spot.toFixed(2)}`;
    $("tradeoffBody").innerHTML = TRADEOFF_GRIDS.map(g =>
      gridTable(g, d.grids[g.key])).join("");
  }catch(e){
    $("tradeoffBody").innerHTML = `<p class="hint">could not fetch it: ${esc(e.message)}</p>`;
  }
}

function gridTable(meta, g){
  if(!g || !g.rows.length) return "";
  // Colour only encodes quantities where larger is worse. The delta columns are not
  // shaded — larger is neither good nor bad there, and shading would imply a judgement
  // that does not exist.
  const flat = g.rows.flatMap(r => r.values).filter(v => v != null);
  const lo = Math.min(...flat), hi = Math.max(...flat);
  const shade = v => {
    if(meta.worse !== "high" || v == null || hi === lo) return "";
    const t = (v - lo) / (hi - lo);
    return ` style="background:rgba(156,74,52,${(t*0.28).toFixed(3)})"`;
  };
  return `<div class="to-grid">
    <div class="to-h">${meta.title}<span class="to-why">${meta.why}</span></div>
    <table><thead><tr><th>moneyness</th>${g.cols.map(c=>`<th>${c}</th>`).join("")}</tr></thead>
    <tbody>${g.rows.map(r => `<tr><th>${r.label}</th>${
      r.values.map(v => `<td${shade(v)}>${v == null ? "—" : meta.fmt(v)}</td>`).join("")
    }</tr>`).join("")}</tbody></table></div>`;
}

$("tradeoffRef").addEventListener("toggle", e => {
  if(e.target.open && !$("toWhen").textContent) loadTradeoff();
});

/* ── the anomaly scan ─────────────────────────────────────
   Found by rules, not by asking a model to look. Nothing triggered hides the whole
   block — an insight forced out of nothing is noise, and noise makes real warnings get
   skipped. */
function renderAnomalies(found){
  $("anomSec").hidden = !found.length;
  if(!found.length) return;
  $("anomBody").innerHTML = found.map(f => `
    <div class="anom ${f.severity}">
      <div class="anom-h">${esc(f.headline)}</div>
      <div class="anom-d">${esc(f.detail)}</div>
    </div>`).join("");
}

/* ── this run's diagnostics ───────────────────────────────
   Unlike the component gaps, these differ every run and can be inspected. A count alone
   is useless — "45 candidates dropped" does not say whether to care; the distribution of
   reasons is what distinguishes a signal that is too strict from a chain that holds
   nothing qualifying. */
const DIAG_LABEL = {
  candidate_dropped: "candidate dropped",
  selection_failed:  "nothing selectable",
  not_tradable:      "untradable",
  quote_missing:     "no quote to mark against",
  range_truncated:   "range truncated",
  fill_deferred:     "fill deferred",
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


/* ── validate this run ────────────────────────────────────
   The four tools from the research layer, run on demand. On demand because each one
   re-runs the strategy — ablation once per slot, baselines once per draw — so running
   them with every backtest would make the run button slow for something not always
   wanted.

   The buttons are rendered from META.validations, which the backend builds from the
   runner's own declaration. A tool added there appears here with no edit on this side;
   that is the only arrangement in which the two cannot drift apart.

   The output is shown as text, deliberately. Every one of these reports leads with the
   boundary on its own conclusions — on the synthetic chain an exit level describes
   Black-Scholes, not the market. Splitting the report into fields for this side to
   re-assemble is exactly how that line gets dropped. */
const VALID_INPUTS = {
  baselines: [{key:"draws", label:"random draws", value:50, hint:"at least 10"}],
  counterfactual: [
    {key:"take_profits", label:"take profits", value:"0.5, 1.0, 1.5", text:true},
    {key:"stop_losses",  label:"stop losses",  value:"0.3, 0.5, 0.7", text:true},
  ],
  trials: [{key:"years", label:"years covered", value:"", hint:"the stored Sharpe is annualised"}],
  ablation: [],
};

function renderValidations(run){
  const box = $("validSec");
  if(!box) return;
  // META is null when /api/meta could not be fetched; that failure is already in the
  // problem strip, so this hides the section rather than reporting it twice.
  const tools = (META && META.validations) || [];
  const offered = new Set(run.validations || []);
  box.hidden = !tools.length;
  $("validOut").hidden = true;
  $("validTools").innerHTML = tools.map(t => {
    const can = offered.has(t.kind);
    const inputs = (VALID_INPUTS[t.kind] || []).map(i =>
      `<label for="v_${t.kind}_${i.key}">${esc(i.label)}</label>
       <input id="v_${t.kind}_${i.key}" value="${esc(i.value)}"
              ${i.hint ? `title="${esc(i.hint)}"` : ""}>`).join("");
    const action = can
      ? `<div class="vrow">${inputs}
           <button class="btn ghost" data-valid="${t.kind}">run</button></div>`
      : `<div class="unavail">needs the ${(t.engines || []).join(" or ")} engine;
           this run used ${esc(run.engine)}</div>`;
    return `<div class="valid-tool">
      <h4>${esc(t.label)}</h4><p>${esc(t.why)}</p>${action}</div>`;
  }).join("");
  $("validTools").querySelectorAll("[data-valid]").forEach(b =>
    b.addEventListener("click", () => runValidation(b.dataset.valid, b)));
}

function validParams(kind){
  const out = {};
  for(const i of (VALID_INPUTS[kind] || [])){
    const el = $(`v_${kind}_${i.key}`);
    if(!el) continue;
    const raw = el.value.trim();
    if(!raw) continue;
    out[i.key] = i.text
      ? raw.split(",").map(x => x.trim()).filter(Boolean).map(Number)
      : Number(raw);
  }
  return out;
}

async function runValidation(kind, btn){
  const out = $("validOut");
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = "running…";
  out.hidden = false;
  out.textContent = "Running. This re-runs the strategy, so it takes about as long as the backtest did.";
  try{
    const r = await fetch("/api/validate", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({id: CURRENT, kind, params: validParams(kind)}),
    });
    const data = await r.json().catch(() => ({error:`the service's response was not JSON (HTTP ${r.status})`}));
    if(!r.ok || data.error){
      // A refusal belongs where the report would have gone, not in the problem strip:
      // "the counterfactual needs its levels" is an instruction, not a fault.
      out.textContent = data.error || `HTTP ${r.status}`;
      return;
    }
    out.innerHTML = esc(data.text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  }catch(e){
    out.textContent = `the request failed: ${e.message}. The service may have stopped — check the terminal.`;
  }finally{
    btn.disabled = false; btn.textContent = was;
  }
}

/* ── the front/back contract ──────────────────────────────
   Which fields the interface needs from the backend is **declared explicitly** here, and
   both sides use the declaration:

   - at runtime: the response is checked against it and anything missing is reported at
     once, rather than letting undefined seep into the render functions (when the backend
     returned `id` and the front end read `run_id`, RUNS.find() "matched" because both
     sides were undefined, and it still looked like it worked)
   - before committing: tools/check_wiring.py checks the declaration against a real
     response

   Scanning the JS source to guess field names is unreliable (it misses forms like
   RUNS[0].x), hence the declaration.

   A path may be **nested**, written with dots, and `*` descends into every element of a
   list: `config.select.dte_lo`, `rows.*.net_pnl`, `scenarios.*.max drawdown`. Top-level
   keys alone were not enough — `cfgLine` read `config.select.dte` for weeks after the
   backend renamed it to `dte_lo`/`dte_hi`, the run history printed "undefinedDTE", and
   the guard stayed green because `config` itself was present. A field the interface
   reads is declared at the depth it is read. */
const CONTRACT = {
  run: ["id", "rows", "config", "engine", "started", "seconds",
        "n_trades", "win_rate", "net_pnl", "boundary", "problems",
        "diagnostics", "anomalies", "manifest", "events", "synthetic",
        // Which validation tools this run can answer. Declared per run rather than
        // assumed, because the counterfactual needs the shadow prices only the
        // event-driven engine keeps.
        "validations",
        // What `describeConfig` reads to say what ran. The selection fields are a
        // feasible region, not one pinned point, so the guard's sample run fills every
        // one of them in — otherwise "absent because nobody asked for it" and "absent
        // because it was renamed" look the same and neither fails.
        "config.symbol", "config.start", "config.end", "config.entry", "config.engine",
        "config.select.right", "config.select.dte_lo", "config.select.dte_hi",
        "config.select.delta_lo", "config.select.delta_hi", "config.select.delta",
        "config.select.max_spread", "config.select.min_open_interest",
        "config.exit.take_profit", "config.exit.stop_loss", "config.exit.hold_days",
        "config.exit.dte_floor", "config.sizing.method",
        // The trade columns every chart and table reads. The column allowlist in
        // runner.py has dropped one of these before, and every group then came out empty.
        "rows.*.net_pnl", "rows.*.gross_pnl", "rows.*.fees", "rows.*.entry_date",
        "rows.*.exit_date", "rows.*.exit_reason", "rows.*.holding_days",
        "rows.*.contract", "rows.*.entry_price", "rows.*.exit_price",
        "rows.*.entry_iv", "rows.*.entry_delta",
        "diagnostics.*.kind", "diagnostics.*.count", "diagnostics.*.first",
        "diagnostics.*.last", "diagnostics.*.reasons"],
  // Only the event-driven engine has these; their absence under the trade table is normal
  run_optional: ["equity", "equity_metrics", "scenarios", "attribution",
                 "equity.*.date", "equity.*.equity_mid", "equity.*.equity_liq",
                 "equity_metrics.total_return", "equity_metrics.annualised",
                 "equity_metrics.max_drawdown", "equity_metrics.sharpe",
                 "equity_metrics.annualised_vol", "equity_metrics.mean_capital_used",
                 "attribution.totals", "attribution.high_residual",
                 "scenarios.*.scenario", "scenarios.*.from", "scenarios.*.to",
                 "scenarios.*.sessions", "scenarios.*.return",
                 "scenarios.*.max drawdown", "scenarios.*.note"],
  meta: ["symbols", "defaults", "form", "signal_docs", "plan_help", "code_help", "gaps",
         "validations"],
  sizing_methods: ["fixed", "risk_pct", "fixed_notional", "scale_by_vol"],
};

/* Resolve one declared path against a response.
   Returns "ok", "missing" or "empty" — and "empty" is **not** a failure: a list with no
   elements (no anomaly fired, no scenario covered) cannot show whether its fields are
   there, and reporting that as missing would cry wolf on every quiet run. */
function resolvePath(obj, path){
  let node = obj;
  for(const part of path.split(".")){
    if(node == null) return "missing";
    if(part === "*"){
      if(!Array.isArray(node)) return "missing";
      if(!node.length) return "empty";
      node = node[0];
      continue;
    }
    if(typeof node !== "object" || !(part in node)) return "missing";
    node = node[part];
  }
  return "ok";
}

function checkContract(record){
  const missing = CONTRACT.run.filter(p => resolvePath(record, p) === "missing");
  if(missing.length)
    notice(`the backend response is missing fields: ${missing.join(", ")}. Something on the interface will not render — the two sides are out of step.`);
  // The event-driven-only fields are checked only when the section they feed is present.
  // Under the trade table their absence is how the interface knows to hide the section.
  const partial = CONTRACT.run_optional.filter(p => {
    const root = p.split(".")[0];
    return record[root] != null && resolvePath(record, p) === "missing";
  });
  if(partial.length)
    notice(`the backend sent ${partial.map(p => p.split(".")[0]).filter((v, i, a) => a.indexOf(v) === i).join(", ")} without: ${partial.join(", ")}. That section will render incomplete.`);
}

/* ── describing a configuration ───────────────────────────
   **One reading of the stored config, used by every place that describes a run.**

   There were two, and both had gone stale: each read `select.dte` and
   `select.moneyness`, neither of which the backend writes — selection has been a
   feasible region (`dte_lo`/`dte_hi`, a delta band, a ranking target) for some time. The
   run history printed "undefinedDTE" and the same string was handed to Claude as the
   strategy's description. Nothing raised, because reading an absent field is not an
   error in JS; it just renders.

   Absence is shown as "—" and never as `undefined`, and the field names are declared in
   CONTRACT so that check_wiring fails on the next rename instead of the screen. */
function describeConfig(c){
  const s = c.select || {}, x = c.exit || {};
  const band = (lo, hi, fmt = v => v) =>
    lo != null && hi != null ? `${fmt(lo)}–${fmt(hi)}`
      : lo != null ? `≥${fmt(lo)}` : hi != null ? `≤${fmt(hi)}` : null;
  // The target delta is the ranking objective inside the band; both can be present, and
  // showing only one of them hides half of how the contract was chosen.
  const deltaBand = band(s.delta_lo, s.delta_hi, v => Number(v).toFixed(2));
  const strike = [deltaBand ? `Δ${deltaBand}` : null,
                  s.delta != null ? `→Δ${Number(s.delta).toFixed(2)}` : null,
                  s.moneyness != null ? `${(s.moneyness*100).toFixed(0)}% OTM` : null]
                 .filter(Boolean).join(" ") || "any strike";
  return {
    engine: c.engine === "event_driven" ? "event driven" : "trade table",
    entry: typeof c.entry === "string" ? c.entry
      : c.plan_code ? "(a plan function)" : "(custom code)",
    right: s.right || "—",
    dte: band(s.dte_lo, s.dte_hi) ? `${band(s.dte_lo, s.dte_hi)} DTE` : "any tenor",
    strike,
    // Both are real constraints on what could be bought, and in live trading the spread
    // is often the binding one — worth a place in the one-line summary.
    spread: s.max_spread != null ? `spread ≤${s.max_spread}` : null,
    openInterest: s.min_open_interest != null ? `OI ≥${s.min_open_interest}` : null,
    takeProfit: x.take_profit ?? null,
    stopLoss: x.stop_loss ?? null,
    holdDays: x.hold_days ?? null,
    dteFloor: x.dte_floor ?? null,
    sizing: c.sizing?.method || null,
    // A slot filled by a registered form decides that slot instead of the matching exit
    // field, so a summary that omits it describes a different run from the one that ran.
    components: Object.entries(c.components || {}).map(([slot, one]) =>
      `${slot}=${one.variant}${one.version != null ? "@" + one.version : ""}`),
    risk: c.risk?.variant ? `risk=${c.risk.variant}` : null,
  };
}

/* ── the run history ── */
function cfgLine(c){
  const d = describeConfig(c);
  const num = v => v == null ? "—" : v;
  return [`[${d.engine}] ${d.entry}`,
          `${d.right} ${d.dte} ${d.strike}`,
          d.spread, d.openInterest,
          `TP${num(d.takeProfit)} SL${num(d.stopLoss)} hold${num(d.holdDays)}`,
          ...d.components, d.risk].filter(Boolean).join(" · ");
}
function paintRuns(){
  $("runsSec").hidden = RUNS.length === 0;
  const base = RUNS.find(r => r.id === BASE);
  const baseSum = base ? stats(base.rows).sum : null;
  $("runs").innerHTML = `<thead><tr>
      <th>#</th><th style="text-align:left">configuration</th><th>trades</th><th>win rate</th>
      <th>net P&L</th><th>mean</th><th>baseline</th></tr></thead><tbody>` +
    RUNS.map(r => {
      const s = stats(r.rows);
      const d = (baseSum !== null && r.id !== BASE) ? s.sum - baseSum : null;
      return `<tr data-run="${r.id}" class="${r.id === CURRENT ? "on" : ""}">
        <td class="n">${r.id}</td>
        <td><span class="cfg" title="${cfgLine(r.config)}">${cfgLine(r.config)}</span></td>
        <td class="n">${s.n}</td>
        <td class="n">${s.n ? pct(s.win) : "—"}</td>
        <td class="n ${sgn(s.sum)}">${s.n ? money(s.sum) : "—"}
          ${d !== null ? `<span class="delta ${sgn(d)}">${money(d)} vs baseline</span>` : ""}</td>
        <td class="n ${sgn(s.mean)}">${s.n ? money(s.mean) : "—"}</td>
        <td><button class="pin ${r.id === BASE ? "on" : ""}" data-pin="${r.id}">
          ${r.id === BASE ? "baseline" : "set as baseline"}</button></td></tr>`;
    }).join("") + `</tbody>`;
  $("runs").querySelectorAll("tbody tr").forEach(tr =>
    tr.addEventListener("click", e => {
      if(e.target.dataset.pin) return;
      show(+tr.dataset.run);
    }));
  $("runs").querySelectorAll(".pin").forEach(b =>
    b.addEventListener("click", e => { e.stopPropagation(); BASE = +b.dataset.pin; paintRuns(); }));
}

/* ── show one run ── */
function show(runId){
  const run = RUNS.find(r => r.id === runId);
  if(!run){ notice(`no result found for run ${runId}.`); return; }
  CURRENT = runId;
  RAW = run.rows;
  $("placeholder").hidden = true;
  $("result").hidden = false;
  paintRuns();

  const b = $("banner");
  // The hard boundary: one line, above the numbers. It decides whether they can be used.
  b.hidden = !run.boundary;
  if(run.boundary)
    b.innerHTML = `<b>the boundary on these numbers</b><span>${run.boundary.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")}</span>`;
  // Only real problems go into the notice strip. Component gaps do not — they are static,
  // and they sit beside each layer in the left panel.
  for(const m of (run.problems || [])) notice(m);
  renderAnomalies(run.anomalies || []);
  renderDiagnostics(run.diagnostics || []);
  renderValidations(run);
  // Run events are not repeated into the notice strip. They are already in the
  // diagnostics panel, with the distribution of reasons and the date range — a
  // "candidate_dropped: 45" line in the strip is a worse version of the same thing. The
  // strip is for what has to be known at once: errors, and a run with no trades.

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
  if(!cols.length){ notice("this run's trade log has no numeric column to group by."); return; }
  const keep = [$("dim").value, $("dim2").value];
  const opts = cols.map(c => `<option value="${c}">${label(c)}</option>`).join("");
  $("dim").innerHTML = opts;
  $("dim2").innerHTML = `<option value="">(no cross)</option>` + opts;
  $("dim").value  = cols.includes(keep[0]) ? keep[0] : (cols.includes("entry_iv") ? "entry_iv" : cols[0]);
  $("dim2").value = cols.includes(keep[1]) ? keep[1] : "";
  const rs = [...new Set(RAW.map(r => r.exit_reason).filter(Boolean))].sort();
  $("reason").innerHTML = `<option value="">all</option>` +
    rs.map(r => `<option value="${r}">${REASON[r] || r}</option>`).join("");
}

function apply(){
  const reason = $("reason").value;
  VIEW = reason ? RAW.filter(r => r.exit_reason === reason) : RAW;
  const col = $("dim").value, col2 = $("dim2").value, k = +$("bins").value;
  renderStrip(VIEW);
  if(!VIEW.length){
    for(const id of ["buckets","cross","robust","detail"]) $(id).innerHTML = "";
    for(const id of ["curve","hist","years","scatter"]) blankChart($(id), "no trades under the current filter");
    $("ab").innerHTML = `<div class="empty" style="grid-column:1/-1">no trades under the current filter</div>`;
    $("crossSec").hidden = true;
    notice(`no trades left after filtering on "${REASON[reason] || reason}".`);
    return;
  }
  const run = RUNS.find(r => r.id === CURRENT);
  // The three event-driven-only sections. Absent under the trade table they are hidden,
  // rather than shown as a row of zeros.
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

/* ── have the AI read it ── */
function digest(rows, col, k){
  const run = RUNS.find(r => r.id === CURRENT);
  const s = stats(rows), B = bucketize(rows, col, k) || [];
  const by = {}; for(const r of rows){ const y = r.entry_date.slice(0,4); by[y] = (by[y]||0) + r.net_pnl; }
  const top5 = [...rows].sort((a,b)=>b.net_pnl-a.net_pnl).slice(0,5).reduce((a,r)=>a+r.net_pnl,0);
  const reasons = {};
  for(const r of rows){ const k2 = r.exit_reason || "?";
    (reasons[k2] = reasons[k2] || {n:0, sum:0}).n++; reasons[k2].sum += r.net_pnl; }
  const hold = rows.reduce((a,r)=>a+r.holding_days,0)/rows.length;
  const c = run.config, d = describeConfig(c);
  // The same reading as the run history's. Claude is told what actually ran, including
  // which form filled a slot — a premium stop and an underlying stop are different
  // strategies, and a summary that omitted it would invite a conclusion about the wrong one.
  const num = v => v == null ? "not set" : v;
  return [
    `Strategy: entry ${d.entry} · ${d.right} · ${d.dte} · ${d.strike}`
      + ` · take profit ${num(d.takeProfit)}, stop loss ${num(d.stopLoss)}, `
      + `hold at most ${num(d.holdDays)} trading days`
      + (d.components.length ? ` · slots filled by: ${d.components.join(", ")}` : "")
      + (d.risk ? ` · book-wide ${d.risk}` : ""),
    `Underlying and range: ${c.symbol} ${c.start} ~ ${c.end}`,
    `Data: ${(run.boundary||"").includes("ynthetic") ? "a synthetic option chain (the underlying's prices are real; the option prices come from Black-Scholes)" : "real data"}`,
    ``,
    `Overall: ${s.n} trades, win rate ${pct(s.win)}, net ${money(s.sum)}, mean per trade ${money(s.mean)}, `
      + `median ${money(s.median)}, mean holding ${hold.toFixed(1)} trading days`,
    `Best trade ${money(s.max)}, worst ${money(s.min)}, the best 5 together ${money(top5)}`,
    ``,
    `By exit reason:`,
    ...Object.entries(reasons).map(([k2,v]) =>
      `  ${REASON[k2]||k2}: ${v.n} trades, total ${money(v.sum)}, mean ${money(v.sum/v.n)}`),
    ``,
    `By ${label(col)}, ${B.length} equal-frequency buckets:`,
    ...B.map(b => { const t = stats(b.rows);
      return `  ${fmtVal(col,b.lo)} – ${fmtVal(col,b.hi)}: ${t.n} trades, win rate ${pct(t.win)}, `
           + `mean ${money(t.mean)}, median ${money(t.median)}`; }),
    ``,
    `Net P&L by year:`,
    ...Object.keys(by).sort().map(y => `  ${y}: ${money(by[y])}`),
  ].join("\n");
}
const PROMPT_RULES = `You are reading a statistical summary of an options strategy backtest. The reader is the strategy's author, using the backtest to settle its parameters.

Answer in under 180 words, in three sections, each beginning with "— " and a short heading:

— What is visible
The one or two most notable facts. Specific numbers, not a restatement of every statistic.

— Worth following up
Which direction deserves further checking, and why. If the buckets are monotonic, say so; if a single bucket stands out, say that is more likely noise.

— What cannot be trusted
The limits imposed by sample size, concentration and provenance. If the data is a synthetic option chain, you must say that conclusions about the option itself (which delta, where to take profit) cannot be trusted, and only conclusions about the underlying's price (when to enter) can.

No pleasantries, and no empty suggestions to "study it further" — state the judgement.`;

$("ask").addEventListener("click", async () => {
  if(!ask){ notice("the AI summary is unavailable, so no request can be made."); return; }
  if(!VIEW.length){ notice("no trades under the current filter, so there is nothing for Claude to read."); return; }
  const out = $("aiOut"), btn = $("ask"), stop = $("stop");
  ctl = new AbortController();
  btn.disabled = true; stop.hidden = false;
  out.innerHTML = `<em>Claude is reading… (the first response usually takes ten or twenty seconds)</em>`;
  try{
    await ask(PROMPT_RULES + "\n\n===== the summary =====\n" + digest(VIEW, $("dim").value, +$("bins").value),
      { signal: ctl.signal, modelTier:"default", onText: ({text}) => { out.textContent = text; } });
  }catch(e){
    const msg = {cancelled:"stopped.", not_granted:"you declined this call.",
      rate_limited:"too many calls; try again shortly.", prompt_too_large:"too much data; narrow the filter first.",
      refused:"this request was refused.", session_expired:"the session expired; reload the page."}[e.code];
    if(e.code === "not_granted"){
      out.textContent = "";
      document.querySelector(".ai").hidden = true;
      notice("you declined the AI summary, so it is hidden for this session.");
      return;
    }
    const shown = msg || `it failed (${e.code}): ${e.message}`;
    out.textContent = e.text || "";
    out.innerHTML += (e.text ? "\n\n" : "") + `<em>${shown}</em>`;
    if(e.code !== "cancelled") notice(`the AI summary failed: ${shown}`);
  }finally{ btn.disabled = false; stop.hidden = true; ctl = null; }
});
$("stop").addEventListener("click", () => ctl?.abort());

(async () => {
  if(!window.claude?.use){
    document.querySelector(".ai").hidden = true;
    return;   // Opened locally there is no Claude runtime; that is expected, not a problem
  }
  try{ ask = await window.claude.use("sample"); }
  catch(e){ ask = null; notice(`probing for the AI summary failed: ${e?.message || e}`); }
  if(!ask) document.querySelector(".ai").hidden = true;
})();
