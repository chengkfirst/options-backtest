/* views/log.js — the experiment log: every run, grouped by the idea that owns it.
 *
 * The design plan's section 5. The grouping is the whole point and it is not cosmetic:
 * "Runs are grouped by idea, because the idea's trial count is what the Deflated Sharpe
 * needs and what keeps research honest." So an idea's header carries the size of the search
 * under it — how many runs, how many of them were distinct configurations, and how many
 * cannot be placed — and a reader cannot look at the best result without seeing how much
 * looking produced it.
 *
 * Three rules from the plan hold here:
 *
 * - **Archived, never deleted.** Archived runs are hidden by default and still counted in
 *   the header, which is why the count in the header is larger than the rows beneath it. The
 *   filter says so rather than leaving the reader to wonder.
 * - **Intervals over points.** The headline interval sits beside the headline figure, and
 *   goes muted below the trade count this project requires before a statistic carries one.
 * - **Filters are view state**, so they are in the URL and a link restores the list.
 *
 * What the plan asks for and is **not** here, each because the record does not hold it: a
 * sparkline per row (needs the daily curve, which is one fetch per run), a research tag
 * (nothing creates ablation or sweep runs yet), a deflated Sharpe in the header (it needs the
 * period count in years, which the robustness tab asks for rather than assumes), and frozen
 * or configurable columns.
 */
"use strict";

import {$, esc} from "../dom.js";
import {API, getJSON, notice, postJSON} from "../net.js";
import {checkIdeas, checkRuns} from "../contract.js";
import {absent, bare, moneyShort, num, pct} from "../fmt.js";
import {summary as diffLine} from "../configdiff.js";
import {go, replace} from "../router.js";
import {bind} from "../ui/keys.js";

let IDEAS = [];
let RUNS = [];
let META = null;
let VERDICTS = {};
let TICKED = new Set();     // the runs ticked for a comparison
let OPEN = new Set();       // which idea groups are expanded
let SORT = {key: "started", dir: -1};
let FILTERS = {};
let UNBIND = null;

/** Every filter the list understands, and how each decides whether a row stays.
 *
 * Declared as a table so the bar, the URL and the filtering cannot disagree — a filter drawn
 * by hand and applied by hand is two places to add the next one. */
const FILTERS_SPEC = [
  {key: "idea", label: "idea", kind: "select",
   options: () => [...new Set(RUNS.map(r => r.strategy_name))].sort(),
   keep: (r, v) => r.strategy_name === v},
  {key: "symbol", label: "underlying", kind: "select",
   options: () => [...new Set(RUNS.map(r => r.symbol))].sort(),
   keep: (r, v) => r.symbol === v},
  {key: "engine", label: "engine", kind: "select",
   options: () => ["event_driven", "trade_table"],
   keep: (r, v) => (r.config?.engine || "trade_table") === v},
  {key: "verdict", label: "verdict", kind: "select",
   options: () => Object.keys(VERDICTS),
   keep: (r, v) => (r.last_verdict || "open") === v},
  {key: "data", label: "data", kind: "select",
   options: () => ["synthetic", "real"],
   keep: (r, v) => (r.synthetic ? "synthetic" : "real") === v},
  {key: "archived", label: "archived", kind: "check",
   // Not a filter on the rows: it decides what is fetched, because the endpoint hides them.
   keep: () => true},
];

/** The columns, in reading order: what it is, then what it did, then what it rests on. */
const COLUMNS = [
  {key: "id", name: "run", left: true,
   text: r => `${r.id}`,
   html: r => `<a class="run-link" href="#/runs/${r.id}">${r.id}</a>
     <span class="log-badge">${r.config?.engine === "event_driven" ? "event" : "table"}</span>
     ${r.synthetic ? `<span class="log-badge syn" data-tip="The option prices came from the
        synthetic chain, so no option-side number here describes a traded market.">synthetic</span>` : ""}
     ${r.archived ? `<span class="log-badge arch">archived</span>` : ""}`},
  {key: "started", name: "when", left: true,
   text: r => String(r.started).slice(0, 10),
   html: r => `<span data-tip="${esc(String(r.started))}">${
     esc(String(r.started).slice(0, 10))}</span>`},
  {key: "period", name: "period", left: true,
   text: r => `${r.start_date} → ${r.end_date}`,
   html: r => `<span class="mono">${esc(String(r.start_date).slice(0, 7))} →
     ${esc(String(r.end_date).slice(0, 7))}</span>`},
  {key: "differs", name: "what differs from the baseline", left: true, wide: true,
   text: r => diffLine(baselineFor(r)?.config, r.config, 3) || "",
   html: r => {
     const base = baselineFor(r);
     if(!base) return `<span class="log-none">no baseline in this idea</span>`;
     if(base.id === r.id) return `<span class="log-base">the baseline</span>`;
     const line = diffLine(base.config, r.config, 3);
     return line ? `<span data-tip="Against run ${base.id}, this idea's baseline.">${
       esc(line)}</span>` : `<span class="log-none">nothing — same configuration</span>`;
   }},
  {key: "n_trades", name: "trades", num: true, text: r => r.n_trades,
   html: r => thinEvidence(r)
     ? `<span class="log-thin" data-tip="Below the ${floor()} trades this project requires
         before a statistic carries an interval. Every figure in this row is drawn muted for
         that reason — they are not wrong, they are not evidence.">${num(r.n_trades, 0)}</span>`
     : num(r.n_trades, 0)},
  {key: "net_pnl", name: "net P&L ($)", num: true, sign: true, text: r => r.net_pnl,
   html: r => bare(r.net_pnl)},
  // **The header does not name a level; the data does.** It read "90% of redraws" because
  // that is the level the design plan asks for, while `bootstrap_trades` reports 95 — so the
  // column asserted one number and the cells under it were another. The level is read off
  // the first row that carries one, and the column says plainly when nothing carries one.
  {key: "interval", name: () => {
     const one = RUNS.map(interval).find(iv => iv && !absent(iv.level));
     return one ? `${Math.round(one.level * 100)}% of redraws` : "redraws";
   }, num: true,
   text: r => interval(r)?.low ?? null,
   html: r => {
     const iv = interval(r);
     if(!iv || absent(iv.low)) return "—";
     const thin = thinEvidence(r);
     const crosses = iv.low <= 0 && 0 <= iv.high;
     return `<span class="log-iv${thin ? " thin" : ""}${crosses ? " crosses" : ""}"
       data-tip="${Math.round((iv.level ?? 0.95) * 100)}% of resamples landed in this range.${
         crosses ? " It spans zero, so the sign is not established." : ""}${
         thin ? ` Muted: ${r.n_trades} trades is below the ${floor()} this project requires.`
              : ""}">${esc(moneyShort(iv.low))} … ${esc(moneyShort(iv.high))}</span>`;
   }},
  {key: "annualised", name: "annual", num: true, sign: true,
   text: r => r.analysis?.equity_metrics?.annualised ?? null,
   html: r => pct(r.analysis?.equity_metrics?.annualised)},
  {key: "max_drawdown", name: "worst fall", num: true,
   text: r => r.analysis?.equity_metrics?.max_drawdown ?? null,
   html: r => pct(r.analysis?.equity_metrics?.max_drawdown)},
  {key: "sortino", name: "Sortino", num: true,
   text: r => r.analysis?.investor?.Sortino ?? null,
   html: r => num(r.analysis?.investor?.Sortino)},
  {key: "realism", name: "rests on", left: true,
   text: r => realism(r).map(c => c.text).join(" "),
   html: r => realism(r).map(c =>
     `<span class="log-chip" data-tip="${esc(c.why)}">${esc(c.text)}</span>`).join("")},
  {key: "verdict", name: "verdict", left: true,
   text: r => r.last_verdict || "open",
   html: r => `<select class="log-verdict" data-verdict="${r.id}">${
     Object.entries(VERDICTS).map(([key, says]) =>
       `<option value="${esc(key)}"${(r.last_verdict || "open") === key ? " selected" : ""}
        >${esc(says)}</option>`).join("")}</select>`},
  {key: "note", name: "note", left: true, wide: true,
   text: r => "",
   html: r => `<input class="log-note" data-note="${r.id}" type="text"
     placeholder="${r.n_notes ? `${r.n_notes} already written — add another` : "add a note"}"
     data-tip="Written to the run's record, with the verdict beside it. Notes are kept; a
               new one does not replace the last.">`},
];

const floor = () => META?.thresholds?.bootstrap_min_observations?.value ?? 0;
const thinEvidence = r => r.n_trades < floor();
const interval = r => r.analysis?.intervals?.net_pnl || null;

/** The two assumptions a reader challenges first, as chips. The mark method is the third and
 *  is not here: both marks are stored for every run and neither is "the" assumption, so the
 *  run page offers it as a display toggle instead. */
function realism(r){
  const lam = r.manifest?.config?.costs?.adverse_fraction;
  const lag = r.manifest?.execution?.execution_lag_days;
  return [
    {text: absent(lam) ? "λ —" : `λ ${num(lam)}`,
     why: "How much of the spread the fill paid. 1.00 is the whole spread, the pessimistic "
        + "end; below it assumes an edge in getting filled."},
    {text: lag == null ? "lag —" : `lag ${lag}`,
     why: "Sessions between the signal and the fill. 0 means filled on the close that "
        + "produced the signal, which cannot be traded."},
  ];
}

/** The baseline a row is measured against: the idea's **first** run, unless one is pinned.
 *
 * The first, because that is the configuration the search started from and every later run
 * under the name is a step away from it. The reader can pin another, which is what the plan's
 * "setting a baseline column" is for, and the pin is in the URL so the choice travels with
 * the link. */
function baselineFor(row){
  const pinned = FILTERS.base && RUNS.find(r => String(r.id) === String(FILTERS.base));
  if(pinned && pinned.strategy_name === row.strategy_name) return pinned;
  const family = RUNS.filter(r => r.strategy_name === row.strategy_name);
  // The list arrives newest first, so the last one loaded is the oldest. On an idea with more
  // runs than the page limit that is the oldest **loaded**, not the first ever — which is why
  // the baseline can be pinned, and why the pin is what a shared link carries.
  return family.length ? family[family.length - 1] : null;
}

export async function enter({query}){
  FILTERS = {...query};
  SORT = query.sort
    ? {key: String(query.sort).replace(/^-/, ""), dir: String(query.sort).startsWith("-") ? -1 : 1}
    : SORT;
  OPEN = new Set(String(query.open || "").split(",").filter(Boolean));
  TICKED = new Set(String(query.runs || "").split(",").filter(Boolean));
  const host = $("view-log");
  if(!RUNS.length) host.innerHTML = `<div class="log-wrap"><p class="hint">reading the record…</p></div>`;
  try{
    if(!META) META = await getJSON(API.meta);
    const [ideaside, runside] = await Promise.all([
      getJSON(API.ideas),
      getJSON(API.runs, {limit: 500, ...(FILTERS.archived ? {archived: 1} : {})}),
    ]);
    IDEAS = ideaside.ideas || [];
    VERDICTS = ideaside.verdicts || {};
    RUNS = runside.runs || [];
    // Checked against the declaration at once, rather than letting `undefined` reach the
    // header of a group where it would read as a trial count of nothing.
    checkIdeas(IDEAS);
    checkRuns(RUNS);
  }catch(e){
    host.innerHTML = `<div class="pending"><h2>experiment log</h2>
      <p class="hint">${esc(e.message)}</p></div>`;
    return;
  }
  // An idea is opened by default when it is the only one with runs on screen, so a store
  // with one idea in it does not greet the reader with a single closed line.
  if(!OPEN.size && IDEAS.length === 1) OPEN.add(IDEAS[0].idea);
  bindKeys();
  render();
}

function bindKeys(){
  if(UNBIND) UNBIND();
  UNBIND = bind("log", [
    {keys: ["c"], does: "compare the ticked runs",
     run: compareTicked, where: () => TICKED.size >= 2},
    {keys: ["Escape"], does: "untick every run",
     run: () => { TICKED.clear(); replace({runs: null}); render(); },
     where: () => TICKED.size > 0},
  ]);
  // No run source of its own. The shell offers one from anywhere, and a second source under
  // the same name would replace it while this page is open and **delete it** on the way out —
  // the palette would then stop finding runs for the rest of the session.
}

/* ── what is on screen ── */

function kept(){
  let rows = RUNS;
  for(const spec of FILTERS_SPEC){
    const value = FILTERS[spec.key];
    if(!value || spec.kind === "check") continue;
    rows = rows.filter(r => spec.keep(r, value));
  }
  if(FILTERS.text){
    const q = String(FILTERS.text).toLowerCase();
    rows = rows.filter(r => JSON.stringify(r.config || {}).toLowerCase().includes(q));
  }
  return rows;
}

function sortRows(rows){
  const col = COLUMNS.find(c => c.key === SORT.key);
  if(!col) return rows;
  return [...rows].sort((a, b) => {
    const x = col.text(a), y = col.text(b);
    if(absent(x) && absent(y)) return 0;
    if(absent(x)) return 1;
    if(absent(y)) return -1;
    return (typeof x === "number" && typeof y === "number"
      ? x - y : String(x).localeCompare(String(y))) * SORT.dir;
  });
}

function filterBar(){
  const controls = FILTERS_SPEC.map(spec => {
    if(spec.kind === "check")
      return `<label class="log-f check"><input type="checkbox" data-filter="${spec.key}"
        ${FILTERS[spec.key] ? "checked" : ""}> show ${esc(spec.label)}</label>`;
    const options = spec.options();
    return `<label class="log-f">${esc(spec.label)}
      <select data-filter="${spec.key}">
        <option value="">any</option>
        ${options.map(o => `<option value="${esc(o)}"${
          String(FILTERS[spec.key] || "") === String(o) ? " selected" : ""}>${
          esc(VERDICTS[o] || String(o).replace("_", " "))}</option>`).join("")}
      </select></label>`;
  }).join("");
  const on = FILTERS_SPEC.filter(s => FILTERS[s.key]).length + (FILTERS.text ? 1 : 0);
  return `<div class="log-filters">
    ${controls}
    <label class="log-f grow">in the configuration
      <input type="search" data-filter="text" value="${esc(FILTERS.text || "")}"
        placeholder="a signal name, a variant, a number…"></label>
    ${on ? `<button type="button" class="chip" id="clearFilters">clear ${on} filter${
      on === 1 ? "" : "s"}</button>` : ""}
  </div>`;
}

/** The idea's header: the size of the search, before its best result. */
function ideaHead(idea, shownRuns){
  const lower = idea.unplaced > 0;
  const holdOut = idea.hold_out
    ? `${esc(idea.hold_out)}${idea.hold_out_touched ? ", revealed" : ", locked"}`
    : `<span class="log-warn">no period reserved</span>`;
  return `<button type="button" class="idea-head" data-idea="${esc(idea.idea)}"
      aria-expanded="${OPEN.has(idea.idea)}">
    <span class="subj-caret" aria-hidden="true"></span>
    <span class="idea-name">${esc(idea.idea)}</span>
    <span class="idea-trials" data-tip="${lower
      ? `${idea.unplaced} of ${idea.runs} runs were saved before the reproducibility `
        + `fingerprint existed and cannot be told from a rerun. ${idea.distinct === 0
            ? "None of this idea's runs can be placed, so there is no trial count to give "
              + "— and a zero would read as a search that never happened."
            : "The distinct count is a lower bound."}`
      : "Distinct configurations tried under this name. Archived runs are included: they "
        + "happened, and the deflated Sharpe is deflated by how much searching was done."
      }">${idea.distinct === 0 && lower
        ? `${idea.runs} runs, none placed`
        : `${idea.distinct}${lower ? "+" : ""} tried${
            idea.reruns ? ` · ${idea.reruns} rerun${idea.reruns === 1 ? "" : "s"}` : ""}`
      }</span>
    <span class="idea-best" data-tip="The best annualised Sharpe among this idea's runs. It
      is **not** deflated for the size of the search — the deflation needs the period count
      in years, which the robustness tab asks for rather than assumes.">${
      idea.best_sharpe == null ? "no Sharpe" : `best Sharpe ${num(idea.best_sharpe)}`}</span>
    <span class="idea-holdout">hold-out: ${holdOut}</span>
    <span class="idea-count">${shownRuns} shown of ${idea.runs}</span>
  </button>`;
}

function table(rows){
  return `<table class="log-table"><thead><tr>
    <th class="tick"></th>
    ${COLUMNS.map(c => `<th${c.left ? ' class="left"' : ""}><button type="button"
      class="sortby${SORT.key === c.key ? " on" : ""}" data-sort="${c.key}">${
        esc(typeof c.name === "function" ? c.name() : c.name)}${
        SORT.key === c.key ? `<span aria-hidden="true">${SORT.dir < 0 ? "↓" : "↑"}</span>`
        : ""}</button></th>`).join("")}
    </tr></thead><tbody>${rows.map(r => `<tr data-run="${r.id}" class="${
      [TICKED.has(String(r.id)) ? "ticked" : "",
       // The research protocol, applied to the list: below the minimum trade count the row's
       // figures are muted. A 13-trade run showing a 47.9% annual return in the same green as
       // a 400-trade one invites exactly the comparison the threshold exists to prevent.
       thinEvidence(r) ? "thin" : ""].filter(Boolean).join(" ")}">
      <td class="tick"><input type="checkbox" data-tick="${r.id}"
        ${TICKED.has(String(r.id)) ? "checked" : ""}
        aria-label="tick run ${r.id} for a comparison"></td>
      ${COLUMNS.map(c => {
        const raw = c.text(r);
        const cls = [c.left ? "left" : c.num ? "n" : "",
                     c.sign && typeof raw === "number" && !absent(raw)
                       ? (raw > 0 ? "pos" : raw < 0 ? "neg" : "") : "",
                     c.wide ? "wide" : ""].filter(Boolean).join(" ");
        return `<td${cls ? ` class="${cls}"` : ""}>${c.html(r)}</td>`;
      }).join("")}
    </tr>`).join("")}</tbody></table>`;
}

function render(){
  const rows = sortRows(kept());
  const byIdea = new Map();
  for(const r of rows){
    if(!byIdea.has(r.strategy_name)) byIdea.set(r.strategy_name, []);
    byIdea.get(r.strategy_name).push(r);
  }
  // Ideas in the order the record puts them, newest activity first, and an idea with no row
  // left after filtering still shows its header — otherwise a filter looks like a store that
  // lost an idea.
  const groups = IDEAS.filter(i => byIdea.has(i.idea) || !rows.length || !FILTERS.idea);

  $("view-log").innerHTML = `<div class="log-wrap">
    <header class="log-head">
      <h2>experiment log</h2>
      <p class="hint">Every run, under the idea that owns it. The count in each header is the
        size of the search, archived runs included.${RUNS.length
          ? ` ${esc(String(RUNS.length))} runs are loaded${FILTERS.archived
              ? ", archived ones among them" : "; archived ones are hidden"}.` : ""}</p>
    </header>
    ${IDEAS.length ? filterBar() : ""}
    ${emptyState(rows.length)}
    <div class="log-groups">${groups.map(idea => {
      const mine = byIdea.get(idea.idea) || [];
      return `<section class="idea${OPEN.has(idea.idea) ? " open" : ""}">
        ${ideaHead(idea, mine.length)}
        ${OPEN.has(idea.idea) ? (mine.length
          ? `<div class="scroll">${table(mine)}</div>`
          : `<p class="hint log-empty">No run in this idea matches the filters. Clearing
             them brings its ${idea.runs} back.</p>`) : ""}
      </section>`;
    }).join("")}</div>
    <div id="compareFoot"></div>
  </div>`;
  wire();
  paintCompareBar();
}

/** Nothing to show, and which kind of nothing it is.
 *
 * The design plan's state table: an empty state says what will appear and offers one button
 * to the next step, and never leaves the words "no data" alone — or, as this page did on a
 * fresh store, a filter bar above blank space. The two kinds of nothing need different
 * sentences and different buttons: a store with no runs in it wants the composer, and a
 * filter that hides all of them wants the filter cleared.
 */
function emptyState(shown){
  if(IDEAS.length && shown) return "";
  if(!IDEAS.length)
    return `<div class="log-blank">
      <h3>No run has been made yet</h3>
      <p>Every backtest that finishes is recorded here, grouped by the idea that owns it,
         with the size of the search under each one. Compose a configuration and run it, and
         the first idea appears.</p>
      <a class="btn" href="#/bench">compose a configuration</a></div>`;
  return `<div class="log-blank">
    <h3>No run matches these filters</h3>
    <p>${RUNS.length} runs are loaded${FILTERS.archived ? "" : " and archived ones are hidden"}.
       Clearing the filters brings them back.</p>
    <button class="btn" type="button" id="clearFiltersEmpty">clear the filters</button></div>`;
}

/** The bar that appears once two runs are ticked. The plan: "Ticking 2 to 4 rows raises a
 *  Compare button." Four is a limit and not a preference — the comparison overlays one line
 *  per run in the four series colours, and a fifth would have no colour of its own. */
function compareBar(){
  if(!TICKED.size) return "";
  const n = TICKED.size;
  return `<div class="compare-bar">
    <span>${n} run${n === 1 ? "" : "s"} ticked</span>
    ${n > 4 ? `<span class="log-warn">four at most — the comparison draws one line per run
      and there are four series colours.</span>` : ""}
    <button type="button" class="btn" id="compareGo"${n < 2 || n > 4 ? " disabled" : ""}
      >compare ${n} runs</button>
    <button type="button" class="chip" id="compareClear">untick all</button>
  </div>`;
}

/** The foot bar, repainted on its own so a tick never redraws the table above it. */
function paintCompareBar(){
  const host = $("compareFoot");
  if(!host) return;
  host.innerHTML = compareBar();
  $("compareGo")?.addEventListener("click", compareTicked);
  $("compareClear")?.addEventListener("click", () => {
    for(const box of document.querySelectorAll("[data-tick]")){
      box.checked = false;
      box.closest("tr")?.classList.remove("ticked");
    }
    TICKED.clear();
    replace({runs: null});
    paintCompareBar();
  });
}

function compareTicked(){
  const ids = [...TICKED];
  if(ids.length < 2 || ids.length > 4) return;
  go("/compare", {runs: ids.join(",")});
}

function wire(){
  for(const head of document.querySelectorAll("[data-idea]"))
    head.addEventListener("click", () => {
      const key = head.dataset.idea;
      if(OPEN.has(key)) OPEN.delete(key); else OPEN.add(key);
      replace({open: [...OPEN].join(",") || null});
      render();
    });

  for(const th of document.querySelectorAll("#view-log [data-sort]"))
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      SORT = SORT.key === key ? {key, dir: -SORT.dir} : {key, dir: -1};
      replace({sort: (SORT.dir < 0 ? "-" : "") + SORT.key});
      render();
    });

  // Ticking a row **does not redraw the list.** It did, and with a hundred rows open that
  // threw away the scroll position on every tick: a reader ticking two runs near the bottom
  // was sent back to the top between them, and the second checkbox they aimed at had moved.
  // Only the row's own mark and the bar at the foot change, which is all that differs.
  for(const box of document.querySelectorAll("[data-tick]"))
    box.addEventListener("change", () => {
      const id = box.dataset.tick;
      if(box.checked) TICKED.add(id); else TICKED.delete(id);
      box.closest("tr")?.classList.toggle("ticked", box.checked);
      replace({runs: [...TICKED].join(",") || null});
      paintCompareBar();
    });

  for(const control of document.querySelectorAll("#view-log [data-filter]"))
    control.addEventListener("change", () => {
      const key = control.dataset.filter;
      const value = control.type === "checkbox"
        ? (control.checked ? "1" : null)
        : (control.value || null);
      FILTERS = {...FILTERS, [key]: value};
      if(value == null) delete FILTERS[key];
      replace({[key]: value});
      // `archived` decides what is fetched, so it re-enters rather than re-filtering.
      if(key === "archived") enter({query: FILTERS});
      else render();
    });

  // Both ways out of a filtered list run the same thing: the bar's own button, and the one in
  // the empty state that explains why the list is empty.
  for(const id of ["clearFilters", "clearFiltersEmpty"])
    $(id)?.addEventListener("click", clearFilters);

  // Metadata is the one thing the plan allows an optimistic update for: the row shows the
  // new verdict at once and the write is reported only if it fails.
  for(const select of document.querySelectorAll("[data-verdict]"))
    select.addEventListener("change", () => saveNote(select.dataset.verdict, "", select.value));
  for(const input of document.querySelectorAll("[data-note]"))
    input.addEventListener("change", () => {
      if(!input.value.trim()) return;
      const verdict = document.querySelector(`[data-verdict="${input.dataset.note}"]`)?.value;
      saveNote(input.dataset.note, input.value.trim(), verdict || "open");
      input.value = "";
      input.placeholder = "written";
    });
}

/** Every filter off, in the URL as well as in memory. */
function clearFilters(){
  const empty = {};
  for(const spec of FILTERS_SPEC) empty[spec.key] = null;
  empty.text = null;
  FILTERS = {};
  replace(empty);
  enter({query: {}});
}

async function saveNote(id, body, verdict){
  const row = RUNS.find(r => String(r.id) === String(id));
  if(row){
    row.last_verdict = verdict;
    if(body) row.n_notes = (row.n_notes || 0) + 1;
  }
  try{
    await postJSON(API.note, {id: Number(id), body, verdict});
  }catch(e){
    notice(`the note on run ${id} was not saved: ${e.message}`);
  }
}

/** The list is re-read on the way in, because a run archived or noted on another surface
 *  has to show here. Only the loaded rows are dropped; the ideas come back with them. */
export function leave(){
  if(UNBIND){ UNBIND(); UNBIND = null; }
  RUNS = [];
}
