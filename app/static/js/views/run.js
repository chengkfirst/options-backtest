/* views/run.js — run analysis: the densest page, and the one most used.
 *
 * The design plan's section 6, in its three tiers:
 *
 *   conclusion  the realism strip, the protocol flags, the metric cards — what this run
 *               is, and whether its numbers can be used at all
 *   process     a stack of chart layers on **one** time axis, brushed together
 *   detail      the trade table, filtered by the brush
 *
 * Two rules shape every line of it.
 *
 * **The backend computes, this renders.** Every number here comes from a field of the run
 * record. There is no arithmetic in this file beyond choosing a format — not a drawdown,
 * not a share, not an interval. A figure the interface worked out for itself is a figure
 * the system cannot reproduce, and the whole point of the record is that it can.
 *
 * **A cut is never a literal.** Every verdict names the threshold behind it, and every
 * threshold comes from `/api/meta`, which reads the one registry in
 * `backtest/core/thresholds.py`. A number written here would turn a judgement into
 * something that reads as a measurement.
 */
"use strict";

import {$, esc, mark} from "../dom.js";
import {API, getJSON, postJSON} from "../net.js";
import {checkContract} from "../contract.js";
import {EXCLUDE, REASON, absent, fmtVal, label, money, moneyShort, num, pct}
  from "../fmt.js";
import {areaLayer, chartStack, lineLayer, underwaterLayer} from "../charts/axis.js";
import {renderAB, renderHist, renderScatter, renderYears} from "../charts/distribution.js";
import {renderCurve} from "../charts/equity.js";
import {renderBuckets, renderCross, renderRobust} from "../charts/tables.js";
import {replace} from "../router.js";

let RUN = null;        // the record being viewed
let META = null;       // /api/meta, for the thresholds
let STACK = null;      // the chart group, so it can be told the brush
let VIEW = [];         // the trades inside the brush — what the detail tier shows
let MARK = "mid";      // which mark the curve is drawn on (display only)
let TAB = "overview";  // which tab is open, so a brush can repaint it

/** The tabs of section 6, and what each one is waiting on where it cannot be built. */
const TABS = [
  {key: "overview", name: "overview"},
  {key: "spread", name: "distribution"},
  {key: "slice", name: "by condition"},
  {key: "attribution", name: "attribution"},
  // Not "trades": the table at the bottom of the page is the trades, and one word for two
  // different things is how a reader stops trusting the labels.
  {key: "trades", name: "exits"},
  {key: "execution", name: "execution"},
  {key: "robustness", name: "robustness"},
  // What was actually set. It was on the composer and nowhere else, so reading a run
  // meant remembering what you had typed — or going back and hoping the form still held
  // it. A result without its settings beside it is half a record.
  {key: "settings", name: "settings"},
  {key: "exposure", name: "exposure",
   pending: "daily portfolio greeks and premium at risk against the book's limits. The "
          + "engine computes them; they are not yet kept with the run, so this tab has "
          + "nothing to read for a run that has finished."},
];

export async function enter({params, query}){
  const id = Number(params.id);
  const host = $("view-run");
  host.innerHTML = `<p class="hint">loading run ${esc(params.id)}…</p>`;
  try{
    if(!META) META = await getJSON(API.meta);
    RUN = await getJSON(API.run, {id});
  }catch(e){
    host.innerHTML = `<div class="pending"><h2>run ${esc(params.id)}</h2>
      <p class="hint">${esc(e.message)}</p></div>`;
    return;
  }
  checkContract(RUN);
  MARK = query.mark === "liq" ? "liq" : "mid";
  VIEW = RUN.rows || [];
  render(query);
}

/* ── the conclusion tier ────────────────────────────────────────────────────── */

/* ── three blocks, three jobs ────────────────────────────────────────────────────
 *
 * They were one list called "Limitations", and it mixed three kinds of statement that a
 * reader uses completely differently:
 *
 *   an assumption   what the result rests on — a synthetic chain, λ = 1.00, a one-session
 *                   fill lag. Conditions, chosen before the run
 *   a limitation    where the numbers stop being reliable — 48 trades, an interval
 *                   containing zero, a censored distribution. Properties of the evidence
 *   a finding       what this particular run did — one trade made 45% of the profit.
 *                   Not a caveat at all: a lead worth following
 *
 * Side by side they diluted each other. "The data is synthetic" and "one trade carried the
 * result" are both true and call for entirely different next actions, and a reader
 * scanning one list of nine grey rows takes neither.
 */

/** What the result rests on. Facts about the run, not controls: a realism assumption is
 *  varied by launching another run, which is the architecture's rule that these are
 *  tested and never tuned. */
function realismStrip(){
  const m = RUN.manifest || {};
  const cfg = m.config || {};
  const ex = m.execution || {};
  const prov = m.provenance || {};
  // Each chip carries what it is **for**. A reader who does not already know what λ is
  // learns nothing from "λ 1.00", and these are the assumptions that decide whether the
  // numbers below mean anything — the least useful place in the interface to be terse.
  const chips = [
    ["period", `${RUN.start_date} → ${RUN.end_date}`,
     "the range the strategy was run over"],
    ["capital", money(cfg.notional_capital),
     "the account the position sizes were worked out against"],
    ["engine", RUN.engine === "event_driven" ? "event driven" : "trade table",
     RUN.engine === "event_driven"
       ? "steps day by day with real portfolio state, so there is an equity curve"
       : "computes each trade independently and builds no equity curve, so no drawdown "
         + "and no Sharpe"],
    ["fill latency", ex.execution_lag_days != null
      ? `${ex.execution_lag_days} session${ex.execution_lag_days === 1 ? "" : "s"}` : "—",
     "how long between the decision and the fill. The signal is read on one close and "
     + "filled on a later one, so nothing is filled on the bar that decided it"],
    ["fill price (λ)", num(cfg.costs?.adverse_fraction),
     "how far across the bid/ask the fill lands. 1.00 is the whole spread paid — the "
     + "pessimistic end. It is tested by a robustness run, never tuned to flatter a result"],
    ["shadow", ex.shadow_days != null ? `${ex.shadow_days} days` : "—",
     "how long a contract keeps being quoted after its trade closed. It is what lets "
     + "\"would a wider stop have worked\" be answered instead of guessed"],
    ["data", prov.all_synthetic ? "synthetic chain"
      : prov.mixed ? `${pct(prov.synthetic_share)} synthetic` : "real",
     prov.all_synthetic
       ? "the underlying's prices are real; the option prices come from Black-Scholes "
         + "with no skew, so option-side conclusions describe the formula, not the market"
       : "where the option prices came from"],
    ["code", m.code || "—",
     "the commit this ran on. A run cannot be reproduced without it"],
    ["result id", m.run_id || "—",
     "the hash of everything that determines this result — config, code and data. Two "
     + "runs sharing it are the same run; two differing in any input cannot share it"],
  ];
  return `<section class="block">
    <h3 class="block-head">Assumptions
      <span class="block-why">what the result rests on. Changed by launching another run,
        never by editing here — an assumption that can be tuned is one that will be.</span>
    </h3>
    <dl class="assume">${chips.map(([k, v, why]) => `
      <div class="assume-item"><dt data-tip="${esc(why)}">${esc(k)}</dt>
        <dd>${esc(v)}</dd></div>`).join("")}</dl>
  </section>`;
}

/* The anomaly scan reports both kinds, so its findings are sorted by what they are
   **about**: the reliability of the evidence, or the behaviour of this strategy. */
const ABOUT_THE_EVIDENCE = new Set([
  "overlap",        // trades that overlap are not independent observations
  "too_good",       // a figure too good to be a measurement of anything
  "rule_failed",    // a rule the run could not hold
]);

/** Where these numbers stop being reliable.
 *
 * Properties of the evidence, each naming the cut behind it, and every cut read from
 * `/api/meta` rather than written here. */
function limitations(){
  const t = (META && META.thresholds) || {};
  const a = RUN.analysis || {};
  const prov = RUN.manifest?.provenance || {};
  const out = [];

  const holdOut = prov.hold_out || {};
  if(!holdOut.hold_out)
    out.push({level: "warn", short: "nothing was held out",
      detail: holdOut.note || "no period was reserved, so no number here is out of sample: "
        + "every one of them was available while the strategy was being chosen."});
  else if(holdOut.touched)
    out.push({level: "warn", short: "the hold-out was revealed",
              detail: holdOut.note || ""});

  const floor = t.bootstrap_min_observations?.value;
  if(floor != null && RUN.n_trades < floor)
    out.push({level: "warn", short: `only ${RUN.n_trades} trades`,
      detail: `below the ${floor} this project requires before a statistic carries an `
            + `interval. ${t.bootstrap_min_observations.because || ""}`});

  for(const interval of Object.values(a.intervals || {})){
    if(!interval) continue;
    if(interval.low <= 0 && 0 <= interval.high)
      out.push({level: "warn", short: `${interval.name}: the sign is not established`,
        detail: `a different sample of the same trades could plausibly have given anything `
              + `from ${money(interval.low)} to ${money(interval.high)}, which includes `
              + `zero. ${interval.caveat || ""}`});
  }

  for(const line of a.excursion?.censoring || [])
    out.push({level: "warn", short: `censored: ${String(line).split(":")[0]}`,
              detail: line});

  for(const found of RUN.anomalies || [])
    if(ABOUT_THE_EVIDENCE.has(found.kind))
      out.push({level: found.severity === "high" ? "bad" : "warn",
                short: found.headline, detail: found.detail});

  const guards = RUN.manifest?.guards || {};
  if(guards.state && guards.state !== "clean")
    out.push({level: "warn", short: `the guards were ${guards.state} for this code`,
              detail: guards.note || ""});

  if(RUN.anomalies === null)
    out.push({level: "warn", short: "no scan was recorded",
      detail: "this run was stored before the interface kept one, which is not the same "
            + "as a scan that found nothing."});

  return rows("Limitations",
    "where these numbers stop being reliable. Each names the cut behind it.", out,
    "Nothing limits these numbers beyond what their sample size allows.");
}

/** What this run actually did that is worth a second look.
 *
 * Not caveats: leads. The scan finds them by rule rather than by asking a model to look,
 * and an empty list shows nothing at all — an insight forced out of nothing is noise, and
 * noise is what makes real warnings get skipped. */
function standsOut(){
  const found = (RUN.anomalies || []).filter(f => !ABOUT_THE_EVIDENCE.has(f.kind));
  if(!found.length) return "";
  return rows("What stands out",
    "found by rule, not by looking. Each is a lead rather than a fault.",
    found.map(f => ({level: f.severity === "high" ? "bad" : "warn",
                     short: f.headline, detail: f.detail})), "");
}

/** One block of openable rows. Shared so the two read identically. */
function rows(title, why, items, whenEmpty){
  if(!items.length)
    return whenEmpty
      ? `<section class="block"><h3 class="block-head">${esc(title)}</h3>
           <p class="block-empty">${esc(whenEmpty)}</p></section>`
      : "";
  return `<section class="block">
    <h3 class="block-head">${esc(title)}
      <span class="block-count">${items.length}</span>
      <span class="block-why">${esc(why)}</span></h3>
    <div class="rows">${items.map(f => `
      <details class="row-item ${f.level}">
        <summary><span class="row-dot" aria-hidden="true"></span>${esc(f.short)}</summary>
        <p>${mark(f.detail)}</p>
      </details>`).join("")}</div>
  </section>`;
}

/** The metric cards: value, interval, and what makes it untrustworthy.
 *
 * Every entry names the field it came from. A card with no field is not invented — it
 * says what would have to exist for it to appear, which is criterion 2.7 applied to a
 * number rather than to a feature. */
function metricCards(){
  const a = RUN.analysis || {};
  const eq = a.equity_metrics || RUN.equity_metrics || null;
  const inv = a.investor || null;
  const t = (META && META.thresholds) || {};
  const floor = t.bootstrap_min_observations?.value ?? 0;
  const thin = RUN.n_trades < floor;

  // Every card carries a definition and, where it has one, the direction that is better.
  // A reader who meets "Calmar -0.44" with no explanation has been given a number and no
  // way to use it, which is worse than giving nothing.
  const cards = [
    {name: "annualised", value: eq && pct(eq.annualised), better: "higher",
     means: "the compound yearly rate this equity curve grew at over the period",
     missing: "needs an equity curve, which only the event-driven engine builds. Rerun on "
            + "the event-driven engine for this one"},
    {name: "max drawdown", value: eq && pct(eq.max_drawdown), better: "closer to zero",
     means: "the deepest fall from a peak of the account, at any point in the run. It is "
          + "what a person would actually have had to sit through",
     note: eq && `at the bid: ${pct(eq.max_drawdown_liq)}`,
     missing: "needs an equity curve, which only the event-driven engine builds"},
    {name: "Sortino", value: inv && num(inv.Sortino), better: "higher",
     means: "return per unit of **downside** volatility. Unlike a Sharpe it does not "
          + "penalise upside swings, which matters for an option buyer whose good days "
          + "are large by design",
     note: inv && inv.downside_vol != null ? `downside vol ${pct(inv.downside_vol)}` : "",
     missing: "needs an equity curve, which only the event-driven engine builds"},
    // The comparison the page was missing, and the first card a reader should be able to
    // find: a return shown alone cannot be judged. Beta and alpha ride with it rather than
    // taking cards of their own — the plan allows eight, and ten wrapped onto an orphaned
    // second row where the eye stopped before reaching them.
    {name: "vs buy and hold", better: "higher",
     value: inv && inv.buy_and_hold != null && inv.strategy_total != null
       ? pct(inv.strategy_total - inv.buy_and_hold) : null,
     note: inv && inv.buy_and_hold != null
       ? `${pct(inv.strategy_total)} against ${pct(inv.buy_and_hold)}`
         + (inv.beta_vs_underlying != null ? ` · beta ${num(inv.beta_vs_underlying)}` : "")
       : "",
     means: "the strategy's total return less what holding the underlying returned over "
          + "the same period. Negative is the work losing to doing nothing. The beta "
          + "beside it says how much of the return was simply the underlying moving — "
          + "near 1 the strategy is the underlying in disguise, above it the underlying "
          + "with leverage",
     missing: "needs an equity curve and a benchmark series"},
    {name: "net P&L", value: money(RUN.net_pnl), interval: a.intervals?.net_pnl,
     means: "what the whole run made or lost after costs"},
    {name: "mean per trade", value: RUN.n_trades ? money(RUN.net_pnl / RUN.n_trades) : "—",
     interval: a.intervals?.mean_pnl,
     means: "the net P&L spread over the trades. The interval beneath it is the backend's "
          + "bootstrap, not an average of averages",
     // The division is the one arithmetic left in this file, and it is the presentation of
     // two stored numbers rather than a statistic of its own.
     note: `over ${RUN.n_trades} trades`},
    {name: "win rate", value: pct(RUN.win_rate),
     means: "the share of trades that finished above zero. It says nothing about size — a "
          + "strategy can win often and still lose money"},
    {name: "top 5 trades' share", value: inv && pct(inv.top5_share), better: "lower",
     means: "how much of the total profit came from the five best trades. A high share "
          + "means the result rests on a handful of outcomes rather than on the rule",
     note: inv && inv.without_top5 != null ? `without them ${money(inv.without_top5)}` : "",
     missing: "the run was profitable in too few trades for this to be meaningful, or the "
            + "total was negative, in which case a share of it would not mean anything"},
  ];

  return `<dl class="cards">${cards.map(c => {
    const absentCard = c.value == null || c.value === "—" || c.value === false;
    const tip = [c.means ? c.means.replace(/\*\*/g, "") : "",
                 c.better ? `${c.better} is better.` : "",
                 thin ? `Fewer than ${floor} trades, so this number is shown but cannot `
                      + `carry weight.` : ""].filter(Boolean).join(" ");
    return `<div class="card${thin ? " thin" : ""}${absentCard ? " empty" : ""}">
      <dt data-tip="${esc(tip)}">${esc(c.name)}${c.better
        ? `<span class="card-dir" aria-hidden="true">${
            c.better === "higher" ? "↑" : c.better === "lower" ? "↓" : "→0"}</span>` : ""}</dt>
      <dd>${absentCard ? "—" : esc(c.value)}</dd>
      ${absentCard && c.missing ? `<p class="card-why">${esc(c.missing)}</p>` : ""}
      ${!absentCard && c.note ? `<p class="card-note">${esc(c.note)}</p>` : ""}
      ${c.interval ? intervalBar(c.interval) : ""}
    </div>`;
  }).join("")}</dl>`;
}

/** A thin bar beneath the value: the range a different sample would plausibly have given.
 *  Marked when it contains zero, because then the sign is not established and the point
 *  estimate alone would read as a result. */
function intervalBar(interval){
  const crosses = interval.low <= 0 && 0 <= interval.high;
  const span = interval.high - interval.low || 1;
  const zeroAt = Math.max(0, Math.min(100, ((0 - interval.low) / span) * 100));
  // Money formatting and a whole-number level. "95.0% interval -88048 … -36070" wrapped
  // onto a second line and read as four unrelated numbers; the grouped form fits.
  const level = `${Math.round(interval.level * 100)}%`;
  const why = `What a different sample of the same trades would plausibly have given. `
    + `${crosses ? "It contains zero, so the sign of this result is not established. " : ""}`
    + `${(interval.caveat || "").replace(/\*\*/g, "")}`;
  return `<div class="interval${crosses ? " crosses" : ""}" data-tip="${esc(why)}">
    <span class="interval-bar">${crosses
      ? `<i class="interval-zero" style="left:${zeroAt.toFixed(1)}%"></i>` : ""}</span>
    <span class="interval-text">${level} ${moneyShort(interval.low)} … ${
      moneyShort(interval.high)}${crosses ? " · spans 0" : ""}</span>
  </div>`;
}

/* ── the process tier ───────────────────────────────────────────────────────── */

/** The reserved period, as the chart stack wants it, or null when none was declared.
 *
 * It was written as a ternary returning null on both branches — dead code, so the hatch
 * never appeared and nothing said why. The note the backend writes is prose; the range
 * itself is the string it reports, which is why this parses rather than guesses. */
function holdOutRange(){
  const held = RUN.manifest?.provenance?.hold_out?.hold_out;
  if(!held) return null;
  const m = String(held).match(/(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})/);
  return m ? {from: m[1], to: m[2]} : null;
}

function buildStack(host){
  const equity = RUN.equity;
  if(!equity || !equity.length){
    host.innerHTML = `<p class="hint">This run has no daily curve. ${
      RUN.engine === "event_driven"
        ? "It was stored before the interface kept one — run the configuration again to get it."
        : "The trade-table engine computes each trade independently and builds no equity curve, so there is no drawdown and no Sharpe. Rerun on the event-driven engine for those."
    }</p>`;
    return null;
  }
  const dates = equity.map(r => String(r.date).slice(0, 10));
  const curve = equity.map(r => r[MARK === "liq" ? "equity_liq" : "equity_mid"]);
  const other = equity.map(r => r[MARK === "liq" ? "equity_mid" : "equity_liq"]);
  const drawdown = equity.map(r => r[MARK === "liq" ? "drawdown_liq" : "drawdown_mid"]);
  const benchmark = equity.map(r => r.benchmark ?? null);
  const capital = RUN.manifest?.config?.notional_capital;

  // Risk events and the range truncation, read from this run's own diagnostics.
  const marks = [];
  for(const d of RUN.diagnostics || [])
    if(d.first) marks.push({date: d.first, kind: d.kind, label: `${d.kind} (${d.count})`});

  const stack = chartStack(host, {
    dates,
    holdOut: holdOutRange(),
    marks,
    brush: null,
    layers: [
      lineLayer({
        key: "equity", title: `equity, marked at the ${MARK === "liq" ? "bid" : "mid"}`,
        // The benchmark is the comparison that makes the line mean anything: a strategy
        // return shown alone cannot be judged, and this one is beaten by simply holding
        // the underlying. Where no benchmark was stored the other mark takes the second
        // line, which is the previous behaviour.
        hint: benchmark.some(v => v != null)
          ? "the thin line is buying and holding the underlying"
          : "the thin line is the other mark",
        height: 160,
        values: curve,
        second: benchmark.some(v => v != null) ? benchmark : other,
        secondTitle: benchmark.some(v => v != null)
          ? "buy and hold the underlying, from the same capital"
          : (MARK === "liq" ? "marked at the mid" : "liquidation value, at the bid"),
        baseline: curve[0], format: money,
      }),
      drawdown.some(v => v != null) && underwaterLayer({
        key: "drawdown", title: "drawdown", hint: "distance below the running peak",
        height: 86, values: drawdown, format: pct,
      }),
      areaLayer({
        key: "deployed", title: "capital deployed", hint: "against the account",
        height: 86, values: equity.map(r => r.deployed), limit: capital, format: money,
      }),
      areaLayer({
        key: "positions", title: "positions held", height: 64,
        values: equity.map(r => r.positions), format: v => num(v, 0),
        colour: "var(--slot-6)",
      }),
    ],
    onBrush: range => {
      applyBrush(range);
      replace({range: range ? `${range.from}..${range.to}` : null});
    },
  });

  // A layer that cannot be drawn says so rather than disappearing. The drawdown is a
  // metric the backend computes and stores with the curve; a run from before that column
  // existed has none, and quietly showing three layers where the design has four would
  // read as a design with three. Appended after the stack, which clears its own root.
  if(!drawdown.some(v => v != null)){
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = "No drawdown layer: this run's curve was stored before the backend "
      + "kept one. A drawdown is a metric, so the interface does not work it out here — "
      + "run the configuration again to get the layer.";
    host.append(note);
  }
  return stack;
}

/** What a brushed range does: narrow the detail tier to the trades opened inside it.
 *
 * One function for both the drag and the restore-from-URL, so the two cannot drift —
 * which they had, leaving a reloaded link showing a shaded range over the full table. */
function applyBrush(range){
  VIEW = range
    ? (RUN.rows || []).filter(r => r.entry_date >= range.from && r.entry_date <= range.to)
    : (RUN.rows || []);
  paintDetail();
  // "Brushing filters every chart and the trade table" — so the open tab is repainted
  // too, unless it is the stack itself, which drew the brush in the first place.
  if(TAB && TAB !== "overview") renderTab(TAB, {});
}

/* ── the detail tier ────────────────────────────────────────────────────────── */

const COLUMNS = [
  {key: "entry_date", name: "opened"},
  {key: "exit_date", name: "closed"},
  {key: "contract", name: "contract", mono: true, left: true},
  {key: "quantity", name: "qty", fmt: v => num(v, 0)},
  {key: "entry_price", name: "in", fmt: v => num(v)},
  {key: "exit_price", name: "out", fmt: v => num(v)},
  {key: "net_pnl", name: "net P&L", fmt: money, sign: true},
  {key: "exit_reason", name: "closed by", left: true, fmt: v => REASON[v] || v || "—"},
  {key: "holding_days", name: "held", fmt: v => num(v, 0)},
  {key: "entry_iv", name: "IV in", fmt: pct},
  {key: "entry_delta", name: "Δ in", fmt: v => num(v, 3)},
];

function paintDetail(){
  const box = $("runDetail");
  if(!box) return;
  const rows = VIEW;
  $("runDetailNote").textContent = rows.length === (RUN.rows || []).length
    ? `${rows.length.toLocaleString()} trades`
    : `${rows.length.toLocaleString()} of ${(RUN.rows || []).length.toLocaleString()} trades in the brushed range`;
  if(!rows.length){
    box.innerHTML = `<p class="hint">No trade opened inside the brushed range. Clear the
      brush by clicking once on a chart.</p>`;
    return;
  }
  const shown = rows.slice(0, 400);
  box.innerHTML = `<table class="trades"><thead><tr>${
    COLUMNS.map(c => `<th${c.left ? ' class="left"' : ""}>${esc(c.name)}</th>`).join("")
  }</tr></thead><tbody>${shown.map((r, i) => `<tr tabindex="0" data-row="${i}">${
    COLUMNS.map(c => {
      const raw = r[c.key];
      const text = c.fmt ? c.fmt(raw) : (raw ?? "—");
      const cls = [c.left ? "left" : "", c.mono ? "mono" : "",
                   c.sign && !absent(raw) ? (raw > 0 ? "pos" : raw < 0 ? "neg" : "") : ""]
                  .filter(Boolean).join(" ");
      return `<td${cls ? ` class="${cls}"` : ""}>${esc(text)}</td>`;
    }).join("")
  }</tr>`).join("")}</tbody></table>${
    rows.length > shown.length
      ? `<p class="hint">The first ${shown.length} are shown.</p>` : ""}`;
}

/* ── the page ───────────────────────────────────────────────────────────────── */

function render(query){
  const tab = TABS.some(t => t.key === query.tab) ? query.tab : "overview";
  $("view-run").innerHTML = `
    <section class="tier-top">
      <header class="run-head">
        <h2>run ${RUN.id}<span class="run-idea"
          data-tip="The strategy this run belongs to, and which version of it. Editing the strategy makes a new version; running it again makes a new run."
          >${esc(RUN.strategy_name || "untitled")} · version ${
          RUN.strategy_ver ?? "?"}</span></h2>
        <p class="run-cfg" data-tip="The entry condition this run used.">enters when ${
          esc(typeof RUN.config?.entry === "string"
            ? RUN.config.entry : "a plan function decides")}</p>
        <div class="run-actions">
          <button type="button" class="chip" id="markToggle"
            data-tip="Display only. An option can be marked at the mid price or at the bid — what it could actually be sold for. Switching this redraws the curve; it does not recompute the metrics, which were worked out on the mark the run was configured with."
            >showing the ${MARK === "liq" ? "bid" : "mid"} mark</button>
          <a class="chip" href="#/bench"
             data-tip="The bench is where a configuration is written and run.">compose a run</a>
        </div>
      </header>
      <p class="run-lead">What this run did, and whether its numbers can be trusted.
        Read the limitations first — they are the reasons a number here might not mean
        what it appears to.</p>
      ${realismStrip()}
      ${limitations()}
      ${standsOut()}
      ${metricCards()}
    </section>

    <nav class="run-tabs" role="tablist">${TABS.map(t =>
      `<button type="button" class="tab${t.key === tab ? " on" : ""}" role="tab"
         data-tab="${t.key}">${esc(t.name)}</button>`).join("")}</nav>

    <section class="tier-mid" id="tierMid"></section>

    <section class="tier-detail">
      <header class="tier-head"><h3>every trade</h3>
        <p class="hint">one row per closed trade, in the order they opened</p>
        <p class="hint tier-count" id="runDetailNote"></p></header>
      <div class="scroll" id="runDetail"></div>
    </section>`;

  $("markToggle").addEventListener("click", () => {
    MARK = MARK === "mid" ? "liq" : "mid";
    replace({mark: MARK});
    render({...query, mark: MARK});
  });
  for(const button of $("view-run").querySelectorAll("[data-tab]"))
    button.addEventListener("click", () => {
      replace({tab: button.dataset.tab});
      render({...query, tab: button.dataset.tab});
    });

  renderTab(tab, query);
  paintDetail();
}

function renderTab(tab, query){
  TAB = tab;
  const host = $("tierMid");
  const spec = TABS.find(t => t.key === tab);
  if(spec && spec.pending){
    host.innerHTML = `<div class="pending" style="padding:var(--space-5)">
      <h2>${esc(spec.name)}</h2><p class="hint"><b>Not built yet.</b> ${
        esc(spec.pending)}</p></div>`;
    return;
  }
  if(tab === "overview"){
    host.innerHTML = `<p class="hint stack-lead">Every layer shares one time axis. Hover
      to read them all at one date; <b>drag across a chart to select a period</b> — the
      trade table below narrows to it and the link in the address bar carries it. Click
      once to clear. The triangles on the axis mark days the run logged something.</p>`;
    const box = document.createElement("div");
    host.append(box);
    if(STACK) STACK.destroy();
    STACK = buildStack(box);
    if(STACK && query.range){
      const [from, to] = String(query.range).split("..");
      if(from && to){
        STACK.setBrush({from, to});
        // The shading alone is not the view state. Restoring the brush has to restore
        // what the brush *does* — a reloaded link that showed a shaded range over the
        // full trade table would be telling the reader two different things at once.
        applyBrush({from, to});
      }
    }
    return;
  }
  if(tab === "settings"){ host.innerHTML = settingsTab(); return; }
  if(tab === "spread"){ spreadTab(host); return; }
  if(tab === "slice"){ sliceTab(host); return; }
  if(tab === "attribution"){ host.innerHTML = attributionTab(); return; }
  if(tab === "trades"){ host.innerHTML = exitReasonTab(); return; }
  if(tab === "execution"){ host.innerHTML = costsTab(); return; }
  if(tab === "robustness"){
    host.innerHTML = robustnessTab();
    for(const b of host.querySelectorAll("[data-valid]"))
      b.addEventListener("click", () => runValidation(b.dataset.valid, b));
    return;
  }
}

/* The two exploratory tabs. Both read `VIEW`, which is the brushed set, so selecting a
   period on the curve narrows every chart here as well as the trade table — the design
   plan's "brushing filters every chart and the trade table". */

function spreadTab(host){
  host.innerHTML = `<div class="pad">
    <p class="hint">Where the outcomes sit, and whether a few of them carry the result.
      ${VIEW.length === (RUN.rows || []).length ? ""
        : `<b>Showing the ${VIEW.length} trades in the brushed period.</b>`}</p>
    <h4>cumulative P&amp;L, trade by trade</h4>
    <p class="hint">The equity curve above marks the book every day; this adds up the
      closed trades in the order they closed. A staircase with one tall step is a result
      resting on one trade.</p>
    <div class="chart"><svg id="curve" role="img" aria-label="cumulative P&amp;L"></svg></div>
    <p class="hint" id="curveNote"></p>
    <div class="two-up">
      <section><h4>per-trade P&amp;L</h4>
        <div class="chart"><svg id="hist" role="img"
          aria-label="per-trade P&amp;L distribution"></svg></div></section>
      <section><h4>by year</h4>
        <div class="chart"><svg id="years" role="img" aria-label="P&amp;L by year"></svg></div>
      </section>
    </div>
    <h4>does it hold without the best few trades</h4>
    <p class="hint">A sign flip means the result rests on a handful of outcomes rather
      than on the rule.</p>
    <table class="plain" id="robust"></table></div>`;
  renderCurve(VIEW, false);
  renderHist(VIEW);
  renderYears(VIEW);
  renderRobust(VIEW);
}

function sliceTab(host){
  const cols = numericCols(RUN.rows || []);
  if(!cols.length){
    host.innerHTML = `<p class="hint">This run's trade log has no numeric column to cut
      by.</p>`;
    return;
  }
  const options = cols.map(c => `<option value="${esc(c)}">${esc(label(c))}</option>`).join("");
  host.innerHTML = `<div class="pad">
    <p class="hint">Split the trades into equal-sized groups by one measurement taken at
      entry, and see whether the outcome moves with it. A single group standing out is
      more likely noise than a signal; a run of groups moving one way is the thing worth
      following.</p>
    <div class="slice-controls">
      <label>group by <select id="sliceDim">${options}</select></label>
      <label>groups <select id="sliceBins">
        <option>3</option><option selected>4</option><option>5</option><option>6</option>
      </select></label>
      <label>cross with <select id="sliceDim2">
        <option value="">(nothing)</option>${options}</select></label>
    </div>
    <table class="plain" id="buckets"></table>
    <div class="two-up">
      <section><h4>this measurement against P&amp;L</h4>
        <p class="hint" id="scatNote"></p>
        <div class="chart"><svg id="scatter" role="img"
          aria-label="dimension against P&amp;L"></svg></div></section>
      <section><h4>the two halves</h4>
        <p class="hint">Split at the median of the chosen measurement.</p>
        <div class="ab" id="ab"></div></section>
    </div>
    <section id="crossWrap" hidden><h4>crossed groups</h4>
      <p class="hint">Each cell is the mean per trade. Cells with few trades are dimmed —
        extremes there are usually noise.</p>
      <table class="grid-tab" id="cross"></table></section>
    <p class="hint" id="dimNote"></p></div>`;

  const paint = () => {
    const col = $("sliceDim").value, k = +$("sliceBins").value, col2 = $("sliceDim2").value;
    renderBuckets(VIEW, col, k);
    renderScatter(VIEW, col);
    renderAB(VIEW, col);
    $("crossWrap").hidden = !col2;
    if(col2) renderCross(VIEW, col, col2, k);
  };
  for(const id of ["sliceDim", "sliceBins", "sliceDim2"])
    $(id).addEventListener("change", paint);
  $("sliceDim").value = cols.includes("entry_iv") ? "entry_iv" : cols[0];
  paint();
}

/** Which trade columns can be grouped by: numeric, and not an outcome.
 *  Grouping the result by the result is the one cut that always "works". */
function numericCols(rows){
  if(!rows.length) return [];
  return Object.keys(rows[0]).filter(c =>
    !EXCLUDE.has(c) && typeof rows[0][c] === "number" && Number.isFinite(rows[0][c]));
}

/** Every setting this run was given, **generated from the component definitions**.
 *
 * The groups, their order, the fields in each and what each field means all come from
 * `META.form`, which the backend builds from `backtest/core/slots.py` — the same
 * definition the composer draws its form from. It was hand-written here once, and that is
 * a second copy of a definition that will change: the moment a component asks for
 * something different, on whatever professional grounds, the composer follows it and a
 * hand-written reader goes quietly stale. One definition, two renderings.
 *
 * The values are found by field name across the stored configuration, because which
 * sub-object a field landed in (`select`, `exit`, `sizing`, or the top level) is the
 * backend's filing decision and not something this view should know.
 */
function settingsTab(){
  const c = RUN.config || {};
  const form = META?.form;
  if(!form || !form.length)
    return `<p class="hint">The component definitions could not be fetched, so there is
      nothing to lay these settings out against.</p>`;

  // Every place a field's value might have been filed. Searched in order; the first hit
  // wins, which is well defined because field names are unique across the form.
  const SOURCES = [c, c.select || {}, c.exit || {}, c.sizing || {}];
  const valueOf = name => {
    for(const where of SOURCES)
      if(where && Object.prototype.hasOwnProperty.call(where, name) && where[name] != null
         && where[name] !== "")
        return where[name];
    return null;
  };

  // A slot filled by a registered form, keyed by slot. Which `run()` argument it arrived
  // through is the backend's business; both are shown at the slot they decide.
  const byForm = {...(c.components || {})};
  if(c.risk?.variant) byForm.headwind = c.risk;

  const groups = form.flatMap(layer => layer.components.map(comp => {
    const chosen = byForm[comp.key];
    const rows = [];
    if(chosen)
      rows.push([`decided by the ${chosen.variant} form`,
                 chosen.version != null ? `version ${chosen.version}` : "yes",
                 "a registered form decides this slot in place of the setting below"],
                ...Object.entries(chosen.params || {}).map(([k, v]) => [`　${k}`, v, ""]));
    for(const field of comp.inputs || []){
      // The field a chosen form replaced is not missing, it is superseded, and the row
      // above already says so.
      if(chosen && field.name === comp.key) continue;
      const v = valueOf(field.name);
      if(v == null) continue;
      rows.push([field.label, field.unit ? `${v} ${field.unit}` : v, field.help || ""]);
    }
    return {name: comp.name, rows, gap: comp.gap, status: comp.status,
            inputs: (comp.inputs || []).length};
  }));

  return `<div class="pad">
    <p class="hint">Everything this run was given, laid out as the seven components that
      asked for it. A component with nothing shown was left alone — which is a different
      statement from one that cannot be configured yet, and those say so.</p>
    <div class="settings">${groups.map(g => `
      <section class="setting-group">
        <h4>${esc(g.name)}</h4>
        ${g.rows.length ? `<dl>${g.rows.map(([k, v, why]) => `
          <dt${why ? ` data-tip="${esc(why)}"` : ""}>${esc(k)}</dt>
          <dd>${esc(String(v))}</dd>`).join("")}</dl>` : ""}
        ${!g.rows.length ? `<p class="setting-none">${
          g.inputs ? "Left alone." : esc(shortGap(g))}</p>` : ""}
      </section>`).join("")}
      <section class="setting-group"><h4>how it was run</h4><dl>
        <dt>symbol</dt><dd>${esc(c.symbol ?? "—")}</dd>
        <dt>period</dt><dd>${esc(`${c.start} → ${c.end}`)}</dd>
        <dt>engine</dt><dd>${esc(c.engine === "event_driven" ? "event driven" : "trade table")}</dd>
        ${c.max_concurrent != null
          ? `<dt>max concurrent</dt><dd>${esc(String(c.max_concurrent))}</dd>` : ""}
      </dl></section>
    </div>
    ${c.entry_code || c.plan_code || c.signals_code ? `
      <h4 class="code-head">the code this run carried</h4>
      ${[["entry", c.entry_code], ["plan", c.plan_code], ["signals", c.signals_code]]
        .filter(([, code]) => code && code.trim())
        .map(([what, code]) => `<details class="code-block"><summary>${esc(what)}</summary>
          <pre>${esc(code)}</pre></details>`).join("")}` : ""}
  </div>`;
}

/** Why a component shows nothing: its own words, trimmed to a line. */
function shortGap(g){
  const text = (g.gap || "").replace(/\*\*/g, "");
  const first = text.split(/[;.]/)[0] || "nothing to configure yet";
  return first.length > 90 ? first.slice(0, 90) + "…" : first;
}

function attributionTab(){
  const attr = RUN.attribution || RUN.analysis?.attribution;
  if(!attr || !attr.totals)
    return `<p class="hint">No attribution for this run.</p>`;
  const names = ["delta", "gamma", "vega", "theta", "residual"];
  const meaning = {delta: "direction", gamma: "movement", vega: "implied volatility",
                   theta: "time", residual: "what the approximation could not place"};
  const total = names.reduce((a, n) => a + Math.abs(attr.totals[n] || 0), 0) || 1;
  return `<div class="pad">
    <p class="hint">Where the money came from. A large residual means the second-order
      approximation failed and those trades' attribution cannot be trusted.</p>
    <table class="plain"><tbody>${names.map(n => {
      const v = attr.totals[n] || 0;
      const w = (Math.abs(v) / total * 100).toFixed(1);
      return `<tr>
        <th class="left">${n}<span class="sub">${meaning[n]}</span></th>
        <td class="bar"><span style="width:${w}%;background:var(--greek-${n})"></span></td>
        <td class="n ${v >= 0 ? "pos" : "neg"}">${money(v)}</td></tr>`;
    }).join("")}</tbody></table>
    ${attr.high_residual
      ? `<p class="hint warn-text">${attr.high_residual} trade(s) have too large a
         residual to trust.</p>` : ""}</div>`;
}

function exitReasonTab(){
  const rows = RUN.analysis?.by_exit_reason;
  if(!rows || !rows.length) return `<p class="hint">No exit breakdown for this run.</p>`;
  const cols = Object.keys(rows[0]);
  return `<div class="pad"><p class="hint">What closed each trade, and what each kind
    was worth. The slot that closed a trade is the readout of what that slot changed.</p>
    <table class="plain"><thead><tr>${cols.map(c =>
      `<th${c === cols[0] ? ' class="left"' : ""}>${esc(label(c))}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r => `<tr>${cols.map((c, i) => {
      const v = r[c];
      return `<td class="${i ? "n" : "left"}">${
        typeof v === "number" ? esc(fmtVal(c, v)) : esc(REASON[v] || v)}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function costsTab(){
  const costs = RUN.analysis?.costs;
  if(!costs) return `<p class="hint">No cost breakdown for this run.</p>`;
  const t = META?.thresholds?.cost_share_warning;
  return `<div class="pad">
    <p class="hint">What the trading cost, against what it made. Live costs above
      backtest costs is the norm, which is what the doubled-cost robustness run tests.</p>
    <dl class="cards">${Object.entries(costs).map(([k, v]) => `
      <div class="card"><dt>${esc(label(k))}</dt>
        <dd>${esc(typeof v === "number" ? (Math.abs(v) < 1 ? pct(v) : money(v)) : String(v))}</dd>
      </div>`).join("")}</dl>
    ${t ? `<p class="hint">The cut this project uses: costs above
      ${pct(t.value)} of gross are flagged — ${esc(t.because || "")}</p>` : ""}</div>`;
}

/* The research tools, run on demand.
 *
 * On demand because each re-runs the strategy — ablation once per slot, baselines once
 * per draw — so running them with every backtest would make the run button slow for
 * something not always wanted.
 *
 * The buttons are rendered from `META.validations`, which the backend builds from the
 * runner's own declaration: a tool added there appears here with no edit on this side,
 * which is the only arrangement in which the two cannot drift apart.
 *
 * The output is text, deliberately. Every one of these reports **leads with the boundary
 * on its own conclusions** — on the synthetic chain an exit level describes Black-Scholes,
 * not the market. Splitting the report into fields for this side to re-assemble is exactly
 * how that line gets dropped. */
const VALID_INPUTS = {
  baselines: [{key: "draws", label: "random draws", value: 50, hint: "at least 10"}],
  counterfactual: [
    {key: "take_profits", label: "take profits", value: "0.5, 1.0, 1.5", text: true},
    {key: "stop_losses", label: "stop losses", value: "0.3, 0.5, 0.7", text: true},
  ],
  trials: [{key: "years", label: "years covered", value: "",
            hint: "the stored Sharpe is annualised, so the period count must be in years"}],
  ablation: [],
};

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
  out.textContent = "Running. This re-runs the strategy, so it takes about as long as "
    + "the backtest did.";
  try{
    const data = await postJSON(API.validate, {
      id: RUN.id, kind, params: validParams(kind),
    });
    out.innerHTML = mark(data.text);
  }catch(e){
    // Both a refusal and a dropped connection land here, and both belong in the report's
    // own box rather than the problem strip: "the counterfactual needs its levels" is an
    // instruction, not a fault.
    out.textContent = e.message;
  }finally{
    btn.disabled = false;
    btn.textContent = was;
  }
}

function robustnessTab(){
  const scenarios = RUN.scenarios || RUN.analysis?.scenarios;
  const offered = new Set(RUN.validations || []);
  return `<div class="pad">
    <p class="hint">The research tools from the architecture's register, run against this
      run. Each re-runs the strategy, so none of them runs until asked. Every report says
      first what its numbers can and cannot be used for.</p>
    <ul class="checks">${(META?.validations || []).map(v => {
      const can = offered.has(v.kind);
      const inputs = (VALID_INPUTS[v.kind] || []).map(i =>
        `<label for="v_${v.kind}_${i.key}">${esc(i.label)}</label>
         <input id="v_${v.kind}_${i.key}" value="${esc(i.value)}"
                ${i.hint ? `data-tip="${esc(i.hint)}"` : ""}>`).join("");
      return `<li class="${can ? "can" : "cannot"}">
        <b>${esc(v.label)}</b><span>${esc(v.why)}</span>
        ${can ? `<div class="vrow">${inputs}
            <button type="button" class="btn ghost" data-valid="${v.kind}">run it</button>
          </div>`
          : `<em>needs the ${(v.engines || []).join(" or ")} engine; this run used ${
              esc(RUN.engine)}</em>`}
      </li>`;
    }).join("")}</ul>
    <pre id="validOut" class="valid-out" hidden></pre>
    ${scenarios && scenarios.length ? `
      <h4>historical scenarios</h4>
      <p class="hint">Real windows, so no approximation — more trustworthy than a stress
        test.</p>
      <table class="plain"><thead><tr><th class="left">scenario</th><th>from</th><th>to</th>
        <th>sessions</th><th>return</th><th>max drawdown</th></tr></thead>
      <tbody>${scenarios.map(s => `<tr>
        <td class="left">${esc(s.scenario)}</td><td>${esc(s.from)}</td><td>${esc(s.to)}</td>
        <td class="n">${num(s.sessions, 0)}</td>
        <td class="n">${s.sessions ? pct(s["return"]) : "—"}</td>
        <td class="n">${s.sessions ? pct(s["max drawdown"]) : "—"}</td></tr>`).join("")}
      </tbody></table>` : ""}</div>`;
}

export function leave(){
  if(STACK){ STACK.destroy(); STACK = null; }
}
