/* states.js — the gallery: every component, in every state, from the real modules.
 *
 * It imports what ships. Nothing here reimplements a component in order to show it; if
 * something could only be shown by reimplementing it, that is a finding about the
 * component, not a reason to draw a picture of it.
 *
 * The sample data is fabricated and says so. It is shaped like a real run's — a trade row
 * carries the columns `CONTRACT` declares — so a renderer that depends on a field will
 * fail here rather than in front of somebody looking at a result.
 */
"use strict";

import {$} from "./dom.js";
import {money, num, pct} from "./fmt.js";
import {notice, paintIssues} from "./net.js";
import {blankChart} from "./charts/svg.js";
import {startTips} from "./ui/tip.js";
import {renderEquity, renderEquityStrip} from "./charts/equity.js";
import {renderHist, renderPeriods, renderScatter} from "./charts/distribution.js";
import {renderDetail, renderRobust, renderStrip} from "./charts/tables.js";
import {renderAttribution} from "./charts/attribution.js";
import {chartStack, seriesLayer} from "./charts/axis.js";
import {closeDrawer, openDrawer, toggleFull} from "./ui/drawer.js";
import * as palette from "./ui/palette.js";
import {bind} from "./ui/keys.js";
import {actions as cardActions, card as tradeCard} from "./views/trade.js";

/* ── fabricated data, shaped like the real thing ── */
const TRADES = Array.from({length: 48}, (_, i) => {
  const swing = Math.sin(i / 3.1) + Math.cos(i / 7.7);
  return {
    trade_id: i + 1,
    entry_date: `202${3 + (i % 2)}-${String(1 + (i % 12)).padStart(2, "0")}-05`,
    exit_date: `202${3 + (i % 2)}-${String(1 + (i % 12)).padStart(2, "0")}-19`,
    // The engine's own contract id, which is what the card parses: underlying, expiry,
    // right, and the strike in thousandths. A readable string here would have hidden the
    // parse rather than shown it.
    contract: `QQQ_20240315_C_${String((380 + i) * 1000).padStart(8, "0")}`,
    quantity: 1 + (i % 3),
    entry_price: 4.2 + (i % 11) * 0.3,
    exit_price: 4.2 + swing * 2.4,
    gross_pnl: swing * 900,
    fees: 12 + (i % 4),
    net_pnl: swing * 900 - 12,
    exit_reason: ["take_profit", "stop_loss", "time_exit", "dte_floor"][i % 4],
    holding_days: 4 + (i % 17),
    entry_iv: 0.16 + (i % 9) * 0.012,
    entry_delta: 0.22 + (i % 7) * 0.035,
    entry_moneyness: -0.04 + (i % 8) * 0.012,
    entry_dte_calendar: 21 + (i % 25),
    entry_underlying_price: 380 + i * 0.7,
    // The trade card reads these, so the fabricated row carries them: a sample that left a
    // column out would make the card look finished here and blank on a real run. The names
    // are the ones `CONTRACT` declares.
    exit_underlying_price: 380 + i * 0.7 + swing * 6,
    exit_iv: 0.15 + (i % 7) * 0.013,
    exit_delta: 0.3 + (i % 5) * 0.06,
    mfe: 0.18 + (i % 6) * 0.22,
    mae: -0.05 - (i % 5) * 0.06,
    entry_sig_rsi_14: 38 + (i % 40),
    entry_sig_rv_20: 0.09 + (i % 6) * 0.01,
    entry_sig_mom_20: -0.03 + (i % 9) * 0.011,
  };
});

/* The configuration a card reads its closing levels out of — the level a stop was held to is
   in the config, not in the trade row, and the card says which. */
const SAMPLE_CONFIG = {
  symbol: "QQQ", start: "2023-01-01", end: "2024-12-31", engine: "event_driven",
  exit: {take_profit: 0.8, stop_loss: 0.4, hold_days: 20, dte_floor: 5},
};

const EQUITY = Array.from({length: 260}, (_, i) => {
  const drift = i * 42;
  const wobble = Math.sin(i / 11) * 2100 + Math.sin(i / 3) * 420;
  const mid = 100_000 + drift + wobble;
  return {
    date: new Date(Date.UTC(2024, 0, 2 + i)).toISOString().slice(0, 10),
    equity_mid: mid,
    equity_liq: mid - 900 - Math.abs(wobble) * 0.06,
    cash: 40_000,
    option_value: 12_000,
    positions: 2 + (i % 4),
    deployed: 18_000 + Math.abs(wobble),
  };
});

const EQUITY_METRICS = {
  total_return: 0.173, annualised: 0.168, annualised_vol: 0.21, sharpe: 0.79,
  max_drawdown: -0.094, max_drawdown_liq: -0.106, mean_capital_used: 0.19,
  peak_positions: 5, skew: -0.4, kurtosis: 3.1,
};

const ATTRIBUTION = {
  totals: {delta: 18_400, gamma: 3_200, vega: -7_900, theta: -6_100, residual: 820},
  share: {}, unusable: 0, high_residual: 3, n: 48,
};

/* ── the gallery ── */
const SECTIONS = [];

/** Register one section: a heading, why it is here, and the cases in it. */
function section(title, why, cases){
  SECTIONS.push({title, why, cases});
}

/** One labelled case.
 *
 * `html` is markup, **or a function returning it**. The difference matters: a case that
 * reads a computed token value has to be rebuilt when the theme changes, and the first
 * version built every case once at module load — before the theme was applied — so the
 * gallery showed a dark page labelled with the light palette's hex values. A gallery that
 * is wrong about what it is showing is worse than no gallery.
 *
 * `after` runs once the markup is in the document, which is what the chart renderers need
 * (they write into a node by id).
 */
function one(label, html, after){
  return {label, html, after};
}

const TOKENS = {
  "surfaces": ["--surface-0", "--surface-1", "--surface-2", "--surface-sunk"],
  "text": ["--text-primary", "--text-secondary", "--text-muted"],
  "interface semantics": ["--accent", "--success", "--warning", "--danger", "--on-accent"],
  "tints": ["--accent-soft", "--success-soft", "--warning-soft", "--danger-soft"],
  "the seven slots": ["--slot-1", "--slot-2", "--slot-3", "--slot-4", "--slot-5",
                      "--slot-6", "--slot-7"],
  "greeks": ["--greek-delta", "--greek-gamma", "--greek-vega", "--greek-theta",
             "--greek-residual"],
  "legs and series": ["--leg-main", "--leg-hedge", "--data-strategy", "--data-benchmark",
                      "--data-1", "--data-2", "--data-3", "--data-4"],
};

function swatches(names){
  const style = getComputedStyle(document.documentElement);
  return `<div class="g-swatches">${names.map(n => {
    const value = style.getPropertyValue(n).trim();
    // The slot colours carry their number, because section 12 requires colour never to be
    // the only carrier of which slot is which — and the colour-blind check reports two
    // slot pairs that collapse, so the number is doing real work.
    const slot = n.match(/^--slot-(\d)$/);
    return `<div class="g-swatch">
      <div class="g-chip-colour" style="background:${value};color:var(--on-accent)">
        ${slot ? slot[1] : ""}</div>
      <dl><dt>${n.replace(/^--/, "")}</dt><dd>${value || "not defined"}</dd></dl>
    </div>`;
  }).join("")}</div>`;
}

section("tokens", "Every value the interface draws with. The contrast of each text level "
  + "against each surface is checked by tools/check_contrast.py, which is why three of "
  + "the design plan's proposed values are a shade different from its table.",
  Object.entries(TOKENS).map(([group, names]) =>
    one(group, () => `<div class="g-frame">${swatches(names)}</div>`)).concat([
    one("the locked hold-out", `<div class="g-frame"><div class="g-holdout"></div>
      <p class="hint">Hatched and <b>empty</b>: a period that has not been revealed
      carries no line, rather than a line drawn faintly.</p></div>`),
    one("the comparison series carry a dash pattern as well as a colour", () => {
      const style = getComputedStyle(document.documentElement);
      const lines = [1, 2, 3, 4].map(i => {
        const dash = style.getPropertyValue(`--dash-${i}`).trim();
        return `<g>
          <path d="M8,${i * 26 - 10} C 90,${i * 26 - 22} 150,${i * 26 + 2} 260,${i * 26 - 14}"
                fill="none" stroke="var(--data-${i})" stroke-width="1.5"
                ${dash && dash !== "none" ? `stroke-dasharray="${dash}"` : ""}/>
          <text x="272" y="${i * 26 - 6}" fill="var(--text-secondary)"
                font-size="11" font-family="var(--mono)">run ${i}</text></g>`;
      }).join("");
      return `<div class="g-frame">
        <svg viewBox="0 0 340 112" style="width:100%;max-width:520px">${lines}</svg>
        <p class="hint">Colour alone cannot carry which run a line belongs to: a line has
        no number printed on it, and the design plan's proposed first and fourth series
        are <b>2.5 apart</b> under deuteranopia. The pattern survives colour blindness,
        greyscale printing and a washed-out projector alike.</p></div>`;
    }),
  ]));

section("controls", "Three button levels, one primary per page. Disabled is not used — a "
  + "control stays clickable and explains what is blocking (section 11).",
  [
    one("an explanation, on hover", `<div class="g-frame"><div class="g-row">
      <span class="chip" data-tip="The page explains itself by pointing at things, so this
        is a main surface rather than a detail. It replaces the browser's own tooltip,
        which arrives after about a second in a font nobody chose.">point at me</span>
      <span class="chip" data-tip="Short one.">and me</span>
    </div></div>`),
    one("buttons and chips", `<div class="g-frame"><div class="g-row">
      <button class="run" style="width:auto">run</button>
      <button class="btn">ask Claude</button>
      <button class="btn ghost">against a baseline</button>
      <button class="chip">dark</button>
      <button class="link" type="button">what is in ctx</button>
    </div></div>`),
    one("the rail, collapsed and open", `<div class="g-frame">
      <div class="g-row" style="align-items:flex-start">
        <nav class="rail" style="position:static;height:auto">
          <a class="rail-item on" href="#"><span class="rail-icon">&#9638;</span>
            <span class="rail-name">bench</span></a>
          <a class="rail-item" href="#"><span class="rail-icon">&#9636;</span>
            <span class="rail-name">experiment log</span></a>
        </nav>
        <nav class="rail" style="position:static;height:auto;width:var(--nav-expanded);
            flex-basis:var(--nav-expanded)">
          <a class="rail-item on" href="#"><span class="rail-icon">&#9638;</span>
            <span class="rail-name" style="opacity:1">bench</span></a>
          <a class="rail-item" href="#"><span class="rail-icon">&#9636;</span>
            <span class="rail-name" style="opacity:1">experiment log</span></a>
        </nav>
      </div></div>`),
  ]);

section("telling the reader something", "The three kinds of text, each in a fixed place "
  + "(criterion 2.6): the hard boundary on the numbers, this run's own diagnostics, and "
  + "help. They are never mixed.",
  [
    one("the boundary — one line, above the numbers",
      `<div class="g-frame"><div class="banner"><b>the boundary on these numbers</b>
        <span>Synthetic option chain: the underlying's prices are real, the option prices
        come from Black-Scholes. Conclusions about <b>when to enter</b> can be trusted;
        <b>which delta and where to take profit</b> cannot.</span></div></div>`),
    one("the problem strip — one notice, then several",
      `<div class="g-frame"><div class="issues" id="issuesDemo"></div></div>`,
      () => {
        // The real strip, from the real module: notice() is what every failure in the
        // interface goes through.
        notice("this run produced no trades. The entry condition may be too strict.");
        notice("the equity-curve metrics could not be computed: KeyError: 'equity_mid'");
        // paintIssues writes into #issues; the gallery lends it that id for the demo.
        const box = $("issuesDemo");
        box.id = "issues";
        paintIssues();
        box.id = "issuesDemo";
      }),
    // The example has to be a surface that is **actually** not built. It was the experiment
    // log, which now is — and a gallery showing a built surface as pending teaches the
    // reader the wrong thing about both.
    one("a surface that is not built yet",
      `<div class="g-frame"><div class="pending" style="padding:0">
        <h2>variants</h2>
        <p class="lead">every registered variant by slot, with its parameter schema</p>
        <p class="hint"><b>Not built yet.</b> the registry exists; no endpoint serves it
          yet</p>
      </div></div>`),
  ]);

section("waiting", "Shaped like the content that is coming, and only after 300 ms. Never "
  + "a full-page spinner, never a blank page, and never pretending a queued run has "
  + "started.",
  [
    one("a skeleton where the metric strip will be",
      `<div class="g-frame"><dl class="strip">${[0, 1, 2, 3, 4, 5].map(() =>
        `<div class="stat"><dt><span class="skeleton" style="display:block;width:68px">
          </span></dt><dd><span class="skeleton" style="display:block;width:92px;height:20px">
          </span></dd></div>`).join("")}</dl></div>`),
    one("running, with elapsed time",
      `<div class="g-frame"><div class="busy-note"><span class="spin"></span>
        <span>Running. The first fetch is slower; after that it is cached.</span></div>
        <p class="hint">The service runs a backtest on one synchronous request, so there
        is no queue position to show and nothing to cancel. Saying so beats a progress bar
        that is guessing.</p></div>`),
  ]);

section("results", "The renderers that ship, on fabricated data shaped like a real run's.",
  [
    one("the metric strip", `<div class="g-frame"><dl class="strip" id="strip"></dl></div>`,
      () => renderStrip(TRADES)),
    one("equity, both marks", `<div class="g-frame">
      <div class="chart"><svg id="equity" viewBox="0 0 1080 280"></svg></div>
      <dl class="strip" id="equityStrip"></dl></div>`,
      () => { renderEquity(EQUITY); renderEquityStrip(EQUITY_METRICS); }),
    one("a metric that cannot be computed prints as —, not as zero",
      `<div class="g-frame"><dl class="strip" id="equityStripNull"></dl>
        <p class="hint">The backend sends <code>null</code> for "cannot be computed".
        <code>pct(null)</code> once printed <b>0.0%</b>, inventing the figure the
        conversion existed to prevent.</p></div>`,
      () => {
        // A renderer writes into a node by **fixed id**, so one chart can appear only
        // once per page. Showing the same strip twice therefore needs the first one to
        // give up the id for a moment — and the first version of this case forgot, so
        // `$("equityStrip")` found the earlier element, rendered the null metrics into
        // it, and left this one empty. The gallery was built to surface exactly this: the
        // id coupling is the finding, and phase 1's chart group takes a target node.
        const real = $("equityStrip");
        const demo = $("equityStripNull");
        real.id = "equityStripReal";
        demo.id = "equityStrip";
        renderEquityStrip({...EQUITY_METRICS, sharpe: null, annualised_vol: null,
                           max_drawdown: null});
        demo.id = "equityStripNull";
        real.id = "equityStrip";
      }),
    one("P&L attribution", `<div class="g-frame">
      <div class="chart"><svg id="attr" viewBox="0 0 520 200"></svg></div></div>`,
      () => renderAttribution(ATTRIBUTION)),
    one("distribution and by year", `<div class="g-frame"><div class="two">
      <div class="chart"><svg id="hist" viewBox="0 0 520 250"></svg></div>
      <div class="chart"><svg id="years" viewBox="0 0 520 220"></svg></div>
      </div></div>`,
      () => { renderHist(TRADES); renderPeriods(TRADES); }),
    one("a dimension against P&L", `<div class="g-frame">
      <div class="chart"><svg id="scatter" viewBox="0 0 520 250"></svg></div>
      <p class="hint" id="scatNote"></p></div>`,
      () => renderScatter(TRADES, "entry_iv")),
    one("robustness, and the trade log", `<div class="g-frame">
      <div class="scroll"><table id="robust"></table></div>
      <p class="hint" id="detailNote"></p>
      <div class="scroll" style="max-height:220px;overflow-y:auto">
        <table id="detail"></table></div></div>`,
      () => { renderRobust(TRADES); renderDetail(TRADES); }),
    one("a chart that cannot be drawn says why", `<div class="g-frame">
      <div class="chart"><svg id="blankDemo" viewBox="0 0 520 200"></svg></div>
      <p class="hint">A blank chart looks exactly like "there was never any data here",
      so it never stays blank.</p></div>`,
      () => blankChart($("blankDemo"),
        "the trade-table engine has no equity curve")),
  ]);

section("the trade card", "The design plan's section 7, which opens in the drawer beside "
  + "the run it came from. Every figure in it is a stored column of the trade log; the four "
  + "blocks the record cannot fill yet are named in the card rather than left out, so a card "
  + "with no action log reads as a record without one and not as a trade that did nothing.",
  [
    one("the card, on a fabricated trade", () => `<div class="g-frame g-card-frame">${
      tradeCard(TRADES[3], {config: SAMPLE_CONFIG, position: 4, total: TRADES.length})
    }</div>`),
    one("a losing trade, and the same card", () => `<div class="g-frame g-card-frame">${
      tradeCard(TRADES.find(t => t.net_pnl < 0) || TRADES[0],
                {config: SAMPLE_CONFIG, position: 9, total: TRADES.length})
    }</div>`),
    one("in the real drawer, where it ships", `<div class="g-frame">
      <p class="hint">The drawer is 400 px by the token, draggable to 560 by its left edge,
        and expands to the full window. Closing it returns focus and scroll to where the
        reader was.</p>
      <button class="btn" type="button" id="openCardDemo">open the trade card</button>
      <button class="chip" type="button" id="fullCardDemo">expand it</button>
      <button class="chip" type="button" id="closeCardDemo">close it</button></div>`,
      () => {
        const open = () => openDrawer({
          title: `trade ${TRADES[3].trade_id}`,
          subtitle: "QQQ · fabricated, shaped like a real row",
          actions: cardActions(TRADES[3]),
          body: tradeCard(TRADES[3], {config: SAMPLE_CONFIG, position: 4,
                                      total: TRADES.length}),
        }, () => {});
        $("openCardDemo").addEventListener("click", open);
        $("fullCardDemo").addEventListener("click", () => { open(); toggleFull(); });
        $("closeCardDemo").addEventListener("click", closeDrawer);
      }),
  ]);

section("comparing runs", "Two to four runs on one axis. Colour is never the only carrier: "
  + "each series takes its column's colour **and** its dash pattern, because a line has no "
  + "number printed on it and two of the four proposed colours collapse into one another "
  + "under deuteranopia.",
  [
    one("three runs overlaid", `<div class="g-frame"><div id="overlayDemo"></div></div>`,
      () => {
        const dates = EQUITY.map(r => r.date);
        const series = [1, 2, 3].map(column => ({
          name: `run ${100 + column}`,
          column,
          values: EQUITY.map((r, i) =>
            // A different shape per series, and the third stops early, so the gap rule is
            // visible: a line that ends does not get drawn across the rest of the period.
            column === 3 && i > EQUITY.length * 0.7 ? null
              : r.equity_mid * (1 + column * 0.04 * Math.sin(i / 40))),
        }));
        chartStack($("overlayDemo"), {
          dates,
          layers: [seriesLayer({key: "equity", title: "equity, marked at the mid",
                                height: 200, series, format: money})],
          onBrush: () => {},
        });
      }),
    one("the column headers, and a difference that is not separated", `<div class="g-frame">
      <table class="cmp-columns"><tbody><tr>
        ${[1, 2, 3].map(i => `<td class="cmp-col series-${i}${i === 1 ? " base" : ""}">
          <span class="cmp-swatch"></span>
          <span class="run-link">run ${100 + i}</span>
          <span class="cmp-idea">a fabricated idea</span>
          <span class="cmp-when">2026-10-07</span>
          ${i === 1 ? `<span class="cmp-badge">baseline</span>` : ""}
        </td>`).join("")}
      </tr></tbody></table>
      <table class="plain cmp-table"><tbody>
        <tr><th class="left">net P&amp;L</th>
          <td class="n base"><span class="cmp-value">+$12,480</span>
            <span class="cmp-iv">−3.1k … +28.4k</span></td>
          <td class="n"><span class="cmp-value">+$15,900</span>
            <span class="cmp-iv">−1.2k … +31.0k</span>
            <span class="cmp-delta pos">+$3,420 vs baseline</span>
            <span class="cmp-tie">not separated</span></td>
        </tr>
      </tbody></table>
      <p class="hint">The second run looks better and its interval overlaps the baseline's,
        so the page says so in words rather than leaving two ranges to be compared by
        eye.</p></div>`),
  ]);

section("the command palette", "⌘K. It lists runs, trades, pages and every action that has "
  + "a shortcut, from the live keyboard registry — so a binding cannot exist without being "
  + "discoverable, and there is no second list to keep in step.",
  [
    one("the real palette, with a fabricated source", `<div class="g-frame">
      <button class="btn" type="button" id="openPaletteDemo">open the palette</button>
      <p class="hint">Type "fabricated" to see a source's entries, or anything else to see
        the commands. Esc closes it.</p></div>`,
      () => {
        bind("gallery", [{keys: ["mod+shift+g"], does: "a command from the gallery",
                          run: () => notice("the gallery's own command ran")}]);
        palette.provide("gallery", query =>
          "fabricated".includes(String(query).toLowerCase()) && query
            ? [{kind: "sample", label: "a fabricated run", hint: "+$12,480",
                run: () => notice("the fabricated row was chosen")}]
            : []);
        $("openPaletteDemo").addEventListener("click", palette.open);
      }),
  ]);

section("formatting", "One place decides how a number reads, so two screens cannot "
  + "disagree about the same value.",
  [
    one("absence, zero, and a real number", `<div class="g-frame">
      <table><thead><tr><th style="text-align:left">value</th><th>money</th><th>pct</th>
        <th>num</th></tr></thead><tbody>
        ${[null, undefined, NaN, Infinity, 0, 0.1234, -5000.4].map(v => `<tr>
          <td style="text-align:left"><code>${v === undefined ? "undefined" : String(v)}</code></td>
          <td class="n">${money(v)}</td><td class="n">${pct(v)}</td>
          <td class="n">${num(v)}</td></tr>`).join("")}
      </tbody></table></div>`),
  ]);

/* ── theme, and render ── */
const THEMES = ["dark", "light"];

function applyTheme(name){
  document.documentElement.dataset.theme = name;
  const next = THEMES[(THEMES.indexOf(name) + 1) % THEMES.length];
  $("theme").textContent = next;
  $("theme").title = `switch to the ${next} theme`;
}

applyTheme("dark");
startTips();
$("theme").addEventListener("click", () => {
  const now = document.documentElement.dataset.theme || "dark";
  applyTheme(THEMES[(THEMES.indexOf(now) + 1) % THEMES.length]);
  // The swatch values are read from the computed style, so they are re-read on a switch.
  render();
});

function render(){
  $("gallery").innerHTML = SECTIONS.map(s => `
    <section class="g-sec">
      <h2>${s.title}</h2>
      <p>${s.why}</p>
      ${s.cases.map(c => `
        <div class="g-case"><h3>${c.label}</h3>${
          typeof c.html === "function" ? c.html() : c.html}</div>`).join("")}
    </section>`).join("");
  // After the markup is in the document: the chart renderers write into a node by id, so
  // they cannot run before it exists. That id coupling is itself a finding — phase 1's
  // chart group takes a target node, which is what lets one chart appear twice on a page.
  for(const s of SECTIONS)
    for(const c of s.cases)
      if(c.after) c.after();
}

render();
