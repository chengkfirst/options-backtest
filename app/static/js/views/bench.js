/* views/bench.js — where a rule is written, saved and run.
 *
 * **The rule is a document, not a form.** This panel used to be seven slot cards, a
 * variant picker per slot and a generated control per parameter. It is a text area now,
 * and what is in it is the exact object POSTed to /api/run — nothing is translated on the
 * way out and nothing is filled in when omitted, so what is on screen is what ran.
 *
 * Why the form went. Two reasons, and the second is the serious one:
 *
 * 1. A researcher who already knows the rule they want to test does not want to be walked
 *    through seven dropdowns to say it. The form was slower than typing and could not be
 *    pasted, diffed or kept in a file.
 * 2. A control can only offer the rules someone anticipated. The engine orders contracts
 *    by nearest-to-target delta, so a delta slider answers "which contract" with the only
 *    answer it has — and a rule like "the most leveraged call" comes out as a delta number
 *    that looks like a setting someone chose. The result then gets read as that rule. A
 *    document holds the rule as written, and `gaps()` below says where the engine falls
 *    short of it, which is the honest version of the same screen.
 *
 * Its job still ends at the moment a run is submitted; reading the result happens on the
 * analysis surface (`views/run.js`).
 *
 * One rule runs through the file: **no silent failure.** Every error and every skipped
 * thing goes through notice() into the problem strip. No empty catch, no quiet return.
 */
"use strict";

import {$, esc} from "./../dom.js";
import {API, clearIssues, getJSON, notice, postJSON} from "./../net.js";
import {checkContract, checkMeta} from "./../contract.js";
import {money, num, pct, sgn} from "./../fmt.js";
import {stats} from "./../stats.js";
import {go} from "./../router.js";

let META = null;          // the contents of /api/meta
let LIVE = false;         // is the service up
let RUNS = [];            // the runs submitted in this session
let CURRENT = null;       // the most recent, highlighted in the list
let BASE = null;          // the pinned baseline run id
let SAVED = [];           // the rules already in the store, from /api/strategies


/* ── startup ── */
export async function init(){
  try{
    await getJSON(API.health);
    LIVE = true;
  }catch(e){
    LIVE = false;
    $("mode").textContent = "offline — no local service, so nothing can be run";
    $("mode").className = "mode offline";
    $("runHint").innerHTML = `Start the service in a terminal:<br>`
      + `<code>/opt/anaconda3/bin/python3 app/server.py</code>`;
    notice(`could not reach the local service (${e.message}). Run `
      + `/opt/anaconda3/bin/python3 app/server.py in the project directory, then reload.`);
    return;
  }
  $("mode").textContent = "connected to the local service";
  $("mode").className = "mode live";
  await loadMeta();
  await loadSaved();
}

async function loadMeta(){
  try{
    META = await getJSON(API.meta);
  }catch(e){
    notice(`could not fetch the available signals: ${e.message}. The rule document still `
      + `runs, but the starting document and the signal reference will be missing.`);
    return;
  }
  checkMeta(META);
  $("sigCount").textContent = `${(META.signal_docs || []).length}`;
  $("sigSearch").addEventListener("input", renderSignalRef);
  renderSignalRef();
  $("ruleText").value = asText(startingRule());
  $("ruleText").addEventListener("input", checkRule);
  $("ruleName").addEventListener("input", checkRule);
  checkRule();
}


/* ── the document ────────────────────────────────────────────────────────────────
 *
 * The starting rule comes from `META.defaults`, which is the backend's own registry of
 * starting values — the same source `check_defaults` holds to the rule that nothing fills
 * itself in when omitted. Writing a starting document here instead would be a second set
 * of defaults, and two sets drift.
 */
function startingRule(){
  const d = META?.defaults || {};
  // Key order is the order of the lifecycle: what to run over, when to enter, which
  // contract, when to leave, how much. A JSON object a person reads top to bottom should
  // read in the order the decisions are made, and JSON.stringify preserves insertion
  // order, so this is the order on screen.
  return {
    symbol: d.symbol, start: d.start, end: d.end,
    engine: d.engine, max_concurrent: d.max_concurrent,

    strategy_mode: "params", entry_mode: "expr",
    entry: d.entry,

    right: d.right,
    dte_lo: d.dte_lo, dte_hi: d.dte_hi,
    delta_lo: d.delta_lo, delta_hi: d.delta_hi, delta: d.delta,
    max_spread: d.max_spread, min_open_interest: 0,

    exit_mode: "fixed",
    take_profit: d.take_profit, stop_loss: d.stop_loss,
    hold_days: d.hold_days, dte_floor: d.dte_floor,

    quantity: d.quantity,
    sizing: {method: d.sizing_method, risk_pct: d.risk_pct},
  };
}

const asText = o => JSON.stringify(o, null, 2);

/** Parse the document. A parse failure is reported with its position, because "unexpected
 *  token" without a line number in a forty-line object is not an error message. */
function parseRule(){
  const text = $("ruleText").value;
  try{
    const o = JSON.parse(text);
    if(o === null || typeof o !== "object" || Array.isArray(o))
      return {ok: false, where: "", why: "the document has to be a JSON object: { … }"};
    return {ok: true, rule: o};
  }catch(e){
    // Turn the character offset V8 reports into a line and column.
    const at = /position (\d+)/.exec(e.message);
    let where = "";
    if(at){
      const upto = text.slice(0, +at[1]);
      const line = upto.split("\n").length;
      const col = +at[1] - upto.lastIndexOf("\n");
      where = `line ${line}, column ${col}`;
    }
    return {ok: false, where, why: e.message.replace(/ in JSON at position \d+.*/, "")};
  }
}

/* ── what the engine cannot do with this rule ────────────────────────────────────
 *
 * Derived from the document each time it changes, never a fixed list. This is the part
 * the form could not do: it offered a control for every choice the engine has and so had
 * no way to represent a choice the engine does not have.
 *
 * Silence here means nothing was detected, not that nothing is wrong.
 */
function gaps(rule){
  const out = [];
  const has = k => rule[k] !== undefined && rule[k] !== null && rule[k] !== "";

  // Contract choice. The engine filters to a feasible region and then orders what is left
  // by distance from `delta`. There is no other ranking: not by leverage (delta·spot /
  // premium), not by premium, not by breakeven move. A rule phrased as "the most
  // leveraged" or "the cheapest" in the band cannot be sent, and the nearest thing the
  // engine will do is stated so the result is not read as that rule.
  if(has("delta_lo") && has("delta_hi")){
    const target = has("delta") ? rule.delta : (rule.delta_lo + rule.delta_hi) / 2;
    out.push({
      what: "contracts are ordered by delta, and only by delta",
      detail: `Inside Δ${rule.delta_lo}–${rule.delta_hi} the engine takes the contract `
        + `nearest Δ${target}. It cannot rank by leverage, by premium or by breakeven `
        + `move, so a rule like "the most leveraged call in the band" has to be `
        + `approximated — Δ${rule.delta_lo} as the target picks the lowest-delta, usually `
        + `most leveraged, contract that still passes the liquidity filters.`,
    });
  }

  // Signal names. An expression naming something the registry does not have fails in the
  // engine; naming it here is the same answer without a 80-second round trip.
  if(typeof rule.entry === "string" && META?.signals){
    const known = new Set(META.signals);
    const used = [...new Set(rule.entry.match(/[a-z_][a-z0-9_]*/gi) || [])]
      .filter(w => !["and","or","not","abs","min","max","if","else","True","False"].includes(w));
    const unknown = used.filter(w => !known.has(w) && isNaN(Number(w)));
    if(unknown.length) out.push({
      what: `the entry expression names ${unknown.join(", ")}`,
      detail: `No signal by ${unknown.length > 1 ? "those names" : "that name"} is `
        + `registered, so the engine will refuse this. Available: ${[...known].join(", ")}.`
        + ` A name of your own goes in signals_code.`,
      blocking: true,
    });
    // There is no rolling high or low in the registry, so "fell 5% from its high" has no
    // reference to be written against. Said once, where the expression is.
    if(!known.has("high_20") && /high|peak|from_high/i.test(rule.entry)) out.push({
      what: "no rolling high is available to measure a fall from",
      detail: "The registry has close, the moving averages, mom_20, rsi_14, rv_20, atr_14 "
        + "and bb_width_20 — none of them is a running high. A fall can be written against "
        + "a moving average (close < sma_20 * 0.95) or over a fixed window "
        + "(mom_20 < -0.05); a fall from a peak needs a signal of your own.",
    });
  }

  // Slots with no registered forms. Sending a component for one of these is a rule with
  // nowhere to go, and the engine's refusal names the slot but not that the slot is empty.
  for(const slot of Object.keys(rule.components || {})){
    const forms = (META?.components || {})[slot] || [];
    if(!forms.length) out.push({
      what: `the ${slot} slot has no registered forms`,
      detail: `Nothing is registered for ${slot}, so this cannot be sent as a component. `
        + `Slots with forms: ${Object.entries(META?.components || {})
            .filter(([, v]) => v.length).map(([k]) => k).join(", ") || "none"}.`,
      blocking: true,
    });
  }

  // A consequence of the engine choice, stated once as a consequence. This used to be
  // four lines of prose in a panel titled "how the two engines differ", sitting next to
  // the engine control on every visit whether or not it mattered.
  if(rule.engine === "trade_table") out.push({
    what: "no equity curve on this engine",
    detail: "trade_table prices each trade independently and keeps no account balance, so "
      + "this run has no drawdown and no Sharpe, and its trade count is an upper bound "
      + "rather than what the capital allowed. event_driven has both and is slower.",
  });

  return out;
}

function checkRule(){
  const p = parseRule();
  const state = $("ruleState"), host = $("ruleGaps");

  if(!p.ok){
    state.className = "rule-state bad";
    state.innerHTML = `<b>not valid JSON</b>${p.where ? ` at ${esc(p.where)}` : ""} — `
      + esc(p.why);
    host.innerHTML = "";
    host.hidden = true;
    $("runHint").textContent = "the document has to parse before it can run";
    return;
  }

  const found = gaps(p.rule);
  const blockers = found.filter(g => g.blocking);
  state.className = "rule-state ok";
  state.innerHTML = `${Object.keys(p.rule).length} fields · sent as written`;

  host.hidden = !found.length;
  host.innerHTML = !found.length ? "" : `<div class="runs-head">
      <h2>what the engine cannot do with this rule</h2></div>
    <div class="gap-list">${found.map(g => `<div class="gap${g.blocking ? " blocking" : ""}">
        <b>${esc(g.what)}</b><p>${esc(g.detail)}</p></div>`).join("")}</div>`;

  // The run button is never disabled — the design plan's state table forbids it, because a
  // dead control explains nothing. It carries the reason instead.
  $("runHint").textContent = blockers.length
    ? blockers[0].what + " — the engine will refuse this"
    : "";
}


/* ── running ── */
$("run").addEventListener("click", runBacktest);
async function runBacktest(){
  if(!LIVE){ notice("no local service, so nothing can be run."); return; }
  clearIssues();
  const p = parseRule();
  if(!p.ok){
    notice(`the rule document is not valid JSON${p.where ? ` (${p.where})` : ""}: ${p.why}`);
    return;
  }
  const name = $("ruleName").value.trim();
  if(!name){ notice("give the rule a name — it is what the store versions it under, and "
    + "what tells two different rules apart in the run history."); return; }

  const btn = $("run");
  btn.disabled = true; btn.classList.add("busy"); btn.textContent = "running…";
  const note = document.createElement("div");
  note.className = "busy-note";
  $("issues").after(note);

  const t0 = performance.now();
  // **A counter, not just a spinner.** A backtest can take well over a minute — the engine
  // values American early exercise on a tree, day by day, for every open position — and a
  // spinner that only spins cannot be told apart from a page that has hung.
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
    const data = await postJSON(API.run, {...p.rule, strategy_name: name});
    checkContract(data);
    RUNS.push(data);
    BASE = BASE ?? data.id;
    CURRENT = data.id;
    paintRuns();
    loadSaved();                       // the rule was just saved, so the list changed
    if(!data.rows.length){
      notice("this run produced no trades. The entry condition may be too strict, or "
        + "nothing in the range could be selected.");
      return;
    }
    go(`/runs/${data.id}`);
  }catch(e){
    notice(e.detail ? `${e.message}\n\n${e.detail}` : e.message);
  }finally{
    clearInterval(ticking);
    note.remove();
    btn.disabled = false; btn.classList.remove("busy"); btn.textContent = "run";
  }
}


/* ── the rules already saved ─────────────────────────────────────────────────────
 *
 * The store versions a rule by name: saving the same name with a different body makes a
 * new version and keeps the old one, which is why a run can still say which version it
 * ran. That machinery was already there and had no screen; this is the screen.
 *
 * It also answers a question that cost a day: runs that look like the same strategy
 * behaving randomly are usually different versions saved under one name. The version
 * column makes that visible instead of mysterious.
 */
async function loadSaved(){
  try{
    const d = await getJSON(API.strategies);
    SAVED = d.strategies || [];
  }catch(e){
    notice(`could not list the saved rules: ${e.message}`);
    return;
  }
  paintSaved();
}

function paintSaved(){
  $("savedSec").hidden = !SAVED.length;
  if(!SAVED.length) return;
  $("saved").innerHTML = `<thead><tr>
      <th style="text-align:left">name</th><th>version</th><th>runs</th>
      <th style="text-align:left">rule</th><th>saved</th></tr></thead><tbody>`
    + SAVED.map((s, i) => `<tr data-saved="${i}">
        <td><b>${esc(s.name)}</b></td>
        <td class="n">v${s.version}</td>
        <td class="n">${s.runs}</td>
        <td><span class="cfg">${esc(cfgLine(s.spec))}</span></td>
        <td class="n">${esc((s.created || "").slice(0, 10))}</td></tr>`).join("")
    + `</tbody>`;
  $("saved").querySelectorAll("tbody tr").forEach(tr =>
    tr.addEventListener("click", () => loadRule(SAVED[+tr.dataset.saved])));
}

/** Put a saved rule back in the editor.
 *
 * The store keeps the rule in its nested form (`select`, `exit`, `sizing`), which is what
 * the engine holds internally; the document is flat, which is what /api/run accepts. The
 * one place the two shapes meet is here, and it is a widening — every field in the stored
 * spec reaches the document, so loading and running reproduces the run rather than
 * approximating it. A field the flattening does not know about is kept under its own name
 * rather than dropped, so a backend addition shows up in the editor as itself.
 */
function loadRule(saved){
  const s = saved.spec || {};
  const flat = {};
  const put = (k, v) => { if(v !== undefined && v !== null) flat[k] = v; };

  put("symbol", s.symbol); put("start", s.start); put("end", s.end);
  put("engine", s.engine); put("max_concurrent", s.max_concurrent);
  put("strategy_mode", s.strategy_mode); put("entry_mode", s.entry_mode || "expr");
  put("entry", s.entry);
  for(const [k, v] of Object.entries(s.select || {})) put(k, v);
  put("exit_mode", s.exit_mode || "fixed");
  for(const [k, v] of Object.entries(s.exit || {})) put(k, v);
  put("quantity", s.quantity); put("sizing", s.sizing);
  put("components", s.components); put("risk", s.risk);
  for(const code of ["signals_code", "entry_code", "plan_code", "review_code"])
    if(s[code]) put(code, s[code]);
  // Anything the backend stores that this function has not been taught about. Keeping it
  // visible is the difference between an editor that falls behind the engine silently and
  // one that shows the field and lets it be edited.
  const known = new Set(["symbol","start","end","engine","max_concurrent","strategy_mode",
    "entry_mode","entry","select","exit_mode","exit","quantity","sizing","components",
    "risk","signals_code","entry_code","plan_code","review_code"]);
  for(const [k, v] of Object.entries(s)) if(!known.has(k)) put(k, v);

  $("ruleName").value = saved.name;
  $("ruleText").value = asText(flat);
  checkRule();
  $("ruleText").scrollIntoView({block: "nearest"});
}


/* ── the signal reference ──────────────────────────────
   Grouped by family, with meanings and units, searchable, and a click inserts at the
   cursor. Names alone carry no information — the reader would still have to guess what
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
        <button type="button" class="sig" data-name="${d["signal"]}">
          <code>${d["signal"]}</code>
          <span class="sig-what">${d["measures"]}</span>
          <span class="sig-unit">${d["unit"]}</span>
          ${d["caveat"] ? '<span class="sig-warn" aria-label="has a caveat">!</span>' : ""}
        </button>`).join("")}
    </div>`).join("");

  $("sigRef").querySelectorAll(".sig").forEach(b =>
    b.addEventListener("click", () => insertSignal(b.dataset.name)));
}

/* Insert at the cursor rather than replacing the line — replacing would discard a
   condition already written. */
function insertSignal(name){
  const el = $("ruleText");
  if(!el) return;
  const a = el.selectionStart ?? el.value.length, b = el.selectionEnd ?? a;
  el.value = el.value.slice(0, a) + name + el.value.slice(b);
  el.focus();
  el.selectionStart = el.selectionEnd = a + name.length;
  checkRule();
}


/* ── the contract-selection reference ─────────────────────
   The shape of four quantities over the moneyness x tenor surface is the substance of the
   intuition about choosing a contract, and it comes from that day's real chain rather than
   a formula table. It is also the only place that shows what the delta band is choosing
   between, which is what makes the leverage gap above concrete. */
const TRADEOFF_GRIDS = [
  {key:"theta_pct_day",  title:"premium burnt per day",  fmt:v=>(v*100).toFixed(1)+"%",
   why:"the cost of the underlying not moving; short-dated and out of the money is a cliff", worse:"high"},
  {key:"breakeven_move", title:"move needed to break even", fmt:v=>(v*100).toFixed(1)+"%",
   why:"in the direction of the thesis; a smaller thesis cannot carry this contract", worse:"high"},
  {key:"delta_now",      title:"delta now",       fmt:v=>v.toFixed(2),
   why:"the directional exposure at entry",       worse:null},
  {key:"delta_if_move",  title:"delta after 3% and ten days", fmt:v=>v.toFixed(2),
   why:"what the exposure becomes once the thesis plays out", worse:null},
];

async function loadTradeoff(){
  const p = parseRule();
  if(!p.ok){
    $("tradeoffBody").innerHTML = `<p class="hint">the rule document has to parse first — `
      + `the symbol and the date come from it.</p>`;
    return;
  }
  $("tradeoffBody").innerHTML = `<p class="hint">computing…</p>`;
  try{
    const d = await getJSON(API.tradeoff, {
      symbol: p.rule.symbol,
      date: p.rule.end,
      right: String(p.rule.right || "call").toUpperCase()[0],
    });
    $("toWhen").textContent = `${d.day} · ${d.symbol} ${num(d.spot)}`;
    $("tradeoffBody").innerHTML = TRADEOFF_GRIDS.map(g =>
      gridTable(g, d.grids[g.key])).join("");
  }catch(e){
    // In the panel rather than the problem strip: this reference is optional, and a day
    // with no chain is a fact about the data, not a failure of the run being composed.
    $("tradeoffBody").innerHTML = `<p class="hint">not available: ${esc(e.message)}</p>`;
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
   description. Nothing raised, because reading an absent field is not an error in JS.

   Absence shows as "—" and never as `undefined`, and the field names are declared in
   CONTRACT so check_wiring fails on the next rename instead of the screen. */
function describeConfig(c){
  const s = c.select || {}, x = c.exit || {};
  const band = (lo, hi, fmt = v => v) =>
    lo != null && hi != null ? `${fmt(lo)}–${fmt(hi)}`
      : lo != null ? `≥${fmt(lo)}` : hi != null ? `≤${fmt(hi)}` : null;
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
    spread: s.max_spread != null ? `spread ≤${s.max_spread}` : null,
    openInterest: s.min_open_interest != null ? `OI ≥${s.min_open_interest}` : null,
    takeProfit: x.take_profit ?? null,
    stopLoss: x.stop_loss ?? null,
    holdDays: x.hold_days ?? null,
    dteFloor: x.dte_floor ?? null,
    sizing: c.sizing?.method || null,
    components: Object.entries(c.components || {}).map(([slot, one]) =>
      `${slot}=${one.variant}${one.version != null ? "@" + one.version : ""}`),
    risk: c.risk?.variant ? `risk=${c.risk.variant}` : null,
  };
}

function cfgLine(c){
  const d = describeConfig(c);
  const n = v => v == null ? "—" : v;
  return [`[${d.engine}] ${d.entry}`,
          `${d.right} ${d.dte} ${d.strike}`,
          d.spread, d.openInterest,
          `TP${n(d.takeProfit)} SL${n(d.stopLoss)} hold${n(d.holdDays)}`,
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
        <td class="n"><a class="run-link" href="#/runs/${r.id}">${r.id}</a></td>
        <td><span class="cfg">${esc(cfgLine(r.config))}</span></td>
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
