/* views/bench.js — the composer: write a configuration and run it.
 *
 * Its job ends at the moment a run is submitted. Reading the result happens on the
 * analysis surface (`views/run.js`), which is the design plan's separation of composing
 * (section 4) from analysing (section 6).
 *
 * They were one page, and it was the wrong shape: a form on the left and, on the right,
 * every chart the system can draw in one endless scroll — history, banner, anomalies,
 * diagnostics, metrics, attribution, scenarios, distribution, buckets, the trade log — with
 * nothing saying which question each answered. It also meant two surfaces claiming the
 * same element ids, so a chart rendered from here landed in the other page's markup.
 *
 * What is left: the form generated from the component definitions, the contract-selection
 * reference, and the list of runs made in this session, each linking to its own page.
 *
 * One rule runs through the file: **no silent failure**. Every error and every skipped
 * thing goes through notice() into the problem strip at the top of the page — seeing a
 * blank in the browser without knowing why is worse than a crash. So there is no empty
 * catch here, and no quiet early return.
 */
"use strict";

import {$, esc} from "./../dom.js";
import {API, clearIssues, getJSON, notice, postJSON} from "./../net.js";
import {CONTRACT, checkContract, checkMeta} from "./../contract.js";
import {money, num, pct, sgn} from "./../fmt.js";
import {stats} from "./../stats.js";
import {go} from "./../router.js";

let META = null;          // the contents of /api/meta
let LIVE = false;         // is the service up
let RUNS = [];            // the runs submitted in this session
let CURRENT = null;       // the most recent, highlighted in the list
let BASE = null;          // the pinned baseline run id (the server's `id` field)

/* Which registered form fills each slot, and the parameters typed into it. Held here
   rather than read back off the DOM, because the component forms are regenerated whenever
   the strategy mode changes and a value only in the markup would vanish with it. */
const PICKED = {};        // slot -> variant name
const PARAMS = {};        // "slot:param" -> value as typed


/* ── startup: probe the service ── */
export async function init(){
  try{
    await getJSON(API.health);
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
}

async function loadMeta(){
  try{
    META = await getJSON(API.meta);
  }catch(e){
    notice(`could not fetch the available signals: ${e.message}. The interface still works, but the defaults and hints will be missing.`);
    return;
  }
  // The form, the gap list and the signal reference are all generated from this payload,
  // so a field missing from it is a part of the interface that silently does not appear.
  checkMeta(META);
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
                ${d["caveat"] ? `data-tip="${esc(d["caveat"])}"` : ""}>
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
        <span class="layer-n" data-tip="${esc(L.what)}">${L.layer}</span>
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
  // A slot decided by a registered form does not also take its plain setting: the engine
  // refuses the pair, because a configuration where both are present cannot be resolved by
  // reading it. So picking a form replaces the field rather than sitting above it, and
  // says which field it replaced.
  const byForm = !taken && PICKED[c.key];
  // Only the field the form actually decides is replaced — the one named after the slot.
  // The others in the same component stay: slot 4's holding cap and days-to-expiry floor
  // are a safety net that applies whichever way the stop is expressed, and a rule written
  // wrong must not be able to drag a position into expiry.
  const replaced = byForm ? c.inputs.filter(i => i.name === c.key) : [];
  const kept = byForm ? c.inputs.filter(i => i.name !== c.key) : c.inputs;
  const body = taken
    ? `<p class="comp-note">→ <code>${c.in_plan}</code></p>`
    : !c.inputs.length && !byForm
      ? `<p class="comp-note">${esc(shortGap(c))}</p>`
      : `${byForm ? `<p class="comp-note">the <b>${esc(PICKED[c.key])}</b> form decides this${
            replaced.length ? `, in place of ${replaced.map(i => esc(i.label)).join(" and ")}`
                            : ""}</p>` : ""}
         ${kept.length ? `<div class="fields">${kept.map(fieldHtml).join("")}</div>` : ""}`;
  const badge = STATUS_MARK[c.status]
    ? `<span class="comp-badge ${c.status}">${STATUS_MARK[c.status]}</span>` : "";
  return `<article class="comp ${taken ? "by-plan" : ""}" data-comp="${c.key}">
      <h4 class="comp-h" data-tip="${esc(c.role)}">${c.name}${badge}</h4>
      ${taken ? "" : variantPicker(c.key)}
      ${body}
    </article>`;
}

/* ── the forms a slot can take ────────────────────────────────────────────────
   A slot has candidate **forms**, not only parameters: a stop measured on the premium
   against one measured on the underlying is the choice the architecture names as research,
   and a sweep over one of them can only ever answer "which premium stop".

   The picker is generated from `META.components`, which the backend reads out of the
   registry — so a form registered in Python is offered here with its own description,
   its own parameters and their own ranges, with no edit on this side. **Nothing is
   selected by default**: the platform does not choose a strategy, and "the one I wrote
   first" is a choice.

   The slot's own fields stay below. They are what runs when no form is picked, which is
   the behaviour every run before this had. */
function variantPicker(slot){
  const forms = (META.components || {})[slot] || [];
  if(!forms.length) return "";
  const chosen = PICKED[slot];
  return `<div class="variant" data-slot="${slot}">
    <label class="variant-lead" data-tip="A registered form decides this slot instead of
      the field below. Which form is the research question; the platform offers them and
      picks none.">form</label>
    <select class="variant-pick" data-pick="${slot}">
      <option value="">use the setting below</option>
      ${forms.map(f => `<option value="${esc(f.name)}"${
        f.name === chosen ? " selected" : ""}>${esc(f.name)}${
        f.stateful ? " (keeps state)" : ""}</option>`).join("")}
    </select>
    ${chosen ? variantParams(slot, forms.find(f => f.name === chosen)) : ""}
  </div>`;
}

function variantParams(slot, form){
  if(!form) return "";
  return `<p class="variant-why">${esc(form.describes)}</p>
    <div class="fields">${form.params.map(p => {
      const id = `v_${slot}_${p.name}`;
      const key = `${slot}:${p.name}`;
      const held = PARAMS[key];
      const label = `<label for="${id}" data-tip="${esc(p.means)}${
        p.required ? "" : ". Optional — left blank, this form does without it."}">${
        esc(p.name)}${p.required ? "" : ` <span class="unit">optional</span>`}</label>`;
      // The control follows the parameter's **kind**, which the schema states. An earlier
      // version rendered everything as a number box and guessed the step from the range —
      // two things the interface was inventing about parameters it does not own, and the
      // first boolean parameter registered would have had no way to be set at all.
      if(p.kind === "bool")
        return `<div class="field">${label}
          <select id="${id}" data-param="${key}">
            <option value=""${held == null || held === "" ? " selected" : ""}>${
              p.required ? "choose" : "leave unset"}</option>
            <option value="true"${held === "true" ? " selected" : ""}>true</option>
            <option value="false"${held === "false" ? " selected" : ""}>false</option>
          </select></div>`;
      return `<div class="field">${label}
        <input type="number" id="${id}" data-param="${key}"
          value="${held ?? ""}" step="${p.kind === "int" ? "1" : "any"}"
          ${p.lo != null ? `min="${p.lo}"` : ""} ${p.hi != null ? `max="${p.hi}"` : ""}
          placeholder="${p.required ? "required" : "optional"}${
            p.lo != null && p.hi != null ? ` · ${p.lo} to ${p.hi}` : ""}">
      </div>`;
    }).join("")}</div>`;
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
  const common = `id="${id}" name="${f.name}"` + (f.help ? ` data-tip="${esc(f.help)}"` : "");
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
      <label for="${id}"${f.help ? ` data-tip="${esc(f.help)}"` : ""}>${f.label}${
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

  // Choosing a form rebuilds that slot so its parameters appear. Only the components are
  // redrawn, so nothing typed elsewhere in the form is lost.
  $("comps").querySelectorAll("[data-pick]").forEach(sel =>
    sel.addEventListener("change", () => {
      const slot = sel.dataset.pick;
      if(sel.value) PICKED[slot] = sel.value;
      else delete PICKED[slot];
      renderComponents();
    }));
  // `change` as well as `input`, because a select fires only the former.
  $("comps").querySelectorAll("[data-param]").forEach(el => {
    const keep = () => { PARAMS[el.dataset.param] = el.value; };
    el.addEventListener("input", keep);
    el.addEventListener("change", keep);
  });
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

  // The slots filled by a registered form. `headwind` is the book-wide risk rule and
  // reaches `run()` by its own argument, so it travels separately — the engine applies it
  // to the book rather than to one trade.
  const components = {};
  for(const [slot, variant] of Object.entries(PICKED)){
    const form = ((META.components || {})[slot] || []).find(f => f.name === variant);
    if(!form) continue;
    const params = {};
    for(const p of form.params){
      const raw = (PARAMS[`${slot}:${p.name}`] ?? "").toString().trim();
      // Blank means "not given". It is never filled in here: a component parameter is a
      // strategy choice, and the backend refuses a missing required one by name rather
      // than running on a number nobody chose.
      if(raw === "") continue;
      params[p.name] = p.kind === "bool" ? raw === "true" : Number(raw);
    }
    const spec = {variant, version: form.version, params};
    // Where a form goes is the backend's to say, not this file's. It used to read
    // `if (slot === "headwind")`, which is a fact about what that slot means — exactly the
    // kind of thing the interface must not know, because the day a slot's meaning is
    // settled on professional grounds the interface should not need editing.
    if(form.sends_to === "risk") o.risk = spec;
    else components[slot] = spec;
  }
  if(Object.keys(components).length) o.components = components;
  return o;
}

/** What is missing before this can run, in the reader's words.
 *
 * Checked here as well as in the backend so the answer arrives on the control rather than
 * after a round trip — but the backend still refuses, because this check can only see the
 * form and the backend sees the run. */
function blockingReason(){
  for(const [slot, variant] of Object.entries(PICKED)){
    const form = ((META.components || {})[slot] || []).find(f => f.name === variant);
    if(!form) continue;
    for(const p of form.params){
      if(!p.required) continue;
      const raw = (PARAMS[`${slot}:${p.name}`] ?? "").toString().trim();
      if(raw === "")
        return `${slot} is set to the ${variant} form, which needs ${p.name}: ${p.means}. `
             + `It is a strategy choice, so the platform does not pick one.`;
    }
  }
  return "";
}


/* ── running ── */
$("run").addEventListener("click", runBacktest);
async function runBacktest(){
  if(!LIVE){ notice("no local service, so nothing can be run."); return; }
  clearIssues();
  // The run button is never disabled — the design plan's state table forbids it, because a
  // dead control explains nothing. It says what is blocking instead.
  const blocked = blockingReason();
  if(blocked){ notice(blocked); return; }
  const btn = $("run");
  btn.disabled = true; btn.classList.add("busy"); btn.textContent = "running…";
  const note = document.createElement("div");
  note.className = "busy-note";
  $("issues").after(note);

  const t0 = performance.now();
  // **A counter, not just a spinner.** A backtest can take well over a minute — the
  // engine values American early exercise on a tree, day by day, for every open position
  // — and a spinner that only spins cannot be told apart from a page that has hung. The
  // elapsed time rising is the difference between waiting and worrying.
  const tick = () => {
    const secs = (performance.now() - t0) / 1000;
    note.innerHTML = `<span class="spin"></span><span>Running — <b>${secs.toFixed(0)}s
      </b> so far.${secs > 20 ? " A long range, or an American contract whose early "
      + "exercise is valued on a tree each day, takes minutes rather than seconds." : ""}
      </span>`;
  };
  tick();
  const ticking = setInterval(tick, 1000);
  try{
    const data = await postJSON(API.run, formPayload());
    checkContract(data);
    RUNS.push(data);
    BASE = BASE ?? data.id;
    CURRENT = data.id;
    paintRuns();
    if(!data.rows.length){
      // No trades is a fact about the configuration, read here where the configuration
      // is. Sending the reader to an analysis page with nothing on it would be a worse
      // way to say the same thing.
      notice("this run produced no trades. The entry condition may be too strict, or "
        + "nothing in the range could be selected.");
      return;
    }
    // The result opens on its own surface. The composer stays as it is behind it, so the
    // next variation is one click back — the design plan's "the composer stays open after
    // submitting".
    go(`/runs/${data.id}`);
  }catch(e){
    // The whole error chain goes back to the interface on purpose: seeing "it failed" in
    // the browser without knowing where is worse than a crash.
    notice(e.detail ? `${e.message}\n\n${e.detail}` : e.message);
  }finally{
    clearInterval(ticking);
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
  $("tradeoffBody").innerHTML = `<p class="hint">computing…</p>`;
  try{
    const d = await getJSON(API.tradeoff, {
      symbol: f.symbol.value,
      date: f.end.value,
      right: (f.right?.value || "call").toUpperCase()[0],
    });
    $("toWhen").textContent = `${d.day} · ${d.symbol} ${num(d.spot)}`;
    $("tradeoffBody").innerHTML = TRADEOFF_GRIDS.map(g =>
      gridTable(g, d.grids[g.key])).join("");
  }catch(e){
    // In the panel rather than the problem strip: this reference is optional, and a day
    // with no chain is a fact about that day, not a fault in the run.
    $("tradeoffBody").innerHTML = `<p class="hint">${esc(e.message)}</p>`;
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
    // Mixed from the token, not written as rgba(): the literal here was the light theme's
    // loss colour from a palette that no longer exists.
    return ` style="background:color-mix(in srgb, var(--danger) `
         + `${(t * 28).toFixed(1)}%, transparent)"`;
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

/* ── describing a configuration ───────────────────────────
   **One reading of the stored config, used by every place that describes a run.**

   There were two, and both had gone stale: each read `select.dte` and `select.moneyness`,
   neither of which the backend writes — selection has been a feasible region
   (`dte_lo`/`dte_hi`, a delta band, a ranking target) for some time. The run history
   printed "undefinedDTE" and the same string was handed to Claude as the strategy's
   description. Nothing raised, because reading an absent field is not an error in JS; it
   just renders.

   Absence shows as "—" and never as `undefined`, and the field names are declared in
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
        <td class="n"><a class="run-link" href="#/runs/${r.id}"
          data-tip="Open run ${r.id} on the analysis page.">${r.id}</a></td>
        <td><span class="cfg" data-tip="${esc(cfgLine(r.config))}">${cfgLine(r.config)}</span></td>
        <td class="n">${s.n}</td>
        <td class="n">${s.n ? pct(s.win) : "—"}</td>
        <td class="n ${sgn(s.sum)}">${s.n ? money(s.sum) : "—"}
          ${d !== null ? `<span class="delta ${sgn(d)}">${money(d)} vs baseline</span>` : ""}</td>
        <td class="n ${sgn(s.mean)}">${s.n ? money(s.mean) : "—"}</td>
        <td><button class="pin ${r.id === BASE ? "on" : ""}" data-pin="${r.id}">
          ${r.id === BASE ? "baseline" : "set as baseline"}</button></td></tr>`;
    }).join("") + `</tbody>`;
  // The whole row opens the run, not only its number: a table of results where most of
  // each row is dead is a table people click twice before learning where to aim.
  $("runs").querySelectorAll("tbody tr").forEach(tr =>
    tr.addEventListener("click", e => {
      if(e.target.closest("[data-pin]")) return;      // pinning is not navigating
      if(e.target.closest(".run-link")) return;       // the link does it itself
      go(`/runs/${tr.dataset.run}`);
    }));
  $("runs").querySelectorAll(".pin").forEach(b =>
    b.addEventListener("click", e => { e.stopPropagation(); BASE = +b.dataset.pin; paintRuns(); }));
}
