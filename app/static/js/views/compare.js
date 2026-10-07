/* views/compare.js — two to four runs, differences first.
 *
 * The design plan's section 5, in its order and for its reason: "Ordered differences first,
 * results second." A reader arriving here already knows the results differ; what they do not
 * know is what was changed, and a page that leads with the metrics invites the conclusion
 * before the cause.
 *
 * Four sections, each answering one question:
 *
 *   1  what differs   the full configuration, differing leaves only, with the reproducibility
 *                     case flagged: two runs that differ only in code or data are not a
 *                     research result, they are a reproducibility event
 *   2  the metrics    rows are metrics, columns are runs, each cell with its interval —
 *                     **and an overlap is marked**, because a difference whose intervals
 *                     overlap must not read as a win
 *   3  the curves     one line per run on one axis, in column order, with the dash pattern
 *                     that survives colour blindness and a projector
 *   4  the trades     which trades only one run took, and which closed differently
 *
 * The comparison URL is the answer: `#/compare?runs=168,170&base=168`. Which runs, which is
 * the baseline and their order are all in it, so the link carries the reading.
 */
"use strict";

import {$, esc} from "../dom.js";
import {API, getJSON} from "../net.js";
import {diff, shown} from "../configdiff.js";
import {REASON, absent, money, moneyShort, num, pct} from "../fmt.js";
import {chartStack, seriesLayer} from "../charts/axis.js";
import {go, replace} from "../router.js";
import {bind} from "../ui/keys.js";

let RUNS = [];          // the records, in column order
let BASE = null;        // the id of the baseline column
let META = null;
let STACK = null;
let UNBIND = null;

/** How many runs can be overlaid: four series colours, four dash patterns. A fifth line
 *  would have to borrow one, and two runs drawn the same way is the one failure a comparison
 *  cannot survive. */
const MAX_RUNS = 4;

/* ── the metrics table ───────────────────────────────────────────────────────────
 *
 * Declared rather than discovered. Reading whatever keys a record happens to carry would make
 * the table's rows depend on which runs are in it — two runs on different engines would show
 * different metrics, and a row missing from a column would read as a zero rather than as
 * "this engine does not produce it".
 *
 * `interval` names where the row's interval lives when it has one. Two rows do; the rest show
 * the value alone, which is honest — a row that drew an interval it did not have would be the
 * exact failure this section exists to prevent.
 */
const METRICS = [
  {name: "net P&L", read: r => r.net_pnl, format: money, interval: r => r.analysis?.intervals?.net_pnl,
   better: "higher"},
  {name: "mean per trade", read: r => r.analysis?.intervals?.mean_pnl?.estimate,
   format: money, interval: r => r.analysis?.intervals?.mean_pnl, better: "higher"},
  {name: "trades", read: r => r.n_trades, format: v => num(v, 0)},
  {name: "win rate", read: r => r.win_rate, format: pct, better: "higher"},
  {name: "annualised", read: r => metrics(r)?.annualised, format: pct, better: "higher"},
  {name: "worst fall", read: r => metrics(r)?.max_drawdown, format: pct, better: "higher"},
  {name: "Sortino", read: r => r.analysis?.investor?.Sortino, format: num, better: "higher"},
  {name: "Calmar", read: r => r.analysis?.investor?.Calmar, format: num, better: "higher"},
  {name: "alpha, annualised", read: r => r.analysis?.investor?.annualised_alpha, format: pct,
   better: "higher"},
  {name: "beta", read: r => r.analysis?.investor?.beta_vs_underlying, format: num},
  {name: "top 5 trades' share", read: r => r.analysis?.investor?.top5_share, format: pct,
   better: "lower"},
  {name: "profit factor", read: r => r.analysis?.trade_stats?.profit_factor, format: num,
   better: "higher"},
  {name: "cost share of gross", read: r => r.analysis?.costs?.fees_pct_of_gross, format: pct,
   better: "lower"},
];

const metrics = r => r.analysis?.equity_metrics || r.equity_metrics || null;

/** The leaves that make a difference a reproducibility event rather than a research change. */
const REPRODUCIBILITY = ["code", "data_version", "environment"];

export async function enter({query}){
  const ids = String(query.runs || "").split(",").map(s => s.trim()).filter(Boolean);
  const host = $("view-compare");
  BASE = query.base || ids[0] || null;
  if(ids.length < 2){
    host.innerHTML = `<div class="pending"><h2>compare</h2>
      <p class="lead">Two to four runs side by side, differences first.</p>
      <p class="hint">Pick them in the experiment log: tick two to four rows and press
        compare.</p>
      <button class="btn" type="button" data-goto="/log">go to the experiment log</button></div>`;
    return;
  }
  if(ids.length > MAX_RUNS){
    host.innerHTML = `<div class="pending"><h2>compare</h2>
      <p class="hint">${ids.length} runs were asked for. The overlay draws one line per run in
      four series colours, each with its own dash pattern, so four is the limit — a fifth
      line would have to borrow another's and two runs drawn the same way is the one thing a
      comparison cannot survive.</p>
      <button class="btn" type="button" data-goto="/log">pick again</button></div>`;
    return;
  }
  host.innerHTML = `<div class="cmp-wrap"><p class="hint">reading ${ids.length} runs…</p></div>`;
  try{
    if(!META) META = await getJSON(API.meta);
    RUNS = await Promise.all(ids.map(id => getJSON(API.run, {id})));
  }catch(e){
    host.innerHTML = `<div class="pending"><h2>compare</h2>
      <p class="hint">${esc(e.message)}</p></div>`;
    return;
  }
  bindKeys();
  render();
}

function bindKeys(){
  if(UNBIND) UNBIND();
  UNBIND = bind("compare", [
    {keys: ["Escape"], does: "go back to the experiment log", run: () => go("/log")},
  ]);
}

/** The column a run is drawn and coloured as. */
const columnOf = id => RUNS.findIndex(r => String(r.id) === String(id));

function head(){
  return `<header class="cmp-head">
    <h2>${RUNS.length} runs compared</h2>
    <p class="hint">The baseline column is the one every difference is measured against.
      Differences come first because the results are the consequence of them.</p>
    <table class="cmp-columns"><tbody><tr>${RUNS.map((r, i) => `
      <td class="cmp-col series-${i + 1}${String(r.id) === String(BASE) ? " base" : ""}">
        <span class="cmp-swatch" aria-hidden="true"></span>
        <a class="run-link" href="#/runs/${r.id}">run ${r.id}</a>
        <span class="cmp-idea">${esc(r.strategy_name || "untitled")}</span>
        <span class="cmp-when">${esc(String(r.started).slice(0, 10))}</span>
        ${String(r.id) === String(BASE)
          ? `<span class="cmp-badge">baseline</span>`
          : `<button type="button" class="chip" data-base="${r.id}"
              data-tip="Measure every difference against run ${r.id} instead.">make baseline</button>`}
        <button type="button" class="chip" data-drop="${r.id}"
          data-tip="Take run ${r.id} out of this comparison.">remove</button>
      </td>`).join("")}</tr></tbody></table>
  </header>`;
}

/* ── 1: what differs ─────────────────────────────────────────────────────────── */

function differences(){
  const items = diff(RUNS.map(r => r.config));
  // The reproducibility case, flagged above everything: identical configurations on
  // different code or data is not a research result.
  const codes = [...new Set(RUNS.map(r => String(r.manifest?.code || "")))];
  const datas = [...new Set(RUNS.map(r => String(r.manifest?.data_version || "")))];
  const sameConfig = items.length === 0;
  const flag = sameConfig && (codes.length > 1 || datas.length > 1)
    ? `<p class="cmp-flag"><b>Same configuration, different ${
        codes.length > 1 ? "code" : "data"}.</b> Whatever differs below is a
       reproducibility event, not a research finding: nothing about the strategy was
       changed.</p>`
    : "";
  const rows = items.length
    ? items.map(({path, values}) => `<tr>
        <th class="left mono">${esc(path)}</th>
        ${values.map((v, i) => `<td class="${String(RUNS[i].id) === String(BASE) ? "base" : ""}"
          >${esc(shown(path, v))}</td>`).join("")}
      </tr>`).join("")
    : `<tr><th class="left" colspan="${RUNS.length + 1}">
        <span class="log-none">Every configuration leaf is identical. The identical branches
        are not listed: there is nothing to highlight in them.</span></th></tr>`;

  // Code and data belong in the same table, because "what differs" includes them — and a
  // reader checking reproducibility should not have to look in two places.
  const provenance = REPRODUCIBILITY.map(key => {
    const values = RUNS.map(r => String(
      key === "environment"
        ? `python ${r.manifest?.environment?.python || "—"}`
        : (r.manifest?.[key] ?? "—")));
    return values.every(v => v === values[0]) ? "" : `<tr class="cmp-prov">
      <th class="left mono">${esc(key)}</th>
      ${values.map(v => `<td class="mono">${esc(v)}</td>`).join("")}</tr>`;
  }).join("");

  return `<section class="cmp-section">
    <h3>what differs</h3>
    ${flag}
    <table class="plain cmp-table"><thead><tr><th class="left">leaf</th>
      ${RUNS.map((r, i) => `<th class="series-${i + 1}">run ${r.id}</th>`).join("")}
      </tr></thead><tbody>${rows}${provenance}</tbody></table>
  </section>`;
}

/* ── 2: the metrics ──────────────────────────────────────────────────────────── */

/** Whether a difference is an improvement, **by the metric's own direction**.
 *
 * Colouring by the sign alone is what this file did first, and it was wrong on three of its
 * thirteen rows: a cost share 1.7 points lower is an improvement and showed in red, and 375
 * fewer trades is neither good nor bad and showed in red as well. A row that does not declare
 * a direction gets no colour, because the page has no opinion to express about it.
 */
function better(metric, delta){
  if(!metric.better || delta === 0) return "";
  const good = metric.better === "higher" ? delta > 0 : delta < 0;
  return good ? "pos" : "neg";
}

/** Whether two intervals overlap. The plan: "overlapping intervals are marked so a
 *  difference that is not significant does not look like a win." */
function overlaps(a, b){
  if(!a || !b || absent(a.low) || absent(b.low)) return null;
  return a.low <= b.high && b.low <= a.high;
}

function metricsTable(){
  const baseAt = Math.max(0, columnOf(BASE));
  return `<section class="cmp-section">
    <h3>the results</h3>
    <p class="hint">Each cell is the figure the backend computed for that run. Where a figure
      has a bootstrap interval it is under the value, and a cell whose interval overlaps the
      baseline's is marked <b>not separated</b> — the two runs have not been shown to differ
      on it, whatever the point estimates say.</p>
    <table class="plain cmp-table"><thead><tr><th class="left">metric</th>
      ${RUNS.map((r, i) => `<th class="series-${i + 1}">run ${r.id}</th>`).join("")}
      </tr></thead><tbody>${METRICS.map(m => {
        const values = RUNS.map(r => m.read(r));
        const ivs = RUNS.map(r => m.interval ? m.interval(r) : null);
        const baseValue = values[baseAt];
        return `<tr><th class="left">${esc(m.name)}${m.better
          ? `<span class="card-dir" aria-hidden="true">${
              m.better === "higher" ? "↑" : "↓"}</span>` : ""}</th>
          ${values.map((v, i) => {
            const isBase = i === baseAt;
            const delta = !isBase && !absent(v) && !absent(baseValue) ? v - baseValue : null;
            const tie = isBase ? null : overlaps(ivs[i], ivs[baseAt]);
            return `<td class="n${isBase ? " base" : ""}">
              <span class="cmp-value">${esc(m.format(v))}</span>
              ${ivs[i] && !absent(ivs[i].low)
                ? `<span class="cmp-iv">${esc(moneyShort(ivs[i].low))} … ${
                    esc(moneyShort(ivs[i].high))}</span>` : ""}
              ${delta != null
                ? `<span class="cmp-delta ${better(m, delta)}"
                    >${esc(m.format(delta))} vs baseline</span>` : ""}
              ${tie === true ? `<span class="cmp-tie">not separated</span>` : ""}
            </td>`;
          }).join("")}</tr>`;
      }).join("")}</tbody></table>
  </section>`;
}

/* ── 3: the curves ───────────────────────────────────────────────────────────── */

/** One date domain for every run, so the lines share an axis even when the runs do not share
 *  a period. A run with no value on a date is a gap in its line and is hatched, never
 *  interpolated — a line drawn across a period a run did not cover would invent it. */
function overlay(host){
  const withCurve = RUNS.filter(r => Array.isArray(r.equity) && r.equity.length);
  if(!withCurve.length){
    host.innerHTML = `<p class="hint">None of these runs kept a daily curve. The trade-table
      engine builds none; rerun on the event-driven engine to compare curves.</p>`;
    return null;
  }
  const dates = [...new Set(withCurve.flatMap(r =>
    r.equity.map(e => String(e.date).slice(0, 10))))].sort();
  const at = new Map(dates.map((d, i) => [d, i]));
  const series = withCurve.map(r => {
    const values = new Array(dates.length).fill(null);
    for(const row of r.equity){
      const i = at.get(String(row.date).slice(0, 10));
      if(i != null) values[i] = row.equity_mid;
    }
    return {name: `run ${r.id}`, values, column: columnOf(r.id) + 1};
  });
  const drawdowns = withCurve.map(r => {
    const values = new Array(dates.length).fill(null);
    for(const row of r.equity){
      const i = at.get(String(row.date).slice(0, 10));
      if(i != null) values[i] = row.drawdown_mid;
    }
    return {name: `run ${r.id}`, values, column: columnOf(r.id) + 1};
  });
  const missing = RUNS.length - withCurve.length;
  const stack = chartStack(host, {
    dates,
    layers: [
      seriesLayer({key: "equity", title: "equity, marked at the mid", height: 240,
                   hint: missing ? `${missing} of these runs kept no curve` : "",
                   series, format: money}),
      seriesLayer({key: "drawdown", title: "drawdown", height: 110,
                   hint: "distance below each run's own running peak",
                   series: drawdowns, format: pct}),
    ],
    onBrush: () => {},
  });
  return stack;
}

/* ── 4: the trades ───────────────────────────────────────────────────────────── */

/** A trade's identity across two runs: the day it opened and the contract it opened.
 *
 * Not `trade_id`, which is a counter per run — the fourth trade of one run and the fourth of
 * another are different trades, and matching on it would report every trade as changed the
 * moment one extra signal fired early in the period. */
const keyOf = t => `${String(t.entry_date).slice(0, 10)}|${t.contract}`;

/** The trade-level diff, for exactly two runs.
 *
 * The plan asks for it "for runs that differ in one slot", which is when it is most readable;
 * it is shown for any two runs because the same three questions apply — which trades only one
 * of them took, which they both took and closed differently, and what that was worth. Above
 * two columns a per-trade table has no shape, so it says so.
 *
 * Every figure is a stored per-trade P&L or the difference of two of them. The difference is
 * subtraction, not a statistic: nothing here is resampled, averaged or annualised.
 */
function trades(){
  if(RUNS.length !== 2)
    return `<section class="cmp-section"><h3>the trades</h3>
      <p class="hint">The trade-level diff compares two runs. With ${RUNS.length} columns
        there is no single pair to difference — remove one to see it.</p></section>`;

  const [a, b] = RUNS;
  // **Duplicates are reported, not dropped.** A `Map` keyed on (day, contract) keeps the last
  // of any pair, and a strategy that may stack — two trades opened on the same contract the
  // same day — would have had one of them vanish out of the diff with nothing saying so.
  // Nothing in the engine forbids it, so the count is checked and named.
  const index = rows => {
    const byKey = new Map();
    let repeated = 0;
    for(const t of rows || []){
      const key = keyOf(t);
      if(byKey.has(key)) repeated++;
      else byKey.set(key, t);
    }
    return {byKey, repeated};
  };
  const {byKey: left, repeated: leftRepeat} = index(a.rows);
  const {byKey: right, repeated: rightRepeat} = index(b.rows);
  const onlyLeft = [...left.keys()].filter(k => !right.has(k));
  const onlyRight = [...right.keys()].filter(k => !left.has(k));
  const changed = [...left.keys()].filter(k => right.has(k)
    && (String(left.get(k).exit_date) !== String(right.get(k).exit_date)
        || left.get(k).exit_reason !== right.get(k).exit_reason));

  const row = (key, t, other) => `<tr>
    <td class="left mono">${esc(String(t.entry_date).slice(0, 10))}</td>
    <td class="left mono">${esc(t.contract)}</td>
    <td class="left">${esc(REASON[t.exit_reason] || t.exit_reason || "—")}${other
      ? ` → ${esc(REASON[other.exit_reason] || other.exit_reason || "—")}` : ""}</td>
    <td class="n ${t.net_pnl >= 0 ? "pos" : "neg"}">${esc(money(t.net_pnl))}</td>
    <td class="n">${other ? esc(money(other.net_pnl)) : "—"}</td>
    <td class="n ${other && other.net_pnl - t.net_pnl >= 0 ? "pos" : "neg"}">${
      other ? esc(money(other.net_pnl - t.net_pnl)) : "—"}</td>
  </tr>`;

  const section = (title, why, rows) => !rows.length ? "" : `
    <h4>${esc(title)} <span class="cmp-n">${rows.length}</span></h4>
    <p class="hint">${esc(why)}</p>
    <div class="scroll"><table class="plain cmp-trades"><thead><tr>
      <th class="left">opened</th><th class="left">contract</th><th class="left">closed by</th>
      <th>run ${a.id}</th><th>run ${b.id}</th><th>difference</th>
    </tr></thead><tbody>${rows.join("")}</tbody></table></div>`;

  return `<section class="cmp-section">
    <h3>the trades</h3>
    <p class="hint">Matched on the day a trade opened and the contract it opened, not on the
      trade number — the fourth trade of one run is not the fourth trade of another. Every
      figure here is a stored per-trade P&amp;L, or the difference of two of them.</p>
    ${leftRepeat || rightRepeat ? `<p class="cmp-flag"><b>${leftRepeat + rightRepeat} trades
      share a day and a contract with another.</b> They cannot be told apart by what this diff
      matches on, so only the first of each pair is compared below and the rest are left
      out — the lists under this are a lower bound on what changed.</p>` : ""}
    ${section(`only run ${a.id} took these`,
      "These trades have no counterpart in the other run: the change stopped them opening.",
      onlyLeft.map(k => row(k, left.get(k), null)))}
    ${section(`only run ${b.id} took these`,
      "The other run opened these and this one did not.",
      onlyRight.map(k => row(k, right.get(k), null)))}
    ${section("both took these and closed them differently",
      "The same contract on the same day, closed by a different rule or on a different day. "
      + "This is the direct readout of what the change did.",
      changed.map(k => row(k, left.get(k), right.get(k))))}
    ${!onlyLeft.length && !onlyRight.length && !changed.length
      ? `<p class="hint">Every trade matches on both sides, and each closed the same way on
         the same day. Whatever differs in the results is not a difference in the trades.</p>`
      : ""}
  </section>`;
}

function render(){
  $("view-compare").innerHTML = `<div class="cmp-wrap">
    ${head()}
    ${differences()}
    ${metricsTable()}
    <section class="cmp-section">
      <h3>the curves</h3>
      <div id="cmpStack"></div>
    </section>
    ${trades()}
  </div>`;

  if(STACK){ STACK.destroy(); STACK = null; }
  STACK = overlay($("cmpStack"));

  for(const button of document.querySelectorAll("[data-base]"))
    button.addEventListener("click", () => {
      BASE = button.dataset.base;
      replace({base: BASE});
      render();
    });
  for(const button of document.querySelectorAll("[data-drop]"))
    button.addEventListener("click", () => {
      const keep = RUNS.filter(r => String(r.id) !== button.dataset.drop).map(r => r.id);
      go("/compare", {runs: keep.join(","), base: keep.includes(Number(BASE)) ? BASE : null});
    });
}

export function leave(){
  if(STACK){ STACK.destroy(); STACK = null; }
  if(UNBIND){ UNBIND(); UNBIND = null; }
  RUNS = [];
}
