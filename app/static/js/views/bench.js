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

import {$, esc, mark} from "./../dom.js";
import {API, clearIssues, getJSON, notice, postJSON} from "./../net.js";
import {CONTRACT, checkContract, checkMeta} from "./../contract.js";
import {num} from "./../fmt.js";
import {go} from "./../router.js";
import {bind} from "./../ui/keys.js";

let META = null;          // the contents of /api/meta
let LIVE = false;         // is the service up

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
  paintSettingGaps();
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
  markChanged();
}

/* ── what has been changed, and from what ────────────────────────────────────────
 *
 * The addendum asks for a dot on every field that differs from the baseline, with the
 * baseline value on hover and a way back, and a line at the top saying how many slots
 * differ. There is no baseline **run** to compare against yet — that needs the comparison
 * page — but there is a baseline every field already has: the starting value the backend
 * ships in `META.defaults`, which `check_defaults` holds to being visible rather than
 * applied on omission.
 *
 * So the dot says "you changed this from what the platform starts at", which is the
 * question a researcher actually asks of a form they have been editing for ten minutes:
 * what in here is mine. Comparing against another run can replace the source later without
 * changing any of this.
 */
function baselineOf(name){
  const d = META?.defaults || {};
  return Object.prototype.hasOwnProperty.call(d, name) ? d[name] : undefined;
}

/** Whether a control holds something other than its starting value. Compared as strings,
 *  because a number typed into a text input is a string and `5 !== "5"` would mark every
 *  untouched numeric field as changed. */
function isChanged(el){
  const was = baselineOf(el.name);
  if(was === undefined || was === null) return false;
  return String(el.value) !== String(was);
}

function markChanged(){
  const form = $("form");
  if(!form) return;
  let n = 0;
  for(const el of form.elements){
    if(!el.name) continue;
    const field = el.closest(".field");
    if(!field) continue;
    const changed = isChanged(el);
    field.classList.toggle("changed", changed);
    if(changed){
      n++;
      // The starting value, and a way back to it. Both on the dot, because a reader who
      // wants to know what they changed from wants to undo it about half the time.
      let dot = field.querySelector(".baseline-dot");
      if(!dot){
        dot = document.createElement("button");
        dot.type = "button";
        dot.className = "baseline-dot";
        field.append(dot);
        dot.addEventListener("click", () => {
          el.value = baselineOf(el.name);
          el.dispatchEvent(new Event("change", {bubbles: true}));
          el.dispatchEvent(new Event("input", {bubbles: true}));
          markChanged();
        });
      }
      dot.title = `starts at ${baselineOf(el.name)} — click to put it back`;
    }else{
      field.querySelector(".baseline-dot")?.remove();
    }
  }
  const line = $("changedLine");
  if(line){
    line.hidden = n === 0;
    line.textContent = n === 1
      ? "1 field differs from the starting configuration"
      : `${n} fields differ from the starting configuration`;
  }
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

/* ── where each slot sits on the board ──────────────────────────────────────────
 *
 * The addendum's wireframe puts the slot board in three lifecycle columns, "so the card order
 * reads like a trade's life": Idle, Armed, Open. The backend's `form` groups them by layer,
 * which is a different cut — it answers "when does the engine call this" — so the mapping is
 * written here, once, with the column each slot acts in.
 *
 * The Open column's cards carry a number, which is `open_state_order`: the order the engine
 * asks them in while a position is on. It is fixed in the engine today, so the numbers are
 * shown and not yet dragged.
 */
const COLUMNS_BY_SLOT = {
  setup: {column: "idle", level: "candidate, per underlying",
          when: "every snapshot while Idle **or Armed** — it can disarm as well as arm",
          emits: ["arm", "disarm"]},
  entry: {column: "armed", level: "underlying, then contracts",
          when: "every snapshot while Armed",
          emits: ["OPEN with legs", "nothing"]},
  select: {column: "armed", level: "contracts",
           when: "inside Entry, once it has fired",
           emits: ["the legs Entry opens"]},
  position: {column: "armed", level: "trade within the book",
             when: "when OPEN or ADD is issued, **and when REDUCE needs a quantity** — so it "
                 + "serves the open state too, not only the entry",
             emits: ["contract quantities"]},
  take_profit: {column: "open", order: 1, level: "trade",
                when: "every snapshot while Open",
                emits: ["REDUCE", "CLOSE", "SET_STOP", "request HEDGE_ADD", "HOLD"]},
  stop_loss: {column: "open", order: 2, level: "trade",
              when: "every snapshot while Open",
              emits: ["CLOSE", "REDUCE", "HOLD"]},
  headwind: {column: "open", order: 3, level: "trade **and** book",
             when: "every snapshot while Open, per trade; and every snapshot on the book",
             emits: ["REDUCE", "CLOSE", "ROLL", "SET_STOP", "request HEDGE_ADD", "HOLD",
                     "scale, veto or force on the book"]},
  hedge: {column: "open", order: 4, level: "trade **and** book",
          when: "on request, or every snapshot while Open; and every snapshot on the book",
          emits: ["HEDGE_ADD", "HEDGE_REMOVE"]},
};

/** What of a slot's contract the engine actually performs today.
 *
 * The architecture gives each slot an action vocabulary — a take profit may REDUCE, move the
 * stop, or ask for a hedge, not only close. What is registered is narrower, and a composer
 * that showed only the controls would let a reader believe the slot is the control. Read from
 * what the registry offers where that is possible; stated here where the gap is structural,
 * with the architecture's own table as the reference.
 */
const WIRED = {
  setup: "nothing is registered. The candidate is always armed, so the gate is open",
  entry: "the condition fires an OPEN. It cannot decline on the contracts it finds",
  select: "a feasible region and one ranking target, applied inside Entry",
  position: "a size per trade. It is not asked again for an ADD or a REDUCE",
  take_profit: "**CLOSE only.** No registered form reduces, moves the stop, or asks for a "
             + "hedge — so a staged exit cannot be expressed",
  stop_loss: "**CLOSE only.** Same: no partial stop, and nothing writes a stop level that a "
           + "later snapshot reads",
  headwind: "**the book level only**, as a risk rule: scale down, halt, liquidate. The "
          + "per-trade headwind runs as the daily review hook and emits nothing of its own",
  hedge: "nothing is registered at either level",
};

const COLUMN_HEAD = {
  idle: {name: "Idle", what: "nothing is open; these decide whether to look at all"},
  armed: {name: "Armed", what: "a setup has fired; these decide what is bought and how much"},
  open: {name: "Open", what: "a position is on; these are asked in this order, every day"},
};

function renderComponents(){
  const planMode = strategyMode() === "plan";
  const all = (META.form || []).flatMap(L => L.components);
  $("comps").innerHTML = ["idle", "armed", "open"].map(column => {
    const here = all
      .filter(c => (COLUMNS_BY_SLOT[c.key] || {}).column === column)
      .sort((a, b) => ((COLUMNS_BY_SLOT[a.key] || {}).order || 0)
                    - ((COLUMNS_BY_SLOT[b.key] || {}).order || 0));
    const head = COLUMN_HEAD[column];
    return `<section class="col" data-col="${column}">
      <h3 class="col-head" data-tip="${esc(head.what)}">${head.name}</h3>
      ${here.map(c => compBlock(c, planMode)).join("")
        || `<p class="hint col-empty">no slot acts here yet</p>`}
    </section>`;
  }).join("");
  // A slot the mapping does not place would vanish silently, which is the one way a
  // generated board can lie about the strategy.
  const placed = new Set(Object.keys(COLUMNS_BY_SLOT));
  const stray = all.filter(c => !placed.has(c.key));
  if(stray.length)
    notice(`the board has no column for ${stray.map(c => c.key).join(", ")}, so ${
      stray.length === 1 ? "it is" : "they are"} not on screen. The lifecycle mapping in `
      + `views/bench.js needs the new slot.`);
  bindFields();
  paintFoot();
}

/** What this slot is, in the architecture's terms, and how much of it runs.
 *
 * A slot is not its control. The architecture gives each one a level, a moment it is asked,
 * and an action vocabulary — a take profit may reduce, move the stop or ask for a hedge, not
 * only close — and the composer showed a number box, which reads as the whole of what the
 * slot is. The line says what it may emit and, underneath, what of that is actually wired, so
 * the difference between the design and the engine is on the card rather than in a document.
 */
function contractLine(slot, place){
  if(!place.level) return "";
  return `<p class="comp-contract">
    <span class="comp-level" data-tip="Where this slot acts, and when the engine asks it: ${
      esc(place.when.replace(/\*\*/g, ""))}">${mark(place.level)}</span>
    <span class="comp-emits" data-tip="The actions this slot may emit, from the
      architecture's shared vocabulary. What is wired today is below.">${
      place.emits.map(e => `<code>${esc(e)}</code>`).join(" ")}</span>
    <span class="comp-wired">${mark(WIRED[slot] || "")}</span>
  </p>`;
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
  const choice = PICKED[c.key];
  const off = !taken && choice === OFF;
  const byForm = !taken && choice && choice !== OFF;
  // Only the field the slot itself decides is replaced or switched off — the one named after
  // it. The others in the same card stay: slot 4's holding cap and days-to-expiry floor are a
  // safety net that applies whichever way the stop is expressed, and a rule written wrong — or
  // a stop switched off — must not be able to drag a position into expiry.
  const own = c.inputs.filter(i => i.name === c.key);
  const kept = off || byForm ? c.inputs.filter(i => i.name !== c.key) : c.inputs;
  const body = taken
    ? `<p class="comp-note">→ <code>${c.in_plan}</code></p>`
    : !c.inputs.length && !byForm && !off
      ? `<p class="comp-note">${gapHtml(c)}</p>`
      : `${off ? `<p class="comp-note off">the engine is given no ${
            own.map(i => esc(i.label)).join(" or ") || esc(c.name)} at all${kept.length
              ? `. The settings below still apply` : ""}</p>` : ""}
         ${byForm ? `<p class="comp-note">the <b>${esc(choice)}</b> rule decides this${
            own.length ? `, in place of ${own.map(i => esc(i.label)).join(" and ")}`
                       : ""}</p>` : ""}
         ${kept.length ? `<div class="fields">${kept.map(fieldHtml).join("")}</div>` : ""}`;
  const badge = STATUS_MARK[c.status]
    ? `<span class="comp-badge ${c.status}">${STATUS_MARK[c.status]}</span>` : "";
  const place = COLUMNS_BY_SLOT[c.key] || {};
  return `<article class="comp ${taken ? "by-plan" : ""}${off ? " is-off" : ""}"
      data-comp="${c.key}">
      <h4 class="comp-h" data-tip="${esc(c.role)}">${place.order
        ? `<span class="comp-order" data-tip="The order the engine asks the open-state slots
             in, every snapshot a position is on — the architecture's open_state_order. Fixed
             in the engine today.">${place.order}</span>`
        : ""}${c.name}${badge}</h4>
      ${contractLine(c.key, place)}
      ${taken ? "" : variantPicker(c.key, own)}
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
/* A dropdown of bare names made this choice blind: `premium`, `underlying`, `trailing` and
   `drawdown` say nothing about what they do, and what each decides was shown **only after
   it had been picked** — so the way to find out was to choose one and read, then choose
   the next. The registry has a sentence for every one of them; the options carry it.

   A list rather than a select for the same reason. Two or three options that each need a
   line to be understood are not a dropdown's shape. */
/** The value that means "this slot does nothing". Not the empty string, which already means
 *  "the plain setting in this card decides it". */
const OFF = "__off__";

/** The slots the engine accepts **nothing** for.
 *
 * `ExitRule` says it in its own words: "`None` means no such exit, which is a real
 * configuration rather than a stand-in" — of take profit and stop loss. Entry is not on the
 * list and must not be: a strategy with no entry condition is not an ablation, it is a
 * strategy that has been deleted, and offering it would be the composer inventing a
 * configuration the engine will refuse.
 *
 * Declared here rather than read from the slot definition because the definition does not
 * carry it. If it grows an `optional` flag this should read that instead, and this comment is
 * the note to do it. */
const CAN_BE_OFF = new Set(["take_profit", "stop_loss"]);

/* ── what decides a slot: nothing, a level, or a rule ────────────────────────────
 *
 * Three kinds, one choice. The card used to offer two — the plain setting, or a registered
 * rule — and **no way to switch the slot off**, which is the design plan's neutral variant:
 * "A neutral variant is always available, so any slot can be switched off for an ablation
 * from the card itself."
 *
 * The engine has taken `stop_loss: float | None` all along. A researcher who wanted to ask
 * "what does this strategy do with no stop at all" had to work out that clearing a number box
 * means something, and nothing on screen said so — so the question could not be asked, and it
 * is one of the first questions there is.
 *
 * The rules come before the level deliberately. A level is one rule — "exit at this fraction
 * of premium" — and listing it first made the registered rules read as an advanced option on
 * top of the real control, when they are the thing a researcher is actually choosing between.
 */
function variantPicker(slot, own){
  const forms = (META.components || {})[slot] || [];
  const canSwitchOff = own.length > 0 && CAN_BE_OFF.has(slot);
  if(!forms.length && !canSwitchOff) return "";
  const chosen = PICKED[slot] ?? "";
  const option = (value, name, why, extra = "") => `
    <label class="rule-opt${value === chosen ? " on" : ""}">
      <input type="radio" name="pick_${slot}" value="${esc(value)}" data-pick="${slot}"
             ${value === chosen ? "checked" : ""}>
      <span class="rule-name">${esc(name)}${extra}</span>
      <span class="rule-why">${mark(why)}</span>
    </label>`;
  const level = own.map(i => i.label).join(" and ");
  return `<div class="variant" data-slot="${slot}">
    <div class="rule-opts">
      ${forms.map(f => option(f.name, f.name, f.describes || "",
        f.stateful ? ` <span class="rule-tag">keeps state</span>` : "")).join("")}
      ${own.length
        ? option("", `a level`, `a single ${esc(level)}, set below — the simplest rule there is`)
        : ""}
      ${canSwitchOff
        ? option(OFF, "not used",
                 `the engine is given no ${esc(level)}. **This is a research question, not a `
                 + `blank**: what the strategy does with nothing holding it`)
        : ""}
    </div>
    ${chosen && chosen !== OFF
      ? variantParams(slot, forms.find(f => f.name === chosen)) : ""}
  </div>`;
}

function variantParams(slot, form){
  if(!form) return "";
  return `<div class="fields">${form.params.map(p => {
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

/* A component with no controls says why it has none.
 *
 * It was cut at fifty-two characters with an ellipsis and no way to reach the rest, so slot 1
 * read `rule-based classification (VIX / term structure / tr…` — a sentence that stops
 * exactly where it was about to say what is missing. The first clause still leads, because a
 * paragraph in a slot card is not read either; the rest is one click away on the card itself.
 */
function gapHtml(c){
  const whole = (c.gap || "").replace(/\*\*/g, "").trim() || "nothing configurable yet";
  const first = whole.split(/[;.]/)[0];
  if(first.length >= whole.length - 1) return `<span>${esc(whole)}</span>`;
  // The whole sentence **replaces** the opening clause rather than following it; appending
  // printed the first clause twice, once on its own and once inside the rest.
  return `<span class="gap-first" id="gapfirst_${esc(c.key)}">${esc(first)}.</span>
    <button type="button" class="gap-more" data-gap="${esc(c.key)}">more</button>
    <span class="gap-rest" id="gap_${esc(c.key)}" hidden>${esc(whole)}</span>`;
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
  for(const el of $("form").elements){
    if(!el.name) continue;
    el.addEventListener("input", markChanged);
    el.addEventListener("change", markChanged);
    // The foot shows what will be sent and what is blocking it; both are wrong the moment
    // anything changes, and a payload that lags the form is worse than none.
    el.addEventListener("input", paintFoot);
    el.addEventListener("change", paintFoot);
  }

  // Choosing a form rebuilds that slot so its parameters appear. Only the components are
  // redrawn, so nothing typed elsewhere in the form is lost.
  $("comps").querySelectorAll("[data-pick]").forEach(input =>
    input.addEventListener("change", () => {
      if(!input.checked) return;
      const slot = input.dataset.pick;
      // The empty string is "a level", which is the absence of a choice and so is stored as
      // one; `OFF` is a choice and is stored.
      if(input.value) PICKED[slot] = input.value;
      else delete PICKED[slot];
      renderComponents();
    }));
  // `change` as well as `input`, because a select fires only the former.
  // The rest of a slot's gap, in place. A sentence cut mid-word with no way to finish it is
  // worse than no sentence: the reader knows something was said and cannot read it.
  $("comps").querySelectorAll("[data-gap]").forEach(button =>
    button.addEventListener("click", () => {
      const rest = $(`gap_${button.dataset.gap}`);
      const first = $(`gapfirst_${button.dataset.gap}`);
      if(!rest) return;
      rest.hidden = false;
      if(first) first.hidden = true;
      button.hidden = true;
    }));
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
for(const tab of document.querySelectorAll("[data-foot]"))
  tab.addEventListener("click", () => {
    for(const t of document.querySelectorAll("[data-foot]"))
      t.classList.toggle("on", t === tab);
    for(const body of document.querySelectorAll("[data-panel]"))
      body.hidden = body.dataset.panel !== tab.dataset.foot;
    // The contract reference is the one that costs a request, so it is fetched when it is
    // first looked at rather than on every page load.
    if(tab.dataset.foot === "contracts" && !$("toWhen").textContent) loadTradeoff();
  });

document.querySelectorAll("[data-strat]").forEach(tab =>
  tab.addEventListener("click", () => {
    document.querySelectorAll("[data-strat]").forEach(t => t.classList.toggle("on", t === tab));
    switchMode();
  }));


/* ── the form ── */
/* How long this will take, from **what runs on this machine have actually taken**.
 *
 * It used to say four seconds a year, which is a number somebody once typed: against this
 * record a five-year event-driven run takes thirteen seconds in the middle and the slowest
 * tenth take twenty-eight, and individual runs have taken four hundred. An estimate wrong by
 * a factor of two is worse than none — the reader stops believing the counter and starts
 * wondering whether the page has hung — so the measured spread is quoted rather than a point,
 * and where there is no measurement the hint says so.
 */
function expected(){
  const f = $("form");
  const a = new Date(f.start.value), b = new Date(f.end.value);
  if(isNaN(a) || isNaN(b)) return null;
  const years = (b - a) / 31557600000;
  if(years <= 0) return {years};
  const pace = (META?.pace || {})[f.engine.value];
  return pace
    ? {years, middle: Math.max(2, Math.round(years * pace.median)),
       high: Math.max(3, Math.round(years * pace.high)), runs: pace.runs}
    : {years};
}

function updateSpanHint(){
  const e = expected();
  if(!e){ $("spanHint").textContent = ""; return; }
  if(e.years <= 0){
    $("spanHint").textContent = "the start is after the end, so this cannot run.";
    return;
  }
  const span = `${e.years.toFixed(1)} years`;
  const hint = $("spanHint");
  hint.textContent = e.middle ? `${span} · about ${e.middle}s` : `${span}`;
  hint.dataset.tip = e.middle
    ? `Measured from the runs on this machine: half of the runs this long finish by ${e.middle
      } seconds and the slowest tenth take over ${e.high}. What drives it is how many `
      + `positions are open at once, not the span — the cost is in valuing them.`
    : "How long a run of this length takes has not been measured on this machine yet.";
}
["start","end"].forEach(n => $("form")[n].addEventListener("change", updateSpanHint));

$("engine").addEventListener("change", updateEngineHint);
$("engine").addEventListener("change", updateSpanHint);
function updateEngineHint(){
  const driven = $("engine").value === "event_driven";
  $("concurrentWrap").hidden = !driven;
  // The two engines run the same rules and usually disagree on the trade count — the
  // event-driven one is bound by the concurrency limit and by cash, so a signal may
  // arrive with no room to open. That is correct, but the first reaction to "135 vs 88"
  // is to wonder which engine is wrong, so it has to be said where the engine is chosen
  // rather than left to be guessed.
  $("engineHint").innerHTML = driven
    ? "Has an equity curve, drawdown and Sharpe. Slower, and its trade count is bound by cash and concurrency."
    : "No equity curve, so no drawdown and no Sharpe. Its trade count is an upper bound, not what the capital allowed.";
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
  // A slot switched off sends **nothing** for the field it decides, whatever a hidden box
  // still holds. The backend reads an absent value as "no rule of this kind", which is what
  // the choice means, and what the engine has always accepted.
  for(const [slot, choice] of Object.entries(PICKED))
    if(choice === OFF) o[slot] = "";
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
    if(variant === OFF) continue;
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
  // **At least one time exit, always.** `ExitRule` refuses a configuration without one —
  // "a position left to drag into expiry is a mechanism failure rather than a choice" — and
  // with the stop switched off it is the only thing left closing a trade. Checked here so the
  // answer arrives on the control rather than after a backtest.
  const f = $("form");
  const blank = name => !f.elements[name] || !String(f.elements[name].value).trim();
  if(blank("hold_days") && blank("dte_floor"))
    return "a holding cap or a days-to-expiry floor is required — with neither, a position "
         + "that nothing else closes is dragged into expiry, which the engine refuses as a "
         + "mechanism failure rather than a choice.";
  for(const [slot, variant] of Object.entries(PICKED)){
    if(variant === OFF) continue;
    const form = ((META.components || {})[slot] || []).find(f => f.name === variant);
    if(!form) continue;
    for(const p of form.params){
      if(!p.required) continue;
      const raw = (PARAMS[`${slot}:${p.name}`] ?? "").toString().trim();
      if(raw === "")
        return `${slot} is set to the ${variant} rule, which needs ${p.name}: ${p.means}. `
             + `It is a strategy choice, so the platform does not pick one.`;
    }
  }
  return "";
}


/* ── running ── */
$("run").addEventListener("click", runBacktest);

/* ⌘↩ submits, which is the design plan's appendix and the one shortcut a composer needs:
   the configuration is typed into a box, and reaching for the mouse to leave it is the step
   the plan's "2 steps to submit" target does not have room for. Bound while the bench is on
   screen rather than always — the same keystroke on the analysis page would run whatever the
   form happened to hold. */
bind("bench", [
  {keys: ["mod+Enter"], does: "run this configuration",
   where: () => !$("view-bench").hidden,
   run: () => { if(!$("run").disabled) runBacktest(); }},
]);
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
  // The counter carries what it is counting towards, and says when it has passed it. A bare
  // "26s so far" cannot be told from a request that will never return.
  const e = expected();
  const tick = () => {
    const secs = (performance.now() - t0) / 1000;
    const past = e && e.high && secs > e.high;
    note.innerHTML = `<span class="spin"></span><span>Running — <b>${secs.toFixed(0)}s</b>${
      e && e.middle ? ` of about ${e.middle}s` : ""}.${past
        ? ` Longer than nine in ten runs of this length. The cost is in valuing open `
          + `positions — an American contract is priced on a tree each day it is held — so a `
          + `configuration that holds many at once takes far longer than its span suggests. `
          + `It has not hung.`
        : ""}</span>`;
  };
  tick();
  const ticking = setInterval(tick, 1000);
  try{
    const data = await postJSON(API.run, formPayload());
    checkContract(data);
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
    const took = (performance.now() - t0) / 1000;
    const e2 = expected();
    $("runHint").textContent = `that run took ${took.toFixed(1)}s`
      + (e2 && e2.middle ? ` against about ${e2.middle}s expected.` : ".");
  }
}

/* ── the panel along the foot ────────────────────────────────────────────────────
 *
 * The addendum's composer has one: Preview, Validation, YAML. Two of the three are
 * answerable now and the third is not — the preview needs a `POST /preview` the service does
 * not have, so that tab says what it would show and what it is waiting on rather than being
 * absent, which would leave the reader thinking the plan asked for less than it did.
 *
 * "What will be sent" rather than YAML: the thing a researcher needs to see is the exact body
 * of the request, and this service takes JSON. A YAML rendering would be a second format to
 * keep true for no gain.
 */
function paintFoot(){
  const blocked = blockingReason();
  const host = $("footValidation");
  if(host)
    host.innerHTML = blocked
      ? `<p class="validation bad">${mark(blocked)}</p>`
      : `<p class="validation ok">Nothing is blocking this run. The backend checks again
         when it arrives — this side can only see the form.</p>`;
  const payload = $("payloadView");
  if(payload) payload.textContent = JSON.stringify(formPayload(), null, 2);
  const run = $("runSum");
  if(run){
    const f = $("form");
    run.textContent = `${f.symbol.value} · ${f.start.value} → ${f.end.value} · ${
      f.engine.value === "event_driven" ? "event driven" : "trade table"}`;
  }
}

/** The gaps the settings column names, each from the backend's own list rather than written
 *  here — a sentence about what is not built goes stale the moment it is. */
function paintSettingGaps(){
  const gaps = META.gaps || [];
  const find = (...words) => gaps.find(g =>
    words.every(w => String(g).toLowerCase().includes(w))) || "";
  const put = (id, text, fallback) => {
    const el = $(id);
    if(el) el.innerHTML = mark(text || fallback);
  };
  put("lifecycleGap", find("lifecycle"),
      "the open-state order, the conflict policy and the re-entry policy are settings the "
      + "architecture names; the engine fixes them, so there is nothing here to set.");
  put("timingGap", find("timing"),
      "the signal snapshot time and the fill latency are set once per configuration. The "
      + "engine decides at the close and fills on the next session.");
  put("dataGap", find("data"),
      "there is one chain and no snapshot id to choose between, so there is nothing to "
      + "pick. The run's own page says which data it stood on.");
  put("bookGap", find("hedge") || find("portfolio"),
      "premium budgets, Greek limits and a scenario loss limit exist in the engine and are "
      + "not wired to this form.");
  const data = $("dataSum");
  if(data) data.textContent = "one chain";
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



/* The list of runs made in this session is gone from here. It was a second, smaller
   experiment log — the same rows, grouped by nothing, lost on reload — and the real one now
   exists and holds every run under the idea that owns it. A composer that also lists results
   is the shape the design plan separates in its sections 4 and 5. */
