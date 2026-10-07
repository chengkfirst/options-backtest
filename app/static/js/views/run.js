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
import {API, getJSON, notice, postJSON} from "../net.js";
import {checkContract} from "../contract.js";
import {go, step} from "../router.js";
import {EXCLUDE, REASON, absent, bare, dateLong, fmtVal, label, money, moneyShort,
        num, pct, size}
  from "../fmt.js";
import {areaLayer, chartStack, lineLayer, underwaterLayer} from "../charts/axis.js";
import {renderAB, renderHist, renderPeriods, renderScatter} from "../charts/distribution.js";
import {renderCurve} from "../charts/equity.js";
import {renderAttribution} from "../charts/attribution.js";
import {renderBuckets, renderCross, renderRobust} from "../charts/tables.js";
import {replace} from "../router.js";
import {closeDrawer, isOpen as cardOpen, openDrawer, toggleFull} from "../ui/drawer.js";
import {bind} from "../ui/keys.js";
import {provide} from "../ui/palette.js";
import {actions as cardActions, card as cardBody, contractParts} from "./trade.js";

let RUN = null;        // the record being viewed
let META = null;       // /api/meta, for the thresholds
let STACK = null;      // the chart group, so it can be told the brush
let VIEW = [];         // the trades inside the brush — what the detail tier shows
let MARK = "mid";      // which mark the curve is drawn on (display only)
let TAB = "overview";  // which tab is open, so a brush can repaint it
let PICKED = null;     // the selected trade's row, or null
let SCROLL_TO_FIRST = false;  // set by the brush: take the table to the range's first trade
let RANGE = null;      // the brushed range, so the chip can show and clear it
let CLOSED_BY = null;  // filter to the trades one slot closed, from the exits tab
let SCALE = "linear";  // the equity layer's vertical scale (display only)
let UNBIND = null;     // takes this page's keyboard bindings off again on the way out
let UNPROVIDE = null;  // and its entries out of the command palette

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
          + "engine computes them; they are not kept with the run."},
];

/* A finished run never changes, so its record is kept by id for the life of the session —
 * the addendum's caching rule, and the reason a tab switch or a step back is instant
 * rather than another eighty-field fetch. Only what the page can change is invalidated:
 * `archive` writes into the cached record, and `delete` leaves the run entirely. */
const CACHE = new Map();

export function forget(id){ CACHE.delete(Number(id)); }

/* A skeleton shaped like the page that is coming, and **only after 300 ms** — the design
 * plan's state table, which also forbids a spinner and a blank page. The delay is the point:
 * a cached run paints in under a frame, and flashing a grey page in front of it is slower to
 * read than the page itself. The cells match the real band, so nothing moves when the
 * numbers arrive. */
const SKELETON = `<section class="tier-top">
  <div class="skeleton sk-head"></div>
  <div class="skeleton sk-verdict"></div>
  <dl class="cards">${Array.from({length: 8}, (_, i) =>
    `<div class="card${i < 4 ? " lead" : ""}"><dt><span class="skeleton sk-dt"></span></dt>
      <dd><span class="skeleton sk-dd"></span></dd></div>`).join("")}</dl>
</section>
<div class="skeleton sk-chart"></div>`;

/* ── a tab switch is not a new page ─────────────────────────────────────────────
 *
 * A tab is a **step** in the history, so it arrives as a hash change and re-enters the route
 * — and the route's job is to render the page. Rendering the page meant rebuilding the
 * verdict, eight metric cards, the standing-assumptions sheet, the chart stack and four
 * hundred table rows in order to change which chart sits between them, which measured at
 * about a second against the plan's budget of a hundred milliseconds for a tab switch with
 * the data already in hand.
 *
 * Nothing above the tabs depends on which tab is open, so only the tab's own body is redrawn.
 */

/** Whether this entry differs from what is on screen **only** in which tab is open.
 *
 * Compared against the view's own live state rather than a snapshot of the last query,
 * because the brush, the mark and the selected trade reach the URL through `replace`, which
 * deliberately does not re-enter the route — a snapshot would be stale the moment anyone
 * dragged across a chart.
 *
 * Deliberately strict: any other difference, or a page that is not already on screen, takes
 * the full path. Back and forward can change several things at once, and a light path that
 * guessed wrong would leave the page describing a reading it is not showing.
 */
function onlyTheTabChanged(id, query){
  if(!RUN || Number(RUN.id) !== id || !$("tierMid")) return false;
  const same = [
    [query.mark === "liq" ? "liq" : "mid", MARK],
    [query.scale === "log" ? "log" : "linear", SCALE],
    [query.range || "", RANGE ? `${RANGE.from}..${RANGE.to}` : ""],
    [query.closed || "", CLOSED_BY || ""],
    [query.trade || "", PICKED ? String(PICKED.trade_id ?? "") : ""],
    [query.card ? "open" : "", cardOpen() ? "open" : ""],
  ];
  if(same.some(([asked, showing]) => asked !== showing)) return false;
  return (TABS.some(t => t.key === query.tab) ? query.tab : "overview") !== TAB;
}

export async function enter({params, query}){
  const id = Number(params.id);
  const host = $("view-run");
  if(onlyTheTabChanged(id, query)){
    const tab = TABS.some(t => t.key === query.tab) ? query.tab : "overview";
    for(const button of $("view-run").querySelectorAll("[data-tab]"))
      button.classList.toggle("on", button.dataset.tab === tab);
    renderTab(tab, query);
    return;
  }
  const cached = CACHE.get(id);
  let late = 0;
  if(!cached)
    late = setTimeout(() => { host.innerHTML = SKELETON; }, 300);
  try{
    if(!META) META = await getJSON(API.meta);
    RUN = cached || await getJSON(API.run, {id});
    CACHE.set(id, RUN);
  }catch(e){
    clearTimeout(late);
    host.innerHTML = `<div class="pending"><h2>run ${esc(params.id)}</h2>
      <p class="hint">${esc(e.message)}</p>
      <button class="btn" type="button" data-goto="/log">go to the experiment log</button></div>`;
    return;
  }
  clearTimeout(late);
  checkContract(RUN);
  MARK = query.mark === "liq" ? "liq" : "mid";
  SCALE = query.scale === "log" ? "log" : "linear";
  VIEW = RUN.rows || [];
  bindKeys();
  render(query);
}

/* ── the keyboard ────────────────────────────────────────────────────────────────
 *
 * The design plan's appendix, for the keys this page owns. They are registered on the way in
 * and taken off on the way out, so a key never acts on a run that is no longer on screen —
 * and so the command palette, which lists the live registry, offers exactly what the page
 * the reader is on can do.
 */
function bindKeys(){
  if(UNBIND) UNBIND();
  UNBIND = bind("run", [
    {keys: ["Escape"], does: "close the trade card, or clear the brushed range",
     run: () => {
       if(cardOpen()) closeDrawer();
       else if(RANGE || CLOSED_BY){
         if(STACK) STACK.setBrush(null);
         CLOSED_BY = null;
         applyBrush(null);
         replace({range: null, closed: null});
       }
     }},
    ...TABS.map((t, i) => ({
      keys: [String(i + 1)], does: `open the ${t.name} tab`,
      run: () => step({tab: t.key}),
    })),
    {keys: ["j"], does: "the next trade in the table's order",
     run: () => walk(1), where: () => VIEW.length > 0},
    {keys: ["k"], does: "the previous trade in the table's order",
     run: () => walk(-1), where: () => VIEW.length > 0},
    {keys: ["f"], does: "expand the trade card to the full window",
     run: toggleFull, where: cardOpen},
    {keys: ["l"], does: "draw the equity on a logarithmic scale, or back",
     run: () => setScale(SCALE === "log" ? "linear" : "log")},
    {keys: ["m"], does: "mark the book at the bid, or at the mid",
     run: () => setMark(MARK === "mid" ? "liq" : "mid")},
    {keys: ["mod+shift+c"], does: "copy a link to this exact reading", run: copyLink},
  ]);
  if(UNPROVIDE) UNPROVIDE();
  // The trades of the open run, by id and by contract, so a trade someone mentioned in a
  // message can be reached by typing its number.
  UNPROVIDE = provide("trades", query => {
    const q = String(query || "").toLowerCase();
    if(!q) return [];
    return (RUN.rows || [])
      .filter(r => String(r.trade_id).includes(q)
        || String(r.contract || "").toLowerCase().includes(q))
      .slice(0, 8)
      .map(r => ({
        kind: "trade", label: `${r.trade_id} · ${contractParts(r.contract).readable}`,
        hint: money(r.net_pnl), run: () => openCard(r),
      }));
  });
}

/** Walk to the next or previous trade, opening the card on it — the plan's J and K. */
function walk(by){
  const order = sorted(VIEW).slice(0, DRAWN);
  if(!order.length) return;
  const at = PICKED ? order.indexOf(PICKED) : -1;
  const next = order[Math.max(0, Math.min(order.length - 1, at + by))] || order[0];
  openCard(next);
}

/** Which mark the book is drawn at. Display only, like the scale beside it.
 *
 * It redrew the whole page, which measured at about 300 ms with four hundred rows in the
 * table — and the metric cards, the verdict and the sheet do not depend on the mark at all:
 * they were computed on the run's configured one and say so. Only the chip and the chart
 * stack change, so only those are redrawn. */
function setMark(mark){
  MARK = mark;
  replace({mark: MARK});
  const chip = $("markToggle");
  if(chip) chip.textContent = `showing the ${MARK === "liq" ? "bid" : "mid"} mark`;
  if(TAB === "overview") renderTab("overview", {});
}

/** The vertical scale of the equity layer. Display only, and labelled as such: the metrics
 *  were computed on the values, not on their logarithms. */
function setScale(scale){
  SCALE = scale;
  replace({scale: SCALE === "log" ? "log" : null});
  if(TAB === "overview") renderTab("overview", {});
  const chip = $("scaleToggle");
  if(chip) chip.textContent = SCALE === "log" ? "log scale" : "linear scale";
}

/** The address bar already describes the reading; this puts it on the clipboard.
 *
 * `navigator.clipboard` needs a secure context, and `http://localhost` is one — but a page
 * opened from a file or over a plain host on the network is not, and there the write
 * rejects. The link is shown in that case rather than silently not copied. */
async function copyLink(){
  const url = window.location.href;
  try{
    await navigator.clipboard.writeText(url);
    notice(`the link to this reading is on the clipboard: ${url}`);
  }catch{
    notice(`this browser would not give the page the clipboard. The link is: ${url}`);
  }
}

/* ── the conclusion tier ────────────────────────────────────────────────────── */

/** The answer, before the evidence for it.
 *
 * A reader opening a finished run is asking one question — did this work — and the only
 * honest form of it compares against doing nothing. That sentence was card number five
 * on a page where every block had the same weight, which is why the page could be read
 * end to end without learning what it said.
 *
 * It is a sentence and not a score. "Good" and "bad" are the reader's to decide on
 * evidence; what this does is put the evidence that decides it first, and name what would
 * most change the answer.
 */
function verdict(){
  const made = RUN.net_pnl;
  const verb = made >= 0 ? "made" : "lost";

  // The comparison that answers the question. Buy-and-hold was the old headline and it
  // flatters or punishes an option strategy for the wrong reason: it judges a position
  // held for days against the underlying's whole-period drift. `delta_equivalent` holds
  // the same first-order exposure in shares over the same dates, so the excess is what
  // the option itself added — convexity and a capped loss, net of decay and the spread.
  const base = (RUN.analysis?.baselines || [])
    .find(b => b.name && b.name.includes("delta-equivalent"));
  const inv = RUN.analysis?.investor || {};

  let line, tone;
  if(base && !absent(base.excess)){
    const under = esc(RUN.config?.symbol || "the underlying");
    tone = base.excess > 0 ? "good" : "bad";
    line = `${verb} ${size(made)}; the same first-order exposure in ${under} shares would
      have ${base.baseline >= 0 ? "made" : "lost"} ${size(base.baseline)}.
      <b>Choosing options over shares ${base.excess > 0 ? "gained" : "gave up"}
      ${size(base.excess)}.</b>`;
  } else if(!absent(inv.buy_and_hold) && !absent(inv.strategy_total)){
    tone = inv.strategy_total > inv.buy_and_hold ? "good" : "bad";
    line = `${verb} ${size(made)} — ${pct(inv.strategy_total)} where holding
      ${esc(RUN.config?.symbol || "the underlying")} returned ${pct(inv.buy_and_hold)}.`;
  } else {
    tone = made > 0 ? "good" : "bad";
    line = `${verb} ${size(made)} over ${RUN.n_trades} trades.`;
  }

  // One caveat, the one most likely to change the reading. The rest are in the tabs.
  const thin = RUN.n_trades < (META?.thresholds?.bootstrap_min_observations?.value ?? 0);
  const worst = (RUN.anomalies || []).find(f => f.severity === "high");
  const caveat = thin ? `${RUN.n_trades} trades — too few to carry an interval.`
    : base && base.valid_on_synthetic === false && RUN.boundary
      ? "Synthetic chain: the shares are priced from real closes and the options are not, "
        + "so read the direction of this comparison, not its size."
    : worst ? worst.headline
    : RUN.boundary ? "Synthetic option chain: the option-side numbers are Black-Scholes."
    : "";

  return `<div class="verdict ${tone}">
    <p class="verdict-line">${line}</p>
    ${spread()}
    ${caveat ? `<p class="verdict-caveat">${esc(caveat)}</p>` : ""}
  </div>`;
}

/** How much of the headline figure is the draw rather than the strategy.
 *
 * This lived under two of the eight metric cards as "95% −54.3k to −8.0k", which asks the
 * reader to already know it is a bootstrap confidence interval, what the 95% is a
 * percentage of, and that the thing to look for is whether it contains zero. Four tokens
 * carrying that much assumed context are not short, they are unreadable.
 *
 * Saying it properly needs a sentence, and a 165px card cannot hold one — spelling it out
 * there took three wrapped lines of 11px monospace and grew the whole band by 40%. But it
 * was never a property of a card: it qualifies **the headline number**, so it belongs
 * beside it, where the full width makes it one plain line.
 *
 * What it means: these trades are one draw. The engine resamples them many times over and
 * recomputes the total each time, and this is where 95% of those totals fell. A reader who
 * has never heard of a bootstrap can still read the sentence and know what they are being
 * told — and an interval spanning zero says the sign of the headline is not established,
 * which is the whole reason to show it. */
function spread(){
  const iv = RUN.analysis?.intervals?.net_pnl;
  if(!iv || absent(iv.low) || absent(iv.high)) return "";
  const crosses = iv.low <= 0 && 0 <= iv.high;
  const level = Math.round(iv.level * 100);
  return `<p class="verdict-spread${crosses ? " crosses" : ""}">These ${RUN.n_trades}
    trades are one draw: resampling them puts the total between <b>${moneyShort(iv.low)}</b>
    and <b>${moneyShort(iv.high)}</b> ${level} times in 100 — the <b>bootstrap interval</b>${
      crosses ? ", and it <b>spans zero</b>, so this run has not established even the sign of"
        + " its result" : ""}${iv.caveat
          ? ` — though the resampling treats the trades as independent, and ${mark(iv.caveat)
              .replace(/^trades resampled as if independent;\s*/, "")}` : ""}.</p>`;
}

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

/* ── what every run stands on ─────────────────────────────────────────────────────
 *
 * Checking is the backend's work — it runs the anomaly scan, writes `diagnostics` as the
 * engine steps, computes the intervals, states the data boundary. Nothing here checks
 * anything. This is the sheet that says **what the machinery assumed and where that limits
 * the answer**, filled in with this run's values.
 *
 * It is the same sheet every time. That is the point: a list that varies in length reads
 * as "here is what we happened to find", and the reader cannot tell a quiet run from an
 * unexamined one. A fixed sheet reads as "here is what this platform always assumes", and
 * a row that is fine this time still tells you the axis exists.
 *
 * Organised by **subject**, because that is how the question arises — what does the data
 * not contain, what does the pricing model assume, what does the fill model leave out —
 * and because an assumption and the limitation it causes are the same fact seen twice.
 * They sit in one row: what was assumed, what this run used, and what it costs you.
 *
 * Every value is read from the manifest rather than written here, so a row stays true when
 * the backend changes rather than becoming a confident claim about code that has moved.
 * `tools/check_evidence.py` holds that: a row whose value does not come from the record is
 * a row that can go stale silently.
 */

/* The anomaly scan reports both kinds, so its findings are sorted by what they are
   **about**: the reliability of the evidence, or the behaviour of this strategy. */
const ABOUT_THE_EVIDENCE = new Set([
  "overlap",        // trades that overlap are not independent observations
  "too_good",       // a figure too good to be a measurement of anything
  "rule_failed",    // a rule the run could not hold
]);

/** How many of a diagnostic kind are worth saying. Below this the engine noting something
 *  a handful of times is routine; above it, it is a statement about how much of the run
 *  rested on a model the engine itself was unsure of. */
const DIAGNOSTIC_FLOOR = 20;

/** One diagnostic kind, counted, or null. */
function diagnostic(r, kind){
  const d = (r.diagnostics || []).find(x => x && x.kind === kind);
  return d && (d.count || 0) >= DIAGNOSTIC_FLOOR ? d.count : null;
}

/** The anomaly of a kind, or null. */
function anomaly(r, kind){
  return (r.anomalies || []).find(f => f && f.kind === kind) || null;
}

const bad = s => ({level: "bad", says: s});
const warn = s => ({level: "warn", says: s});
const ok = s => ({level: "ok", says: s});

/** The sheet. Each row: what the axis is, what this run did on it, and what that limits.
 *
 * `limits` returning `ok(...)` means the axis is in a good state and says so, rather than
 * leaving a blank that reads as "not examined". */
const SHEET = [
  {
    area: "data",
    title: "Data",
    rows: [
      {what: "option prices",
       value: r => {
         const p = r.manifest?.provenance || {};
         return p.all_synthetic ? "Black-Scholes, no skew"
           : p.mixed ? `${pct(p.synthetic_share)} modelled` : "real quotes";
       },
       limits: r => {
         const p = r.manifest?.provenance || {};
         if(p.all_synthetic) return bad("no option-side number describes a traded market "
           + "— the formula has no skew, so strike choice is being judged against an "
           + "assumption rather than against prices");
         if(p.mixed) return warn(`${pct(p.synthetic_share)} of the option prices are the `
           + `formula rather than quotes`);
         return ok("from quotes");
       }},

      {what: "bid/ask spread",
       value: r => {
         const syn = r.manifest?.config?.synthetic;
         return r.manifest?.provenance?.all_synthetic && syn
           ? `${pct(syn.spread_pct)} of price, floor ${syn.spread_floor}` : "from quotes";
       },
       limits: r => r.manifest?.provenance?.all_synthetic
         ? warn("the spread is a formula, so a max-spread filter is filtering the formula "
              + "and the cost of crossing it is not a real cost")
         : ok("the spread paid is the spread quoted")},

      {what: "underlying prices",
       // The data version names what produced this chain, so the row says which snapshot
       // rather than asserting a quality the record is not consulted about.
       value: r => {
         const v = String(r.manifest?.data_version || "—");
         return v.startsWith("synthetic:") ? "real closes; chain generated" : v;
       },
       limits: r => r.manifest?.provenance?.availability_assumed
         ? warn(r.manifest.provenance.availability_note
                || "availability dates were assumed, so rows may have been used before "
                 + "they were knowable")
         : ok("availability came from the data; rows not yet known were withheld")},

      {what: "period",
       // The span the run covers, which went missing when the sheet was reorganised by
       // subject. Prose takes a month name; a table and a URL take ISO — two ISO dates
       // inside a spec row read as serial numbers.
       value: r => `${dateLong(r.start_date)} → ${dateLong(r.end_date)}`,
       limits: r => {
         const days = (Date.parse(r.end_date) - Date.parse(r.start_date)) / 86400000;
         const years = days / 365.25;
         return years < 1
           ? warn(`${Math.round(days)} days, which covers one kind of market and not a `
                + `cycle — nothing here says how the rule behaves in the others`)
           : ok(`${years.toFixed(1)} years`);
       }},

      {what: "breadth",
       // The engine runs one symbol. There is no configuration in which this axis is
       // fine, so it is marked permanent rather than written as a row that merely never
       // reports ok.
       always: "the engine runs one underlying at a time",
       value: r => `${r.config?.symbol || r.symbol || "—"} only`,
       limits: () => warn("one underlying, so nothing here says whether the rule "
                        + "generalises or whether this is one instrument's decade")},
    ],
  },
  {
    area: "model",
    title: "Pricing model",
    rows: [
      {what: "exercise style",
       value: r => r.manifest?.config?.contract?.exercise || "—",
       limits: r => {
         const n = diagnostic(r, "early_exercise_risk");
         return n
           ? warn(`the binomial tree disagreed with the early-exercise flag ${
               n.toLocaleString()} times; those positions were valued on the less certain `
               + `side`)
           : ok("early exercise was valued without disagreement");
       }},

      {what: "risk-free rate",
       // The config carries one rate, not a curve; no setting makes this axis fine.
       always: "the config carries a single rate, not a term structure",
       value: r => {
         const rate = r.manifest?.config?.fallback_risk_free_rate;
         return absent(rate) ? "—" : `${pct(rate)} flat`;
       },
       limits: () => warn("a single constant, not a curve, so anything the term structure "
                        + "would have changed is absorbed into the result")},

      {what: "greeks",
       // How much of the result the expansion actually placed, rather than a label for the
       // method. `share.residual` is the part it could not attribute to any Greek.
       value: r => {
         const share = (r.attribution || r.analysis?.attribution)?.share;
         return absent(share?.residual) ? "—"
           : `${pct(Math.abs(share.residual))} unplaced`;
       },
       limits: r => {
         const attr = r.attribution || r.analysis?.attribution;
         const n = attr && attr.high_residual;
         const cut = META?.thresholds?.attribution_residual_warning?.value;
         return n
           ? warn(`${n} trade${n === 1 ? "" : "s"} left a residual past ${
               cut != null ? pct(cut) : "the cut"}, so that much of the attribution is `
               + `unplaced`)
           : ok("the expansion placed every trade inside its tolerance");
       }},
    ],
  },
  {
    area: "execution",
    title: "Execution",
    rows: [
      {what: "fill delay",
       value: r => {
         const lag = r.manifest?.execution?.execution_lag_days;
         return lag == null ? "—" : lag === 0 ? "same session"
           : `${lag} session${lag === 1 ? "" : "s"}`;
       },
       limits: r => r.manifest?.execution?.execution_lag_days === 0
         ? bad("filled on the close that produced the signal, which cannot be traded")
         : ok("nothing is filled on the bar that decided it")},

      {what: "fill price",
       value: r => {
         const lam = r.manifest?.config?.costs?.adverse_fraction;
         return absent(lam) ? "—" : lam >= 1 ? "whole spread (λ 1.00)"
           : `${pct(lam)} across (λ ${num(lam)})`;
       },
       limits: r => {
         const lam = r.manifest?.config?.costs?.adverse_fraction;
         return absent(lam) ? warn("not recorded")
           : lam >= 1 ? ok("the pessimistic end: the whole spread is paid")
           : warn("less than the whole spread, which assumes an edge in getting filled");
       }},

      {what: "market impact",
       value: r => {
         const slip = r.manifest?.config?.costs?.slippage_per_contract;
         return absent(slip) ? "—" : slip === 0 ? "none charged" : `${slip} per contract`;
       },
       limits: r => r.manifest?.config?.costs?.slippage_per_contract === 0
         ? warn("size is assumed not to move the price, which holds while the position is "
              + "small against the contract's volume and not otherwise")
         : ok("a per-contract charge was applied")},

      {what: "tradability cut",
       value: r => {
         const w = r.manifest?.config?.costs?.max_relative_spread;
         return absent(w) ? "none" : `spread ≤ ${pct(w)}`;
       },
       limits: r => absent(r.manifest?.config?.costs?.max_relative_spread)
         ? warn("no width was refused, so a contract nobody could have traded could still "
              + "have been bought")
         : ok("contracts wider than the cut were refused rather than filled")},
    ],
  },
  {
    area: "book",
    title: "Portfolio",
    rows: [
      {what: "capital",
       value: r => size(r.manifest?.config?.notional_capital),
       limits: r => r.manifest?.execution?.enforce_capital
         ? ok("positions were refused when the cash was not there")
         : warn("capital was not enforced, so the book could hold more than it could pay "
              + "for")},

      {what: "concurrent positions",
       value: r => {
         const n = r.manifest?.execution?.max_concurrent;
         return n == null ? "no cap" : String(n);
       },
       limits: r => r.manifest?.execution?.max_concurrent == null
         ? warn("every signal that had cash could open, so the book's concentration is "
              + "whatever the signal produced rather than something that was chosen")
         : ok("the book was held to the cap")},

      {what: "book-level risk rule",
       value: r => r.manifest?.execution?.risk_rule || "none",
       limits: r => r.manifest?.execution?.risk_rule
         ? ok("a book-wide rule was in force")
         : warn("nothing reduced or halted the book on a drawdown, so the equity curve is "
              + "what the strategy does unmanaged")},
    ],
  },
  {
    area: "evidence",
    title: "Evidence",
    rows: [
      {what: "trades",
       value: r => String(r.n_trades ?? "—"),
       limits: r => {
         const floor = META?.thresholds?.bootstrap_min_observations?.value;
         return floor != null && r.n_trades < floor
           ? bad(`below the ${floor} this project requires before a statistic carries an `
               + `interval`)
           : ok("enough observations to carry an interval");
       }},

      {what: "independence",
       value: r => {
         const o = anomaly(r, "overlap");
         return o ? o.headline.replace(/^only /, "") : "not measured";
       },
       limits: r => {
         const o = anomaly(r, "overlap");
         return o ? warn("positions held at the same time move together, so an interval "
                       + "computed on the nominal count is too narrow")
           : ok("no overlap was reported");
       }},

      {what: "censoring",
       value: r => {
         const c = r.analysis?.excursion?.censoring || [];
         return c.length ? `${c.length} cut${c.length === 1 ? "" : "s"} in force` : "none";
       },
       limits: r => {
         const c = r.analysis?.excursion?.censoring || [];
         return c.length
           ? warn(c.map(l => String(l).split(":")[0]).join("; ")
                + " — paths past each were never observed")
           : ok("no exit rule truncated what could be seen");
       }},

      {what: "out of sample",
       value: r => {
         const h = r.manifest?.provenance?.hold_out || {};
         return h.hold_out ? (h.touched ? `${h.hold_out}, revealed` : h.hold_out) : "none";
       },
       limits: r => {
         const h = r.manifest?.provenance?.hold_out || {};
         if(!h.hold_out) return bad("no period was reserved, so every number here was "
                                  + "available while the strategy was being chosen");
         return h.touched ? warn(h.note || "the reserved period has been revealed")
           : ok("a period is reserved and has not been looked at");
       }},
    ],
  },
  {
    area: "provenance",
    title: "Reproducibility",
    rows: [
      {what: "code",
       value: r => String(r.manifest?.code || "—"),
       limits: r => String(r.manifest?.code || "").includes("+dirty")
         ? bad("the named commit does not contain the code that produced these numbers, "
             + "and the difference is recorded nowhere — this run cannot be obtained again")
         : ok("the commit names the code that ran")},

      {what: "guards",
       value: r => r.manifest?.guards?.state || "—",
       limits: r => {
         const g = r.manifest?.guards || {};
         return g.state && g.state !== "clean"
           ? warn(g.note || `the guards were ${g.state} for this code`)
           : ok("the guards passed on this code");
       }},

      {what: "result id",
       value: r => r.manifest?.run_id || "—",
       limits: r => r.manifest?.run_id
         ? ok("two runs sharing this fingerprint are the same run")
         : warn("no fingerprint was recorded, so this result cannot be matched to another")},

      {what: "libraries",
       value: r => {
         const e = r.manifest?.environment || {};
         return e.python ? `python ${e.python}, pandas ${e.pandas || "—"}` : "—";
       },
       limits: r => r.manifest?.environment?.python
         ? ok("the versions that produced this are recorded")
         : warn("no versions were recorded, so a different environment cannot be told "
              + "apart from a different result")},
    ],
  },
];

/** The sheet, rendered: one table per subject, same shape every run.
 *
 * Three columns — the axis, what this run did on it, and what that limits — because an
 * assumption and its consequence are the same fact and reading them apart was the problem
 * with the two separate blocks this replaces. A row in a good state says so rather than
 * going blank: a blank cannot be told from an axis nobody looked at. */
/* ── the sheet, read the way it is used ──────────────────────────────────────────
 *
 * Twenty-two rows laid out flat answered the question nobody asks first. A reader arriving
 * at a result wants, in this order: can I trust this at all, what is the worst thing wrong,
 * what specifically about the data — and only rarely, what was checked. A fourteen-row
 * table answers the last one by default and makes the first two something you work out by
 * reading, which is why it was 737px of wall between the answer and the charts.
 *
 * So: one line per subject. Its state, and **the worst thing in it, named** — because a
 * count tells you there is a problem and a sentence tells you which. Opening a subject
 * shows its rows. Six lines closed, and the thing you came for is in them.
 */
const OPEN_SUBJECTS = new Set();

/* Whether the whole block is open. `null` means nobody has said, and then it opens only
   when there is something serious in it — the block exists to put a bad finding in front
   of the reader, and a run with none does not need six lines of it sitting open on every
   visit. Once it is toggled by hand that choice sticks, including across tab switches,
   which is the design plan's "tab switches keep collapsed state". */
let SHEET_OPEN = null;

const SEVERITY = {bad: 0, warn: 1, ok: 2};

function evidence(){
  const leads = standsOutItems();
  const subjects = SHEET.map(sec => {
    const rows = sec.rows.map(row => ({row, lim: row.limits(RUN)}))
      .sort((a, b) => SEVERITY[a.lim.level] - SEVERITY[b.lim.level]);
    const counts = {bad: 0, warn: 0, ok: 0};
    for(const r of rows) counts[r.lim.level]++;
    return {sec, rows, counts, worst: rows[0]};
  });
  const all = subjects.flatMap(s => s.rows);
  const tally = {bad: 0, warn: 0, ok: 0};
  for(const r of all) tally[r.lim.level]++;

  const line = ({sec, rows, counts, worst}) => {
    const open = OPEN_SUBJECTS.has(sec.area);
    const state = counts.bad ? "bad" : counts.warn ? "warn" : "ok";
    // The dots are the subject's rows, worst first — a shape the eye reads before any
    // word, and it says how many as well as how bad.
    const dots = rows.map(r =>
      `<i class="dot ${r.lim.level}"></i>`).join("");
    return `<div class="subj ${state}${open ? " open" : ""}">
      <button type="button" class="subj-head" data-subject="${esc(sec.area)}"
              aria-expanded="${open}">
        <span class="subj-caret" aria-hidden="true"></span>
        <span class="subj-name">${esc(sec.title)}</span>
        <span class="dots" aria-label="${counts.bad} serious, ${counts.warn} noted">${dots}</span>
        <span class="subj-worst">${
          worst.lim.level === "ok" ? "nothing to note" : mark(worst.lim.says)}</span>
      </button>
      ${open ? `<table class="sheet">
        <colgroup><col class="col-what"><col class="col-is"><col class="col-so"></colgroup>
        <tbody>${rows.map(({row, lim}) => `<tr class="${lim.level}">
          <th>${esc(row.what)}${row.always ? `<span class="always">always</span>` : ""}</th>
          <td class="sheet-is">${esc(String(row.value(RUN)))}</td>
          <td class="sheet-so">${mark(lim.says)}</td></tr>`).join("")}</tbody>
      </table>` : ""}
    </div>`;
  };

  // Leads are not faults and are not part of the fixed sheet: they are what this
  // particular run did that is worth a second look, so an empty list shows nothing at all.
  const stands = !leads.length ? "" : `
    <div class="subj leads open">
      <div class="subj-head static">
        <span class="subj-name">what stands out</span>
        <span class="subj-worst">found by rule — leads, not faults</span>
      </div>
      <table class="sheet">
        <colgroup><col class="col-what"><col class="col-is"><col class="col-so"></colgroup>
        <tbody>${leads.map(f => `<tr class="${f.level}">
          <th colspan="2">${esc(f.short)}</th>
          <td class="sheet-so">${mark(f.detail || "")}</td></tr>`).join("")}</tbody>
      </table>
    </div>`;

  const open = SHEET_OPEN === null ? tally.bad > 0 : SHEET_OPEN;
  return `<section class="evidence${open ? " open" : ""}">
    <header class="ev-head">
      <button type="button" class="ev-toggle" data-sheet="toggle" aria-expanded="${open}">
        <span class="subj-caret" aria-hidden="true"></span>
        <h3>What this run stands on</h3>
      </button>
      <span class="ev-why">${tally.bad} serious · ${tally.warn} noted · ${tally.ok} clear</span>
      ${open ? `<button type="button" class="ev-all" data-subject="*">${
        OPEN_SUBJECTS.size >= SHEET.length ? "close all" : "open all"}</button>` : ""}
    </header>
    ${open ? `<div class="subjects">${subjects.map(line).join("")}${stands}</div>` : ""}
  </section>`;
}

/** Opening and closing subjects. Bound after each paint, because the tier is rewritten
 *  whole and a listener on a replaced node goes with it. */
function bindEvidence(){
  const repaint = () => {
    const host = document.querySelector(".evidence");
    host.outerHTML = evidence();
    bindEvidence();
  };
  for(const b of document.querySelectorAll("[data-sheet]"))
    b.addEventListener("click", () => {
      const tally = SHEET.flatMap(sec => sec.rows.map(r => r.limits(RUN).level))
        .filter(l => l === "bad").length;
      SHEET_OPEN = !(SHEET_OPEN === null ? tally > 0 : SHEET_OPEN);
      repaint();
    });
  for(const b of document.querySelectorAll("[data-subject]"))
    b.addEventListener("click", () => {
      const key = b.dataset.subject;
      if(key === "*"){
        if(OPEN_SUBJECTS.size >= SHEET.length) OPEN_SUBJECTS.clear();
        else for(const sec of SHEET) OPEN_SUBJECTS.add(sec.area);
      }else if(OPEN_SUBJECTS.has(key)) OPEN_SUBJECTS.delete(key);
      else OPEN_SUBJECTS.add(key);
      // Only this tier is redrawn; the charts below keep their brush and their scroll.
      const host = document.querySelector(".evidence");
      host.outerHTML = evidence();
      bindEvidence();
    });
}

/** What this run actually did that is worth a second look.
 *
 * Not caveats: leads. The scan finds them by rule rather than by asking a model to look,
 * and an empty list shows nothing at all — an insight forced out of nothing is noise, and
 * noise is what makes real warnings get skipped. */
function standsOutItems(){
  return (RUN.anomalies || [])
    .filter(f => !ABOUT_THE_EVIDENCE.has(f.kind))
    .map(f => ({level: f.severity === "high" ? "bad" : "warn",
                short: f.headline, detail: f.detail}));
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
  const ts = a.trade_stats || null;
  const t = (META && META.thresholds) || {};
  const floor = t.bootstrap_min_observations?.value ?? 0;
  const thin = RUN.n_trades < floor;

  // Every card carries a definition and, where it has one, the direction that is better.
  // A reader who meets "Calmar -0.44" with no explanation has been given a number and no
  // way to use it, which is worse than giving nothing.
  // Eight, which is the maximum the design plan allows, and the reason the count is a limit
  // rather than a preference: a band of nine wrapped onto an orphaned second row where the
  // eye had already stopped. The ninth was the profit factor, and it is not lost — the win
  // rate's note carries it, beside the average win and loss it is the ratio of, which is
  // where a reader comparing "how often" against "how much" wants it.
  const iv = a.intervals || {};
  const cards = [
    {lead: true, name: "annualised", value: eq && pct(eq.annualised), better: "higher",
     means: "the compound yearly rate this equity curve grew at over the period",
     missing: "needs the event-driven engine"},
    {lead: true, name: "max drawdown", value: eq && pct(eq.max_drawdown), better: "closer to zero",
     means: "the deepest fall from a peak of the account at any point in the run",
     // The bid-marked figure is worth a line only when it **differs** at the precision it is
     // printed at. It read "−90.2%  at the bid: −90.2%", which spends a line to say the two
     // marks agree and reads as a number that failed to update.
     note: eq && pct(eq.max_drawdown_liq) !== pct(eq.max_drawdown)
       ? `at the bid: ${pct(eq.max_drawdown_liq)}` : "",
     missing: "needs the event-driven engine"},
    {lead: true, name: "Sortino", value: inv && num(inv.Sortino), better: "higher",
     means: "return per unit of **downside** volatility; upside swings are not penalised",
     note: inv && inv.downside_vol != null ? `downside vol ${pct(inv.downside_vol)}` : "",
     missing: "needs the event-driven engine"},
    // The comparison the page was missing, and the first card a reader should be able to
    // find: a return shown alone cannot be judged.
    //
    // It used to subtract the two totals here — `strategy_total - buy_and_hold` — which is
    // not the comparison it looked like. A strategy that is twice the underlying's exposure
    // *should* beat it by that much, and the difference of two totals credits the leverage
    // to the strategy. `annualised_alpha` is the backend's own figure, the return left after
    // beta times the benchmark, and it has been in `investor_view` all along. One less
    // number this file works out, and a more honest one.
    {lead: true, name: "alpha, annualised", better: "higher",
     value: inv && inv.annualised_alpha != null ? pct(inv.annualised_alpha) : null,
     note: inv && inv.buy_and_hold != null
       ? `${pct(inv.strategy_total)} total against ${pct(inv.buy_and_hold)} held`
         + (inv.beta_vs_underlying != null ? ` · beta ${num(inv.beta_vs_underlying)}` : "")
       : "",
     means: "the annual return left after what beta times the underlying explains — the "
          + "part that is not the underlying moving",
     missing: "needs an equity curve and a benchmark series"},
    {name: "net P&L", value: money(RUN.net_pnl),
     means: "what the whole run made or lost after costs",
     interval: iv.net_pnl, format: moneyShort},
    // The backend bootstraps the mean per trade and reports the estimate with it, so the
    // card reads that rather than dividing the total by the count: a division here produces
    // a number beside an interval that was computed from a different quantity.
    {name: "mean per trade", means: "the average trade, with the bootstrap's own estimate",
     value: iv.mean_pnl && !absent(iv.mean_pnl.estimate) ? money(iv.mean_pnl.estimate) : null,
     note: `over ${RUN.n_trades} trades`,
     interval: iv.mean_pnl, format: moneyShort,
     missing: "the bootstrap declines below ten trades, so there is no estimate to show"},
    {name: "win rate", value: pct(RUN.win_rate),
     means: "the share of trades that finished above zero; the note says how much, which is "
          + "the half a rate cannot tell you",
     // The design plan asks for a payoff ratio. The backend computes a **profit factor** —
     // gross wins over gross losses — which is a different statistic, so it is named for
     // the one that exists rather than relabelled into the one that was asked for.
     note: ts && ts.avg_win != null
       ? `win ${moneyShort(ts.avg_win)} · loss ${moneyShort(ts.avg_loss)}`
         + (ts.profit_factor != null ? ` · profit factor ${num(ts.profit_factor)}` : "")
       : ""},
    {name: "top 5 trades' share", value: inv && pct(inv.top5_share), better: "lower",
     means: "how much of the total profit came from the five best trades",
     note: inv && inv.without_top5 != null ? `without them ${money(inv.without_top5)}` : "",
     missing: "no positive total to take a share of"},
  ];

  // Four lead the band at full size and the rest follow smaller. Eight cells of equal
  // weight give the eye nowhere to land, and a reader who must weigh every number equally
  // weighs none of them.
  const ordered = [...cards.filter(c => c.lead), ...cards.filter(c => !c.lead)];
  return `<dl class="cards">${ordered.map(c => {
    const absentCard = c.value == null || c.value === "—" || c.value === false;
    return `<div class="card${c.lead ? " lead" : ""}${thin ? " thin" : ""}${
      absentCard ? " empty" : ""}"${thin
        ? ` data-tip="Muted: ${RUN.n_trades} trades is below the ${floor} this project `
          + `requires before a statistic carries an interval."` : ""}>
      <dt>${esc(c.name)}${c.better
        ? `<span class="card-dir" data-tip="Which way is better for this metric: ${
            esc(c.better)}.">${
            c.better === "higher" ? "↑" : c.better === "lower" ? "↓" : "→0"}</span>` : ""}</dt>
      <dd>${absentCard ? "—" : esc(c.value)}</dd>
      ${absentCard ? "" : intervalBar(c.interval, c.format)}
      ${absentCard && c.missing ? `<p class="card-why">${esc(c.missing)}</p>` : ""}
      ${!absentCard && c.note ? `<p class="card-note">${esc(c.note)}</p>` : ""}
    </div>`;
  }).join("")}</dl>`;
}

/** A thin bar beneath the value: the range a different sample would plausibly have given.
 *
 * The design plan puts one under every metric card. Two of the eight have one, because two
 * are what the backend bootstraps — and a bar drawn under the other six would be a bar this
 * file invented. The rest carry their interval in the verdict sentence above the band, which
 * is where the headline figure's own spread belongs.
 *
 * **Marked when it contains zero**, because then the sign of the result is not established
 * and the point estimate alone reads as a finding. The span is the interval plus a fifth of
 * its width at each end, so a bar that clears zero narrowly still shows the gap rather than
 * ending exactly at the edge. */
function intervalBar(interval, format = moneyShort){
  if(!interval || absent(interval.low) || absent(interval.high)) return "";
  const {low, high} = interval;
  const pad = (high - low) / 5 || Math.abs(high) / 5 || 1;
  const from = Math.min(low, 0) - pad, to = Math.max(high, 0) + pad;
  const at = v => ((v - from) / ((to - from) || 1)) * 100;
  const crosses = low <= 0 && 0 <= high;
  const level = absent(interval.level) ? "" : `${Math.round(interval.level * 100)}%`;
  return `<div class="card-iv${crosses ? " crosses" : ""}"
       data-tip="${level} of resamples of these trades landed between ${format(low)} and ${
         format(high)}${crosses ? ". It spans zero, so the sign is not established." : ""}">
    <span class="card-iv-track">
      <span class="card-iv-span" style="left:${at(low).toFixed(1)}%;width:${
        (at(high) - at(low)).toFixed(1)}%"></span>
      <span class="card-iv-zero" style="left:${at(0).toFixed(1)}%"></span>
    </span>
    <span class="card-iv-ends">${esc(format(low))} … ${esc(format(high))}</span>
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
        : "The trade-table engine builds none. Rerun on the event-driven engine."
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
        key: "equity", title: `equity, marked at the ${MARK === "liq" ? "bid" : "mid"}`
          + (SCALE === "log" ? ", log scale" : ""),
        log: SCALE === "log",
        // The benchmark is the comparison that makes the line mean anything: a strategy
        // return shown alone cannot be judged, and this one is beaten by simply holding
        // the underlying. Where no benchmark was stored the other mark takes the second
        // line, which is the previous behaviour.
        hint: benchmark.some(v => v != null)
          ? "the thin line is buying and holding the underlying"
          : "the thin line is the other mark",
        height: 230,
        values: curve,
        second: benchmark.some(v => v != null) ? benchmark : other,
        secondTitle: benchmark.some(v => v != null)
          ? "buy and hold the underlying, from the same capital"
          : (MARK === "liq" ? "marked at the mid" : "liquidation value, at the bid"),
        baseline: curve[0], format: money,
      }),
      drawdown.some(v => v != null) && underwaterLayer({
        key: "drawdown", title: "drawdown", hint: "distance below the running peak",
        height: 80, values: drawdown, format: pct,
      }),
      areaLayer({
        key: "deployed", title: "capital deployed", hint: "against the account",
        height: 70, values: equity.map(r => r.deployed), limit: capital, format: money,
      }),
      areaLayer({
        key: "positions", title: "positions held", height: 56,
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
/** Recompute what the table and the tabs see, from every filter at once.
 *
 * The brushed range and the closing slot are two filters on one set, and the design plan
 * asks for both — "brushing filters every chart and the trade table", and "clicking a slot
 * anywhere filters the page to that slot's actions". They compose: brushing a drawdown and
 * then clicking the stop loss asks "what did the stop do inside this fall", which is the
 * question flow 2 of the addendum is made of, and neither filter alone answers it. */
function recompute(){
  VIEW = (RUN.rows || []).filter(r =>
    (!RANGE || (r.entry_date >= RANGE.from && r.entry_date <= RANGE.to))
    && (!CLOSED_BY || r.exit_reason === CLOSED_BY));
  // A different set is a different first page: without this, narrowing to twelve trades after
  // scrolling through three hundred would keep drawing three hundred rows' worth of nothing.
  DRAWN = PAGE;
  paintRangeChip();
  if(PICKED && !VIEW.includes(PICKED)) pick(null);
  paintDetail();
  if(TAB && TAB !== "overview") renderTab(TAB, {});
}

/** Filter to the trades one slot closed, or clear it. The same control does both, because
 *  the way out of a filter should be where the way in was. */
function closedBy(reason){
  CLOSED_BY = reason && reason !== CLOSED_BY ? reason : null;
  SCROLL_TO_FIRST = Boolean(CLOSED_BY);
  replace({closed: CLOSED_BY});
  recompute();
}

function applyBrush(range){
  // Take the table to where the selection starts rather than leaving the reader to find
  // it among four hundred rows.
  SCROLL_TO_FIRST = Boolean(range);
  RANGE = range || null;
  recompute();
}

/** The brushed range, with a way out of it.
 *
 * Clearing a brush was a click on a chart, which the page explained in a sentence — and a
 * control a reader has to be told about in prose is one they will not find. The chip says
 * what is selected and how many trades it holds, and the button undoes it. */
function paintRangeChip(){
  const host = $("rangeChip");
  if(!host) return;
  const on = Boolean(RANGE || CLOSED_BY);
  host.hidden = !on;
  if(!on){ host.innerHTML = ""; return; }
  // Each filter says what it is and offers its own way out, because clearing the range and
  // clearing the slot are different intentions and one button for both would guess.
  host.innerHTML =
    (RANGE ? `<span class="chip-range">${esc(RANGE.from)} → ${esc(RANGE.to)}</span>
       <button type="button" id="clearRange">clear range</button>` : "")
    + (CLOSED_BY ? `<span class="chip-range">closed by ${esc(REASON[CLOSED_BY] || CLOSED_BY)}</span>
       <button type="button" id="clearClosed">clear slot</button>` : "")
    + `<span class="chip-n">${VIEW.length} of ${(RUN.rows || []).length} trades</span>`;
  $("clearRange")?.addEventListener("click", () => {
    if(STACK) STACK.setBrush(null);
    applyBrush(null);
    // The stack writes the range into the URL when it is dragged; clearing from here has
    // to take it out, or a reload restores a range the page is no longer showing.
    replace({range: null});
  });
  $("clearClosed")?.addEventListener("click", () => closedBy(null));
}

/* ── the detail tier ────────────────────────────────────────────────────────── */

const COLUMNS = [
  {key: "entry_date", name: "opened"},
  {key: "exit_date", name: "closed"},
  // The readable form, with the engine's own id on hover — the design plan's rule for a
  // contract, and the reason is scanning: `QQQ_20200207_C_00219000` hides the strike in a
  // zero-padded field, and a column of four hundred of them cannot be read down at all.
  {key: "contract", name: "contract", mono: true, left: true,
   fmt: v => contractParts(v).readable, title: v => String(v ?? "")},
  {key: "quantity", name: "qty", fmt: v => num(v, 0)},
  {key: "entry_price", name: "in", fmt: v => num(v)},
  {key: "exit_price", name: "out", fmt: v => num(v)},
  {key: "net_pnl", name: "net P&L ($)", fmt: bare, sign: true},
  // The readable name on screen and **the stored one on hover**. They are not the same word —
  // the column says "holding cap" and the trade log says `time_exit` — so a reader moving
  // between this table and an exported file has to be able to see both without leaving.
  {key: "exit_reason", name: "closed by", left: true, fmt: v => REASON[v] || v || "—",
   title: v => v ? `stored as ${v}` : ""},
  {key: "holding_days", name: "held (trading days)", fmt: v => num(v, 0)},
  {key: "entry_iv", name: "IV in", fmt: pct},
  {key: "entry_delta", name: "Δ in", fmt: v => num(v, 3)},
  // How far the trade ran in favour and against while it was open. Both are named in the
  // design plan's default columns and both reach the page as "—" on a run stored before
  // `mfe`/`mae` were passed through, which is a fact about that run rather than a gap here.
  {key: "mfe", name: "best", fmt: pct, sign: true},
  {key: "mae", name: "worst", fmt: pct, sign: true},
];

/* Which column the table is ordered by, and which way. The plan asks for sorting and the
   table had none, so the only order available was the one the trades happened in — which
   answers "what happened next" and never "which was the worst". */
let SORT = {key: null, dir: -1};

/* ── how many rows are drawn, and why it is not all of them ──────────────────────
 *
 * Four hundred rows cost **264 ms of the page's 366**, measured, and no screen shows more
 * than about fifteen at once. So the table draws a page and grows as the reader scrolls into
 * it: the first paint builds sixty rows instead of four hundred, and each further page is
 * appended rather than redrawing the table — appending keeps the focused row, which a
 * repaint throws away, and the keyboard then walks the whole of what is drawn.
 *
 * This is the plan's virtualisation at the scale this data actually has. A true windowing
 * recycles rows and needs the scroll container's geometry; growing a list does not, and at
 * four hundred rows the difference is between 40 ms and 264 ms, not between 40 and 4.
 */
const PAGE = 60;

/** How many of the filtered set are currently in the document. Reset by anything that
 *  changes the order or the set, grown by scrolling. */
let DRAWN = PAGE;

/** The rows in the order the header asks for. A column with no value sorts to the end
 *  whichever way the arrow points: an absent number is not a small one, and letting it
 *  float to the top of a descending sort would read as the extreme. */
function sorted(rows){
  if(!SORT.key) return rows;
  const col = COLUMNS.find(c => c.key === SORT.key);
  const val = r => r[SORT.key];
  return [...rows].sort((a, b) => {
    const x = val(a), y = val(b);
    if(absent(x) && absent(y)) return 0;
    if(absent(x)) return 1;
    if(absent(y)) return -1;
    return (typeof x === "number" && typeof y === "number"
      ? x - y : String(x).localeCompare(String(y))) * SORT.dir;
  });
}

/** One row of the trade table. Shared by the first paint and by each appended page, so the
 *  two cannot drift into drawing different rows. */
function rowHtml(r, i){
  return `<tr tabindex="0" data-row="${i}"
    data-trade-id="${esc(String(r.trade_id ?? ""))}"${
    r === PICKED ? ' class="picked" aria-selected="true"' : ""}>${
    COLUMNS.map(c => {
      const raw = r[c.key];
      const text = c.fmt ? c.fmt(raw) : (raw ?? "—");
      const cls = [c.left ? "left" : "", c.mono ? "mono" : "",
                   c.sign && !absent(raw) ? (raw > 0 ? "pos" : raw < 0 ? "neg" : "") : ""]
                  .filter(Boolean).join(" ");
      return `<td${cls ? ` class="${cls}"` : ""}${c.title
        ? ` title="${esc(c.title(raw))}"` : ""}>${esc(text)}</td>`;
    }).join("")}</tr>`;
}

/** What a row answers to. `order` is the sorted set the indices point into, so an appended
 *  page selects the right trade. */
function bindRow(tr, order){
  const box = $("runDetail");
  // A click selects and shades; opening the card is a second, deliberate action. The plan
  // asks for both and they are different intentions: "what was the book doing while this was
  // on" is answered by the shading alone, and a drawer that opened on every click would cover
  // the chart that answers it.
  tr.addEventListener("click", () => pick(order[+tr.dataset.row]));
  tr.addEventListener("dblclick", () => openCard(order[+tr.dataset.row]));
  // Arrows walk the table, Enter opens the card, Escape clears — the design plan's keyboard
  // rule for tables.
  tr.addEventListener("keydown", e => {
    const i = +tr.dataset.row;
    if(e.key === "ArrowDown" || e.key === "ArrowUp"){
      e.preventDefault();
      const next = box.querySelector(`[data-row="${i + (e.key === "ArrowDown" ? 1 : -1)}"]`);
      if(next){ next.focus(); pick(order[+next.dataset.row]); }
    }else if(e.key === "Enter"){
      e.preventDefault(); openCard(order[i]);
    }else if(e.key === " "){
      e.preventDefault(); pick(order[i]);
    }else if(e.key === "Escape"){ pick(null); }
  });
}

function paintDetail(){
  const box = $("runDetail");
  if(!box) return;
  const rows = VIEW;
  // The note names **which** filters narrowed the table. It said "in the brushed range"
  // whatever was on, so filtering by a closing slot with no range read as a range nobody had
  // drawn — and the reader's next move is to look for a brush that is not there.
  const all = (RUN.rows || []).length;
  const by = [RANGE ? "the brushed range" : "", CLOSED_BY ? "the closing slot" : ""]
    .filter(Boolean).join(" and ");
  $("runDetailNote").textContent = rows.length === all
    ? `${all.toLocaleString()} trades`
    : `${rows.length.toLocaleString()} of ${all.toLocaleString()} trades${
        by ? `, narrowed by ${by}` : ""}`;
  if(!rows.length){
    box.innerHTML = `<p class="hint">No trade opened inside the brushed range.</p>`;
    return;
  }
  const order = sorted(rows);
  const shown = order.slice(0, DRAWN);
  box.innerHTML = `<table class="trades"><thead><tr>${
    COLUMNS.map(c => `<th${c.left ? ' class="left"' : ""}><button type="button"
      class="sortby${SORT.key === c.key ? " on" : ""}" data-sort="${esc(c.key)}"
      >${esc(c.name)}${SORT.key === c.key
        ? `<span aria-hidden="true">${SORT.dir < 0 ? "↓" : "↑"}</span>` : ""}</button></th>`).join("")
  }</tr></thead><tbody>${shown.map((r, i) => rowHtml(r, i)).join("")}</tbody></table>
    <p class="hint trades-more" id="tradesMore"${order.length > shown.length ? "" : " hidden"}
      >${shown.length} of ${order.length.toLocaleString()} drawn, in the order the table is
       sorted by. Scrolling brings the rest in.</p>`;

  for(const th of box.querySelectorAll("[data-sort]"))
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      // Same column flips the direction; a new one starts descending, because the first
      // question of a sorted column is almost always "which are the biggest".
      SORT = SORT.key === key ? {key, dir: -SORT.dir} : {key, dir: -1};
      DRAWN = PAGE;                    // a new order is a new first page
      paintDetail();
    });

  for(const tr of box.querySelectorAll("tbody tr")) bindRow(tr, order);

  // The next page, when the foot of the table comes into view. **Appended, not repainted**: a
  // repaint throws away the focused row, so the keyboard would walk one step and stop, which
  // is a bug this table has had once already.
  const more = $("tradesMore");
  if(more && !more.hidden){
    const watch = new IntersectionObserver(entries => {
      if(!entries[0].isIntersecting) return;
      const from = DRAWN;
      DRAWN = Math.min(order.length, DRAWN + PAGE);
      const body = box.querySelector("tbody");
      if(!body) return;
      body.insertAdjacentHTML("beforeend",
        order.slice(from, DRAWN).map((r, i) => rowHtml(r, from + i)).join(""));
      for(const tr of [...body.children].slice(from)) bindRow(tr, order);
      more.textContent = `${DRAWN} of ${order.length.toLocaleString()} drawn, in the order `
        + `the table is sorted by. Scrolling brings the rest in.`;
      if(DRAWN >= order.length){ more.hidden = true; watch.disconnect(); }
      // **Watched inside the box that scrolls, when there is one.** The detail tier is an
      // `overflow: auto` panel, so a row scrolled out of it is not intersecting the viewport
      // and an observer on the window never fires — the table simply stopped at sixty with a
      // line underneath promising more.
    }, {root: box.scrollHeight > box.clientHeight ? box : null, rootMargin: "400px"});
    watch.observe(more);
  }

  // A brushed range scrolls the table to the first trade inside it, rather than leaving the
  // reader to find where the selection starts.
  if(SCROLL_TO_FIRST && shown.length){
    SCROLL_TO_FIRST = false;
    box.querySelector("tbody tr")?.scrollIntoView({block: "nearest"});
  }
}

/** Select a trade, or clear the selection.
 *
 * One function, because the three things that happen have to happen together: the row is
 * marked, the charts shade the period it was open, and the address bar carries it. A
 * selection that the link does not carry is a reading that cannot be sent to anyone. */
function pick(row, {toggle = true} = {}){
  PICKED = row && (!toggle || row !== PICKED) ? row : null;
  const held = PICKED && PICKED.entry_date && PICKED.exit_date
    ? {from: String(PICKED.entry_date).slice(0, 10), to: String(PICKED.exit_date).slice(0, 10)}
    : null;
  if(STACK) STACK.setHeld(held);
  // `replace` deletes any key whose value is null, so `card` is only mentioned when it is
  // being taken out — naming it with a null-ish value on every selection would close the card
  // the moment the reader clicked the next row.
  replace(PICKED
    ? {trade: String(PICKED.trade_id ?? "")}
    : {trade: null, card: null});
  // Clearing the selection closes the card: a card with nothing selected behind it is a
  // reading of a trade the rest of the page has stopped showing.
  if(!PICKED && cardOpen()) closeDrawer();

  // The marks are moved in place rather than by repainting the table. Rebuilding it
  // destroyed the focused row, so the first arrow key worked and the second had nothing
  // to act on — the keyboard walked one step and stopped.
  const box = $("runDetail");
  if(!box) return;
  for(const tr of box.querySelectorAll("tbody tr")){
    const on = PICKED !== null && tr.dataset.tradeId === String(PICKED.trade_id ?? "");
    tr.classList.toggle("picked", on);
    if(on) tr.setAttribute("aria-selected", "true");
    else tr.removeAttribute("aria-selected");
  }
}

/* ── the trade card ─────────────────────────────────────────────────────────────
 *
 * The design plan's section 7, opened in the right drawer so the chart stack and the table
 * stay where they were — its fifth principle, "drill down without navigating away".
 *
 * Opening it does three things at once, which is why they are one function: the row is
 * selected, the charts shade the period the trade was open, and the URL carries both the
 * trade and the fact that the card is open, so the link a reader sends opens the card and not
 * only the page behind it.
 */
function openCard(row){
  if(!row) return;
  // Not a toggle here. A click on a row toggles its selection, which is right for selecting;
  // asking for the card twice is asking for the card, and clearing the selection under an
  // open drawer would leave a card on screen with nothing shaded behind it.
  pick(row, {toggle: false});
  replace({card: "1"});
  const order = sorted(VIEW).slice(0, DRAWN);
  const spec = {
    title: `trade ${row.trade_id}`,
    subtitle: `${esc(contractParts(row.contract).underlying)} · ${
      esc(dateLong(row.entry_date))} → ${esc(dateLong(row.exit_date))}`,
    actions: cardActions(row),
    body: cardBody(row, {config: RUN.config, position: order.indexOf(row) + 1,
                         total: order.length}),
  };
  // The same call whether the card is opening or walking to the next trade: the drawer only
  // notes where to return focus and scroll **on the way in**, so J and K through forty trades
  // still come back to the row the reader started from, and the width they dragged stays.
  openDrawer(spec, () => replace({card: null}));
  paintCardActions(row);
}

/** The card's own controls. Rebound after each paint, because the header is rewritten. */
function paintCardActions(row){
  $("tradeLink")?.addEventListener("click", copyLink);
  $("tradeLocate")?.addEventListener("click", () => {
    if(TAB !== "overview") step({tab: "overview"});
    SCROLL_TO_FIRST = true;
    paintDetail();
  });
  $("tradeSlot")?.addEventListener("click", () => closedBy(row.exit_reason));
}

/* ── the page ───────────────────────────────────────────────────────────────── */

function render(query){
  const tab = TABS.some(t => t.key === query.tab) ? query.tab : "overview";
  $("view-run").innerHTML = `
    <section class="tier-top">
      <header class="run-head">
        <h2>run ${RUN.id}<span class="run-idea">${esc(RUN.strategy_name || "untitled")} · version ${
          RUN.strategy_ver ?? "?"}</span></h2>
        <p class="run-cfg">enters when ${
          esc(typeof RUN.config?.entry === "string"
            ? RUN.config.entry : "a plan function decides")}</p>
        <div class="run-actions">
          <button type="button" class="chip" id="markToggle"
            data-tip="Display only (M). Both marks are stored; the metrics and the stop
                      triggers were computed on the run's configured mark."
            >showing the ${MARK === "liq" ? "bid" : "mid"} mark</button>
          <button type="button" class="chip" id="scaleToggle"
            data-tip="Display only (L). A log axis makes equal percentage moves equal
                      distances, which is how a multi-year curve should be read."
            >${SCALE === "log" ? "log scale" : "linear scale"}</button>
          <a class="chip" href="#/log">experiment log</a>
          <a class="chip" href="#/bench">compose a run</a>
          <button type="button" class="chip" id="archiveRun">${
            RUN.archived ? "restore" : "archive"}</button>
          <button type="button" class="chip danger" id="deleteRun">delete</button>
        </div>
      </header>
      <div id="deleteBox"></div>
      ${verdict()}
      ${metricCards()}
      ${evidence()}
    </section>

    <div class="range-chip" id="rangeChip" hidden></div>
    <nav class="run-tabs" role="tablist">${TABS.map(t =>
      `<button type="button" class="tab${t.key === tab ? " on" : ""}" role="tab"
         data-tab="${t.key}">${esc(t.name)}</button>`).join("")}</nav>

    <section class="tier-mid" id="tierMid"></section>

    <section class="tier-detail">
      <header class="tier-head"><h3>the trades</h3>
        <p class="hint tier-count" id="runDetailNote"></p></header>
      <div class="scroll" id="runDetail"></div>
    </section>`;

  $("archiveRun").addEventListener("click", archiveRun);
  $("deleteRun").addEventListener("click", deleteRun);
  $("markToggle").addEventListener("click", () => setMark(MARK === "mid" ? "liq" : "mid"));
  $("scaleToggle").addEventListener("click",
    () => setScale(SCALE === "log" ? "linear" : "log"));
  for(const button of $("view-run").querySelectorAll("[data-tab]"))
    button.addEventListener("click", () => {
      // A step, not a replace: the addendum asks that view state in the URL make back and
      // forward work, and a tab is the piece a reader moves between on purpose. The hash
      // change re-enters the route, which renders it — rendering here as well would draw
      // the tab twice and throw away the chart stack mid-draw.
      step({tab: button.dataset.tab});
    });

  bindEvidence();

  // A link carries the whole reading — the tab, the brushed range, the closing slot, the
  // mark and the trade — and every part of it is restored here, before the tab is drawn.
  // This lived inside the overview branch of `renderTab`, so a link that landed on any
  // other tab silently dropped the range and the filter while the URL still said they
  // were on.
  // **Assigned from the query every time, including when the query does not have it.** These
  // were only set when the key was present, so the module kept the last value: opening a run
  // with a brushed range, leaving for the log and coming back without one left the table
  // filtered while the address bar said it was not — the URL is supposed to be the only thing
  // holding this state, and a leftover in a module is exactly the drift it exists to prevent.
  CLOSED_BY = query.closed ? String(query.closed) : null;
  PICKED = query.trade != null && query.trade !== ""
    ? (RUN.rows || []).find(r => String(r.trade_id ?? "") === String(query.trade)) || null
    : null;
  const span = query.range ? String(query.range).split("..") : [];
  RANGE = span[0] && span[1] ? {from: span[0], to: span[1]} : null;
  recompute();
  renderTab(tab, query);
  // The card is part of the reading a link carries, so a link that says the card was open
  // opens it. After the tab, because locating the trade on the chart needs the stack built.
  if(query.card && PICKED) openCard(PICKED);
}

/* ── archiving and deleting ──────────────────────────────────────────────────────
 *
 * Two operations, and which one is right turns on a question about the **record**, not
 * about the run:
 *
 *   archive   research that did not work out. It stays in the trial count, because
 *             `count_trials` deflates the best result by how much searching produced it —
 *             a search that quietly shrinks every time a failure is tidied away reports
 *             the survivor as more significant than the work supports. Reversible.
 *   delete    a record that was never research: a guard's own test run, a configuration
 *             with a typo, a curl aimed at the wrong port. Those must **not** be archived,
 *             because a search size inflated by somebody else's test runs understates the
 *             result just as surely as a deleted failure overstates it. Not reversible.
 *
 * The design plan said archive and never delete, and for research that is right. It did
 * not account for a store that accumulates runs nobody performed — twenty-seven of this
 * one's hundred and seventy-one came from a guard that ran a real backtest on every
 * invocation. Deleting those is not tidying away evidence, it is removing something that
 * was never evidence.
 */
async function archiveRun(){
  const now = !RUN.archived;
  try{
    await postJSON(API.runArchive, {id: RUN.id, archived: now});
  }catch(e){ notice(`could not ${now ? "archive" : "restore"} run ${RUN.id}: ${e.message}`); return; }
  RUN.archived = now ? 1 : 0;   // the cached record is this object, so it follows
  render({tab: TAB, mark: MARK});
}

/** Delete, having first said what that takes.
 *
 * Two steps in the page rather than a `confirm()`. A browser dialog can hold one line and
 * the question it asks is "did you mean it", which the reader has already answered by
 * pressing the button; the question they actually have is **what else goes with this**,
 * and that is a list. It stays on screen, where it can be read twice and where the
 * alternative — archiving — is one click away rather than behind a dismissed modal. */
async function deleteRun(){
  const host = $("deleteBox");
  if(host.dataset.armed === "yes"){ host.innerHTML = ""; host.dataset.armed = ""; return; }

  let takes;
  try{
    takes = await postJSON(API.runDeletion, {id: RUN.id});
  }catch(e){ notice(`could not read what deleting run ${RUN.id} would take: ${e.message}`); return; }

  const lines = [
    `${takes.strategy} · ${takes.n_trades} trades · ${money(takes.net_pnl)} · ${
      String(takes.started).slice(0, 10)}`,
    takes.notes ? `${takes.notes} note${takes.notes === 1 ? "" : "s"} written about it`
      : "no notes",
    takes.files.length ? takes.files.join(", ") : "no files on disk",
  ];
  // The case where deleting changes what every other run under this name means.
  const cost = takes.last_of_version && takes.counted_as_a_trial
    ? `<p class="del-cost">This is the only run of that configuration. Deleting it takes
       one trial out of the search under <b>${esc(RUN.strategy_name || "untitled")}</b>,
       and every other run's deflated result rises to match.</p>`
    : "";

  host.dataset.armed = "yes";
  host.innerHTML = `<div class="del">
    <h4>Delete run ${takes.run_id} permanently</h4>
    <ul>${lines.map(l => `<li>${esc(l)}</li>`).join("")}</ul>
    ${cost}
    <p class="del-alt"><b>Archive instead</b> if this was research that did not work —
      archiving hides it and keeps it in the trial count. Delete is for records that were
      never research: a guard's test run, a typo'd configuration, a request aimed at the
      wrong port.</p>
    <div class="del-row">
      <button type="button" class="chip danger" id="deleteGo">delete permanently</button>
      <button type="button" class="chip" id="deleteNo">keep it</button>
    </div>
  </div>`;

  $("deleteNo").addEventListener("click", () => {
    host.innerHTML = ""; host.dataset.armed = "";
  });
  $("deleteGo").addEventListener("click", async () => {
    try{
      const out = await postJSON(API.runDelete, {id: RUN.id});
      forget(RUN.id);
      notice(`run ${out.took.run_id} deleted, with ${out.took.notes} note(s) and `
        + `${out.took.files.length} file(s).`);
    }catch(e){ notice(`could not delete run ${RUN.id}: ${e.message}`); return; }
    go("/bench");
  });
}


/** Render a tab, keeping a failure inside it.
 *
 * The addendum's rule: "a layer that fails shows its own error and retry while the rest of
 * the page renders". Every renderer was called straight, so one throwing took the whole
 * tab with it — the metric cards and the trade table above and below it are computed and
 * correct, and the reader saw a half-drawn page with a console message they never open.
 *
 * The error says which part failed and offers the one action that helps, because a chart
 * that failed on a transient is worth a second attempt and one that failed on its data is
 * worth knowing about. */
function renderTab(tab, query){
  try{
    renderTabBody(tab, query);
  }catch(e){
    const host = $("tierMid");
    host.innerHTML = `<div class="layer-error">
      <h4>this tab could not be drawn</h4>
      <p>${esc(e.message)}</p>
      <button type="button" id="retryTab">draw it again</button>
    </div>`;
    $("retryTab").addEventListener("click", () => renderTab(tab, query));
    // Still reported, so a failure that happens once is in the record rather than only on
    // the screen it happened on.
    notice(`the ${tab} tab could not be drawn: ${e.message}`);
  }
}

function renderTabBody(tab, query){
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
    host.innerHTML = `<p class="hint stack-lead">Drag across any chart to select a period.
      Triangles on the axis mark days the run logged an event.</p>`;
    const box = document.createElement("div");
    host.append(box);
    if(STACK) STACK.destroy();
    STACK = buildStack(box);
    // A stack built after the range was restored still has to be told about it, since the
    // shading lives on the chart and the filter lives in `RANGE`.
    if(RANGE) STACK.setBrush(RANGE);
    if(PICKED && PICKED.entry_date && PICKED.exit_date)
      STACK.setHeld({from: String(PICKED.entry_date).slice(0, 10),
                     to: String(PICKED.exit_date).slice(0, 10)});
    return;
  }
  if(tab === "settings"){ host.innerHTML = settingsTab(); return; }
  if(tab === "spread"){ spreadTab(host); return; }
  if(tab === "slice"){ sliceTab(host); return; }
  if(tab === "attribution"){ attributionTab(host); return; }
  if(tab === "trades"){
    host.innerHTML = exitReasonTab();
    for(const tr of host.querySelectorAll("[data-closed]"))
      tr.addEventListener("click", () => closedBy(tr.dataset.closed));
    return;
  }
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
    ${VIEW.length === (RUN.rows || []).length ? ""
      : `<p class="hint"><b>Showing the ${VIEW.length} trades in the brushed
         period.</b></p>`}
    <h4>cumulative P&amp;L, trade by trade</h4>
    <div class="chart"><svg id="curve" role="img" aria-label="cumulative P&amp;L"></svg></div>
    <p class="hint" id="curveNote"></p>
    <div class="two-up">
      <section><h4>per-trade P&amp;L</h4>
        <div class="chart"><svg id="hist" role="img"
          aria-label="per-trade P&amp;L distribution"></svg></div></section>
      <section><h4 id="periodHead">by period</h4>
        <div class="chart"><svg id="years" role="img" aria-label="P&amp;L by period"></svg></div>
      </section>
    </div>
    <h4>does it hold without the best few trades</h4>
    <table class="plain" id="robust"></table></div>`;
  renderCurve(VIEW, false);
  renderHist(VIEW);
  renderPeriods(VIEW);
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
    <p class="hint">Equal-sized groups by one measurement taken at entry.</p>
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
      <p class="hint">Mean per trade; cells with few trades are dimmed.</p>
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
    return `<p class="hint">The slot definitions could not be fetched, so there is
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
    // "form" is the registry's own word for a slot's registered implementation, and on
    // screen it means nothing: a reader sees "decided by the trailing form" and has to
    // guess whether a form is a shape, a document or a setting. The slot is decided by a
    // **rule**, and the rule has a name.
    if(chosen)
      rows.push([`rule`,
                 `${chosen.variant}${chosen.version != null ? ` v${chosen.version}` : ""}`,
                 "this slot was decided by a named rule rather than by the level below"],
                ...Object.entries(chosen.params || {}).map(([k, v]) => [`　${k}`, v, ""]));
    for(const field of comp.inputs || []){
      // The field a chosen rule replaced is not missing, it is superseded, and the row
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
    <div class="settings">${groups.map(g => `
      <section class="setting-group">
        <h4>${esc(g.name)}</h4>
        ${g.rows.length ? `<dl>${g.rows.map(([k, v, why]) => `
          <dt>${esc(k)}</dt>
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

/** Why a component shows nothing: its own words, whole.
 *
 * It was cut at ninety characters with an ellipsis, which on the settings tab — where there
 * is a column's width to spare and nothing competing for it — removed the end of a sentence
 * for no gain. */
function shortGap(g){
  return (g.gap || "").replace(/\*\*/g, "").trim() || "nothing to configure yet";
}

/* The attribution tab.
 *
 * It was a five-row table of numbers with a proportional bar beside each. Attribution is
 * an identity — the Greeks and the residual add up to the result — and neither a table
 * nor a bar scaled against the sum of absolute values can show that. The waterfall does,
 * and it makes the shape of this particular run readable at a glance: theta took more
 * than the whole loss and gamma gave a third of it back.
 *
 * Returns markup and then paints, because the tab's host is written in one go. */
function attributionTab(host){
  const attr = RUN.attribution || RUN.analysis?.attribution;
  if(!attr || !attr.totals){
    host.innerHTML = `<p class="hint">No attribution was recorded for this run, so there `
      + `is nothing to split into delta, gamma, vega and theta.</p>`;
    return;
  }
  // Written, then painted — in that order and in the same turn. It used to return markup
  // and schedule the paint on `requestAnimationFrame`, which works on a visible page and
  // not otherwise: a browser throttles rAF in an off-screen frame, so the chart simply
  // never appeared. Every other tab takes its host and paints into it, and nothing here
  // needed a frame boundary — `innerHTML` is synchronous, so the element exists on the
  // next line.
  const bad = attr.high_residual;
  host.innerHTML = `<div class="pad">
    <div class="chart"><svg id="attr" role="img"
      aria-label="P&amp;L attribution by Greek"></svg></div>
    ${bad ? `<p class="hint warn-text">${bad} ${bad === 1 ? "trade has" : "trades have"} a
       residual too large to place, so that much of this is unattributed.</p>` : ""}</div>`;
  renderAttribution(attr, RUN.net_pnl ?? null);
}

/* Each row is one slot's closings, and clicking it filters the page to them — the design
   plan's "clicking a slot anywhere filters the page to that slot's actions", at the level
   the data supports. Filtering by Greek, which the addendum's flow 2 also asks for, needs
   per-trade attribution the backend does not send; the run totals cannot say which trade a
   Greek drove. */
function exitReasonTab(){
  const rows = RUN.analysis?.by_exit_reason;
  if(!rows || !rows.length) return `<p class="hint">No exit breakdown for this run.</p>`;
  const cols = Object.keys(rows[0]);
  const key = cols[0];
  return `<div class="pad">
    <table class="plain pickable"><thead><tr>${cols.map(c =>
      `<th${c === cols[0] ? ' class="left"' : ""}>${esc(label(c))}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r => `<tr data-closed="${esc(String(r[key]))}"${
      String(r[key]) === CLOSED_BY ? ' class="picked"' : ""}>${cols.map((c, i) => {
      const v = r[c];
      return `<td class="${i ? "n" : "left"}"${i === 0 && typeof v !== "number"
        ? ` title="stored as ${esc(String(v))}"` : ""}>${
        typeof v === "number" ? esc(fmtVal(c, v)) : esc(REASON[v] || v)}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table>
    <p class="hint">Click a row to filter the trade table to the trades it closed.</p></div>`;
}

/* The costs the engine charged, as money rather than as field names.
 *
 * `label()` falls back to the key when it has no entry for it, which put GROSS_PNL,
 * FEES_PCT_OF_GROSS and EATEN_BY_COSTS on screen as themselves, and printed the last of
 * them as the word "false". A reader should not have to know the backend's identifiers to
 * read their own costs, and a boolean is a sentence, not a value. */
const COST_LABEL = {
  commission: "commission", regulatory: "regulatory fees", slippage: "slippage",
  fees: "fees in total", spread: "spread paid", total: "total cost",
  premium_traded: "premium paid", gross_pnl: "before costs", net_pnl: "after costs",
  gross_pnl_at_mid: "before costs, at the mid",
  cost_pct_of_premium: "cost of the premium paid",
  fees_pct_of_premium: "fees of the premium paid",
  cost_pct_of_gross_at_mid: "cost of the edge",
  // What the record carried before the spread moved into this block. Kept so an older run
  // still reads as something rather than as its own field name.
  fees_pct_of_gross: "fees as a share of gross",
};

/** The two sentences the cost block writes for itself, which are not cards. */
const COST_NOTES = new Set(["spread_note", "gross_note", "eaten_by_costs"]);
/** Costs are money spent. A signed formatter prints 346 spent as "+346", which reads as a
 *  gain; the sign belongs to the P&L rows, not to the cost rows. */
const COST_SIGNED = new Set(["gross_pnl", "net_pnl"]);

function costsTab(){
  const costs = RUN.analysis?.costs;
  if(!costs) return `<p class="hint">No cost breakdown for this run.</p>`;
  const t = META?.thresholds?.cost_share_warning;
  const eaten = costs.eaten_by_costs;

  const cards = Object.entries(costs)
    .filter(([k]) => !COST_NOTES.has(k))           // sentences, shown as sentences below
    .map(([k, v]) => {
      // A null here is a measurement the run cannot make, not a zero — the spread without a
      // quote beside the fill is the case, and it is the largest cost an option buyer pays.
      const show = v == null ? "not measured"
        : typeof v !== "number" ? String(v)
        : Math.abs(v) < 1 ? pct(v)
        : COST_SIGNED.has(k) ? money(v) : size(v);
      return `<div class="card${v == null ? " empty" : ""}">
        <dt>${esc(COST_LABEL[k] || label(k))}</dt>
        <dd>${esc(show)}</dd></div>`;
    }).join("");

  // The notes the backend writes about what it could and could not measure. They are
  // sentences and belong as sentences — the spread one in particular, which says the largest
  // cost an option buyer pays is unmeasurable on this run rather than zero.
  const notes = ["spread_note", "gross_note"]
    .map(k => costs[k]).filter(Boolean)
    .map(text => `<p class="hint">${mark(String(text))}.</p>`).join("");

  return `<div class="pad">
    <dl class="cards">${cards}</dl>
    ${notes}
    ${eaten != null ? `<p class="hint${eaten ? " warn-text" : ""}">${eaten
      ? `<b>Gross was positive and net was not.</b> The edge did not cover the cost of
         trading it, so this result is a statement about the cost model as much as about the
         strategy.`
      : `Gross and net have the same sign, so the costs did not turn the result over. The
         share they took is above; this project stops trusting a result past
         ${pct(t?.value)}.`}</p>` : ""}</div>`;
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
    <p class="hint">Each re-runs the strategy, so none has run yet.</p>
    <ul class="checks">${(META?.validations || []).map(v => {
      const can = offered.has(v.kind);
      const inputs = (VALID_INPUTS[v.kind] || []).map(i =>
        `<label for="v_${v.kind}_${i.key}">${esc(i.label)}${
           i.hint ? ` <span class="vhint">${esc(i.hint)}</span>` : ""}</label>
         <input id="v_${v.kind}_${i.key}" value="${esc(i.value)}">`).join("");
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
      <p class="hint">Real windows, not simulated shocks.</p>
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
  // Everything this page registered globally comes off with it. A key bound to a run that is
  // no longer on screen is the quietest kind of bug: it acts on the record the module still
  // holds, and the page it acts on is somebody else's.
  if(UNBIND){ UNBIND(); UNBIND = null; }
  if(UNPROVIDE){ UNPROVIDE(); UNPROVIDE = null; }
  if(cardOpen()) closeDrawer();
}
