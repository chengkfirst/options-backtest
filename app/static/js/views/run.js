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
import {API, getJSON} from "../net.js";
import {checkContract} from "../contract.js";
import {REASON, absent, fmtVal, label, money, num, pct} from "../fmt.js";
import {areaLayer, chartStack, lineLayer, underwaterLayer} from "../charts/axis.js";
import {replace} from "../router.js";

let RUN = null;        // the record being viewed
let META = null;       // /api/meta, for the thresholds
let STACK = null;      // the chart group, so it can be told the brush
let VIEW = [];         // the trades inside the brush — what the detail tier shows
let MARK = "mid";      // which mark the curve is drawn on (display only)

/** The tabs of section 6, and what each one is waiting on where it cannot be built. */
const TABS = [
  {key: "overview", name: "overview"},
  {key: "attribution", name: "attribution"},
  {key: "trades", name: "trades"},
  {key: "execution", name: "execution"},
  {key: "robustness", name: "robustness"},
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

/** One read-only line restating what determined this result.
 *
 * Chips, not switches. A realism assumption is varied by launching a robustness run, not
 * by changing it here — which is the architecture's rule that these are tested, never
 * tuned, and the reason the only toggle on this page is the display one. */
function realismStrip(){
  const m = RUN.manifest || {};
  const cfg = m.config || {};
  const ex = m.execution || {};
  const prov = m.provenance || {};
  const chips = [
    ["period", `${RUN.start_date} → ${RUN.end_date}`],
    ["capital", money(cfg.notional_capital)],
    ["engine", RUN.engine === "event_driven" ? "event driven" : "trade table"],
    ["fill latency", ex.execution_lag_days != null
      ? `${ex.execution_lag_days} session${ex.execution_lag_days === 1 ? "" : "s"}` : "—"],
    ["λ (adverse fill)", num(cfg.costs?.adverse_fraction)],
    ["shadow", ex.shadow_days != null ? `${ex.shadow_days} days` : "—"],
    ["data", prov.all_synthetic ? "synthetic chain"
      : prov.mixed ? `${pct(prov.synthetic_share)} synthetic` : "real"],
    ["commit", m.code || "—"],
    ["result hash", m.run_id || "—"],
  ];
  return `<div class="realism">${chips.map(([k, v]) =>
    `<span class="realism-chip"><b>${esc(k)}</b>${esc(v)}</span>`).join("")}</div>`;
}

/** What the research protocol wants said before any number is read.
 *
 * Each flag names the cut behind it. The anomaly scan already applies most of them in the
 * backend, so its findings are flags rather than a second opinion computed here. */
function protocolFlags(){
  const t = (META && META.thresholds) || {};
  const a = RUN.analysis || {};
  const prov = RUN.manifest?.provenance || {};
  const flags = [];

  if(RUN.boundary)
    flags.push({level: "warn", short: "synthetic chain", detail: RUN.boundary});

  const holdOut = prov.hold_out || {};
  flags.push(holdOut.hold_out
    ? {level: holdOut.touched ? "warn" : "ok",
       short: holdOut.touched ? "hold-out revealed" : "hold-out respected",
       detail: holdOut.note || ""}
    : {level: "warn", short: "no hold-out",
       detail: holdOut.note || "nothing was reserved, so no result here is out of sample"});

  const floor = t.bootstrap_min_observations?.value;
  if(floor != null && RUN.n_trades < floor)
    flags.push({level: "warn", short: `${RUN.n_trades} trades`,
      detail: `below the ${floor} this project requires before a statistic carries an `
            + `interval. ${t.bootstrap_min_observations.because || ""}`});

  for(const [key, interval] of Object.entries(a.intervals || {})){
    if(!interval) continue;
    if(interval.low <= 0 && 0 <= interval.high)
      flags.push({level: "warn", short: `${interval.name}: sign not established`,
        detail: `the 95% interval is [${num(interval.low, 0)}, ${num(interval.high, 0)}] `
              + `and contains zero. ${interval.caveat || ""}`});
  }

  const guards = RUN.manifest?.guards || {};
  if(guards.state && guards.state !== "clean")
    flags.push({level: "warn", short: `guards ${guards.state}`, detail: guards.note || ""});

  for(const found of RUN.anomalies || [])
    flags.push({level: found.severity === "high" ? "bad" : "warn",
                short: found.headline, detail: found.detail});

  for(const line of a.excursion?.censoring || [])
    // The summary carries what was censoring, not just that something was: three chips
    // reading "the excursion distribution is censored" cannot be told apart, and which
    // rule did the censoring is the whole content — it is what has to be turned off to
    // see the distribution at all.
    flags.push({level: "warn",
                short: `censored: ${String(line).split(":")[0]}`, detail: line});

  if(RUN.anomalies === null)
    flags.push({level: "warn", short: "no anomaly scan recorded",
      detail: "this run was stored before the interface kept one, which is not the same "
            + "as a scan that found nothing."});

  if(!flags.length)
    return `<p class="flags-none">Nothing the research protocol flags on this run.</p>`;
  return `<div class="flags">${flags.map((f, i) => `
    <details class="flag ${f.level}">
      <summary>${esc(f.short)}</summary>
      <p>${mark(f.detail)}</p>
    </details>`).join("")}</div>`;
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

  const cards = [
    {name: "annualised", value: eq && pct(eq.annualised),
     missing: "needs an equity curve, which only the event-driven engine builds"},
    {name: "max drawdown", value: eq && pct(eq.max_drawdown),
     note: eq && `at the bid: ${pct(eq.max_drawdown_liq)}`,
     missing: "needs an equity curve"},
    {name: "Sortino", value: inv && num(inv.Sortino),
     note: inv && inv.downside_vol != null ? `downside vol ${pct(inv.downside_vol)}` : "",
     missing: "needs an equity curve"},
    {name: "Calmar", value: inv && num(inv.Calmar), missing: "needs an equity curve"},
    {name: "net P&L", value: money(RUN.net_pnl), interval: a.intervals?.net_pnl},
    {name: "mean per trade", value: RUN.n_trades ? money(RUN.net_pnl / RUN.n_trades) : "—",
     interval: a.intervals?.mean_pnl,
     // The division is the one arithmetic left in this file and it is a presentation of
     // two stored numbers, not a statistic: the interval beside it is the backend's.
     note: `over ${RUN.n_trades} trades`},
    {name: "win rate", value: pct(RUN.win_rate)},
    {name: "top 5 trades' share", value: inv && pct(inv.top5_share),
     note: inv && inv.without_top5 != null ? `without them ${money(inv.without_top5)}` : "",
     missing: "the investor view did not report one for this run"},
  ];

  return `<dl class="cards">${cards.map(c => {
    const absentCard = c.value == null || c.value === "—" || c.value === false;
    return `<div class="card${thin ? " thin" : ""}${absentCard ? " empty" : ""}"
       ${thin ? `title="fewer than ${floor} trades, so this is shown but not to be relied on"` : ""}>
      <dt>${esc(c.name)}</dt>
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
  return `<div class="interval${crosses ? " crosses" : ""}"
       title="${esc(interval.name)} ${num(interval.estimate, 0)} — the ${pct(interval.level)} interval is [${num(interval.low, 0)}, ${num(interval.high, 0)}]${interval.caveat ? ". " + interval.caveat.replace(/\*\*/g, "") : ""}">
    <span class="interval-bar">${crosses
      ? `<i class="interval-zero" style="left:${zeroAt.toFixed(1)}%"></i>` : ""}</span>
    <span class="interval-text">${num(interval.low, 0)} … ${num(interval.high, 0)}${
      crosses ? " · crosses zero" : ""}</span>
  </div>`;
}

/* ── the process tier ───────────────────────────────────────────────────────── */

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
  const capital = RUN.manifest?.config?.notional_capital;

  // Risk events and the range truncation, read from this run's own diagnostics.
  const marks = [];
  for(const d of RUN.diagnostics || [])
    if(d.first) marks.push({date: d.first, kind: d.kind, label: `${d.kind} (${d.count})`});

  const stack = chartStack(host, {
    dates,
    holdOut: RUN.manifest?.provenance?.hold_out?.hold_out ? null : null,
    marks,
    brush: null,
    layers: [
      lineLayer({
        key: "equity", title: `equity, marked at the ${MARK === "liq" ? "bid" : "mid"}`,
        hint: "the thin line is the other mark", height: 150,
        values: curve, second: other,
        secondTitle: MARK === "liq" ? "marked at the mid" : "liquidation value, at the bid",
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
        <h2>run ${RUN.id}<span class="run-idea">${esc(RUN.strategy_name || "")} v${
          RUN.strategy_ver ?? "?"}</span></h2>
        <p class="run-cfg">${esc(typeof RUN.config?.entry === "string"
          ? RUN.config.entry : "a plan function")}</p>
        <div class="run-actions">
          <button type="button" class="chip" id="markToggle"
            title="display only: the metrics and the stop triggers were computed on the run's configured mark"
            >marked at the ${MARK === "liq" ? "bid" : "mid"}</button>
          <a class="chip" href="#/compose">back to the bench</a>
        </div>
      </header>
      ${realismStrip()}
      ${protocolFlags()}
      ${metricCards()}
    </section>

    <nav class="tabs" role="tablist">${TABS.map(t =>
      `<button type="button" class="tab${t.key === tab ? " on" : ""}" role="tab"
         data-tab="${t.key}">${esc(t.name)}</button>`).join("")}</nav>

    <section class="tier-mid" id="tierMid"></section>

    <section class="tier-detail">
      <header class="tier-head"><h3>trades</h3>
        <p class="hint" id="runDetailNote"></p></header>
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

  watchScroll();
  renderTab(tab, query);
  paintDetail();
}

/** Shrink the conclusion tier once the reader has scrolled past it.
 *
 * The design plan asks for it, and without it the page is broken rather than merely
 * unpolished: the tier is about 500 px of sticky header, so the chart stack it sits above
 * has no room left and reads as missing.
 */
function watchScroll(){
  const top = $("view-run")?.querySelector(".tier-top");
  if(!top) return;
  const apply = () => top.classList.toggle("compact", window.scrollY > 48);
  apply();
  // One listener for the page, replaced on each render rather than accumulating: a
  // listener per render would fire a dozen times per scroll by the end of a session.
  window.removeEventListener("scroll", watchScroll._on || (() => {}));
  watchScroll._on = apply;
  window.addEventListener("scroll", apply, {passive: true});
}

function renderTab(tab, query){
  const host = $("tierMid");
  const spec = TABS.find(t => t.key === tab);
  if(spec && spec.pending){
    host.innerHTML = `<div class="pending" style="padding:var(--space-5)">
      <h2>${esc(spec.name)}</h2><p class="hint"><b>Not built yet.</b> ${
        esc(spec.pending)}</p></div>`;
    return;
  }
  if(tab === "overview"){
    const box = document.createElement("div");
    host.innerHTML = "";
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
  if(tab === "attribution"){ host.innerHTML = attributionTab(); return; }
  if(tab === "trades"){ host.innerHTML = exitReasonTab(); return; }
  if(tab === "execution"){ host.innerHTML = costsTab(); return; }
  if(tab === "robustness"){ host.innerHTML = robustnessTab(); return; }
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

function robustnessTab(){
  const scenarios = RUN.scenarios || RUN.analysis?.scenarios;
  const offered = new Set(RUN.validations || []);
  return `<div class="pad">
    <p class="hint">The standard set from the architecture's research register. Each item
      is launched from the bench today; this tab reports what this run can answer.</p>
    <ul class="checks">${(META?.validations || []).map(v => `
      <li class="${offered.has(v.kind) ? "can" : "cannot"}">
        <b>${esc(v.label)}</b><span>${esc(v.why)}</span>
        ${offered.has(v.kind) ? "" : `<em>needs the ${
          (v.engines || []).join(" or ")} engine; this run used ${esc(RUN.engine)}</em>`}
      </li>`).join("")}</ul>
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
