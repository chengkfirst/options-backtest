/* selftest.js — every module exercised against the cases that break them.
 *
 * Not a substitute for the guards: `check_wiring` holds the front/back contract,
 * `check_modules` the imports, `check_contrast` the palette. Those read the source. This
 * runs the code, which is the only way to find the faults that live in behaviour — a
 * formatter that prints absence as a number, a chart that throws on an empty series, an
 * escape that is safe in text and not in an attribute.
 *
 * It runs in the browser against the real modules and, where a case needs one, the real
 * API. Open `/selftest.html`; every case reports pass or fail with what it expected.
 *
 * **Degenerate input is the point.** Every renderer here is given no rows, one row, all
 * equal values, nulls, and infinities, because that is what a short run, a filtered range
 * or a statistic that would not compute actually produces — and each of those has shipped
 * a blank chart or a thrown exception at some point in this file's history.
 */
"use strict";

const CASES = [];
const suite = (name, fn) => CASES.push({name, fn});

/* ── the harness ── */
class Fail extends Error {}
const eq = (got, want, what) => {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  if(a !== b) throw new Fail(`${what}: got ${a}, wanted ${b}`);
};
const ok = (cond, what) => { if(!cond) throw new Fail(what); };
const throws = (fn, what) => {
  try{ fn(); }catch{ return; }
  throw new Fail(`${what}: nothing was thrown`);
};

/** A detached element with an id, so a renderer that looks one up by id can be run
 *  outside the page it belongs to.
 *
 * **The tag matters.** `renderRobust` and the other table renderers write `<thead><tr>`
 * into their host, and a browser drops those tags inside a `<div>` — keeping the text and
 * none of the structure. Given one, the first run of this suite reported "the robustness
 * table has rows" as a failure against code that is correct on the page, where the host is
 * a `<table>`. A harness that can be wrong in that direction is worth less than no harness.
 */
function host(id, tag = "div"){
  const el = document.createElementNS(
    tag === "svg" ? "http://www.w3.org/2000/svg" : "http://www.w3.org/1999/xhtml", tag);
  el.id = id;
  if(tag === "svg") el.style.width = "400px";
  document.getElementById("sandbox").append(el);
  return el;
}

/* ── fmt: absence must never print as a number ── */
suite("fmt — absence, zero and sign", async () => {
  const f = await import("./fmt.js");
  for(const v of [null, undefined, NaN, Infinity, -Infinity])
    for(const [n, fn] of [["money", f.money], ["pct", f.pct], ["num", f.num],
                          ["size", f.size], ["moneyShort", f.moneyShort]])
      eq(fn(v), "—", `${n}(${String(v)})`);
  eq(f.money(0), "+$0.00", "a real zero is not absence");
  eq(f.pct(0), "0.0%", "pct(0)");
  eq(f.size(-1234), "$1,234", "size drops the sign and keeps the unit");
  eq(f.money(-1234), "−$1,234", "money uses a true minus");
  eq(f.pct(-0.258), "−25.8%", "pct uses a true minus");
  eq(f.num(-1.5), "−1.50", "num uses a true minus");
  eq(f.moneyShort(-54321), "−54.3k", "moneyShort thousands");
  eq(f.moneyShort(1190000), "+1.19M", "moneyShort millions");
  eq(f.sgn(0), "", "zero has no sign class");
  ok(!/-/.test(f.pct(-0.1) + f.num(-1) + f.money(-1)),
     "no hyphen-minus survives anywhere a true minus is used");
});

/* ── the addendum's formatting rules, which are checkable one by one ── */
suite("fmt — the written form of every kind of value", async () => {
  const f = await import("./fmt.js");
  // Money: a dollar sign, thousands separators, the sign always, whole dollars at a
  // thousand and above and cents below — at four figures the cents are noise, at two they
  // are the number.
  eq(f.money(12480), "+$12,480", "money at four figures is whole dollars");
  eq(f.money(-3.15), "−$3.15", "money below a thousand carries its cents");
  eq(f.money(999.5), "+$999.50", "just below the boundary");
  eq(f.money(1000), "+$1,000", "and at it");
  // In a table the unit is in the header, so the cell carries the number alone.
  eq(f.bare(12480), "+12,480", "bare drops the unit and keeps the rest");
  eq(f.bare(-3.15), "−3.15", "bare keeps the cents too");
  // Percent: one decimal, and two below one percent, where one rounds 0.35% to 0.4%.
  eq(f.pct(0.184), "18.4%", "a percentage at one decimal");
  eq(f.pct(0.0035), "0.35%", "below one percent takes two");
  eq(f.pct(-0.0035), "−0.35%", "and keeps its sign");
  eq(f.pct(0), "0.0%", "zero is one decimal, not two");
  // Ratios are two decimals; counts carry separators.
  eq(f.num(1.3249), "1.32", "a ratio at two decimals");
  eq(f.size(-1046), "$1,046", "unsigned money keeps its unit");
  // Three date forms, one per place they are read.
  eq(f.dateLong("2026-10-07"), "Oct 7, 2026", "prose takes a month name");
  eq(f.dateShort("2026-10-07"), "Oct 7", "an axis has already said the year");
  eq(f.expiry("2026-11-20", 44), "2026-11-20 · 44 DTE", "an expiry says how far away it is");
  eq(f.dateLong(null), "—", "an absent date");
  eq(f.dateLong("not a date"), "—", "a value that is not one");
  // Parsed as UTC: a bare Date() renders UTC midnight in the local zone, which is the
  // previous day west of Greenwich — a trade opened on the 17th labelled the 16th.
  eq(f.dateLong("2023-01-01"), "Jan 1, 2023", "no off-by-one from the local zone");
});

/* ── dom: escaping, in both the contexts this interface writes ── */
suite("dom — escaping is safe in text and in an attribute", async () => {
  const d = await import("./dom.js");
  eq(d.esc("<b>"), "&lt;b&gt;", "angle brackets");
  eq(d.esc('a"b'), "a&quot;b", "double quote");
  eq(d.esc("a'b"), "a&#39;b", "apostrophe");
  eq(d.esc(null), "", "null is empty, not the word null");
  eq(d.esc(0), "0", "zero is not empty");
  const nasty = '" onload="alert(1)';
  const box = document.createElement("div");
  box.innerHTML = `<div id="x" title="${d.esc(nasty)}">t</div>`;
  ok(!box.querySelector("#x").hasAttribute("onload"), "no attribute break-out");
  eq(box.querySelector("#x").getAttribute("title"), nasty, "the value survives intact");
  eq(d.mark("a **b** c"), "a <b>b</b> c", "emphasis is rendered");
  ok(d.mark("<i>**x**</i>").startsWith("&lt;i&gt;"), "escaped before formatted");
  eq(d.mark(null), "", "mark(null)");
});

/* ── stats and contract ── */
suite("stats — degenerate input", async () => {
  const s = await import("./stats.js");
  eq(s.stats([]).n, 0, "no rows");
  eq(s.stats([{net_pnl: 5}]).sum, 5, "one row sums");
  eq(s.bucketize([], "x", 4), null, "no rows cannot be bucketed");
  eq(s.bucketize([{x: 1}, {x: 1}, {x: 1}], "x", 4), null,
     "one distinct value cannot be bucketed");
});

suite("contract — the three outcomes of a path", async () => {
  const c = await import("./contract.js");
  eq(c.resolvePath({a: {b: 1}}, "a.b"), "ok", "present");
  eq(c.resolvePath({a: {}}, "a.b"), "missing", "absent key");
  eq(c.resolvePath({a: null}, "a.b"), "empty", "a recorded null is not missing");
  eq(c.resolvePath({a: []}, "a.*.b"), "empty", "an empty list");
  eq(c.resolvePath({a: [{b: 1}]}, "a.*.b"), "ok", "a list element");
  eq(c.resolvePath({a: {}}, "a.*"), "missing", "a non-list under *");
  eq(c.resolvePath({a: 0}, "a"), "ok", "zero is a value");
  eq(c.resolvePath({a: false}, "a"), "ok", "false is a value");
});

/* ── router: the state of a reading, carried in a link ── */
suite("router — merge, removal and reserved characters", async () => {
  const r = await import("./router.js");
  const was = location.hash;
  location.hash = "#/runs/9?tab=overview&range=a..b&trade=T-1";
  await new Promise(x => setTimeout(x, 60));
  eq(r.location_().path, "/runs/9", "path");
  eq(r.location_().query.range, "a..b", "a range survives");
  r.replace({tab: "spread"});
  await new Promise(x => setTimeout(x, 40));
  eq(r.location_().query.range, "a..b", "replace merges rather than overwriting");
  r.replace({trade: null});
  await new Promise(x => setTimeout(x, 40));
  eq("trade" in r.location_().query, false, "null removes the key");
  r.replace({note: "a b&c=d"});
  await new Promise(x => setTimeout(x, 40));
  eq(r.location_().query.note, "a b&c=d", "reserved characters round-trip");
  location.hash = was;
});

/* ── svg: one unit is one screen pixel ── */
suite("svg — fit, and a blank chart that says why", async () => {
  const v = await import("./charts/svg.js");
  const svg = host("fit_probe", "svg");
  const {W, H} = v.fit(svg, {height: 200});
  eq(H, 200, "height is what was asked for");
  ok(Math.abs(W - 400) < 2, "width tracks the box");
  eq(svg.getAttribute("viewBox"), `0 0 ${W} 200`, "the viewBox makes a unit a pixel");
  v.blankChart(svg, "nothing to draw");
  ok(svg.innerHTML.includes("nothing to draw"),
     "a blank chart carries its reason, never an empty box");
  ok(v.txt(0, 0, "x").includes("<text"), "txt builds a text node");
});

/* ── every chart, against the shapes that have broken them ── */
const TRADES = [
  {trade_id: 1, entry_date: "2023-01-03", exit_date: "2023-01-10", net_pnl: 120,
   holding_days: 5, entry_iv: 0.21, entry_delta: 0.35, exit_reason: "take_profit",
   mfe: 0.4, mae: -0.1, entry_moneyness: 0.02},
  {trade_id: 2, entry_date: "2023-02-01", exit_date: "2023-02-06", net_pnl: -80,
   holding_days: 3, entry_iv: 0.25, entry_delta: 0.30, exit_reason: "stop_loss",
   mfe: 0.1, mae: -0.5, entry_moneyness: -0.01},
];
const FLAT = [{trade_id: 1, entry_date: "2023-01-03", exit_date: "2023-01-04", net_pnl: 0,
               holding_days: 1, entry_iv: 0.2, entry_delta: 0.3, exit_reason: "time_exit",
               entry_moneyness: 0}];

suite("charts — no rows, one row, and all-equal values", async () => {
  const dist = await import("./charts/distribution.js");
  const eqt = await import("./charts/equity.js");
  const att = await import("./charts/attribution.js");
  for(const id of ["hist", "years", "scatter", "curve", "attr", "ab"]) host(id, "svg");
  host("scatNote"); host("curveNote"); host("abNote");

  // No rows: every one of these must say so rather than drawing an empty frame.
  dist.renderHist([]); dist.renderPeriods([]); dist.renderScatter([], "entry_iv");
  eqt.renderCurve([], false); att.renderAttribution(null);
  for(const id of ["hist", "years", "scatter", "curve", "attr"])
    ok(document.getElementById(id).textContent.trim().length > 0,
       `${id} with no rows says why it is blank`);

  // One row, and a run where every value is the same — the divide-by-zero shapes.
  dist.renderHist(FLAT); dist.renderPeriods(FLAT); eqt.renderCurve(FLAT, false);
  dist.renderScatter(TRADES.concat(TRADES, TRADES), "entry_iv");
  for(const id of ["hist", "years", "curve"])
    ok(document.getElementById(id).innerHTML.length > 0, `${id} survives one row`);

  // Two rows: the ordinary case must actually draw shapes, not only text.
  dist.renderHist(TRADES); dist.renderPeriods(TRADES); eqt.renderCurve(TRADES, false);
  for(const id of ["hist", "years", "curve"])
    ok(document.getElementById(id).querySelectorAll("rect,path,circle,line").length > 0,
       `${id} draws something for two trades`);

  // Attribution: the parts and the result, and a residual that must not be hidden.
  att.renderAttribution({totals: {delta: 10, gamma: -5, vega: 1, theta: -20, residual: 2},
                         high_residual: 3}, -12);
  const a = document.getElementById("attr");
  ok(a.querySelectorAll("rect").length >= 6, "a bar per Greek plus the result");
  ok(a.textContent.includes("result"), "the result bar is labelled");
});

suite("charts — the period chart picks its bucket from the span", async () => {
  const dist = await import("./charts/distribution.js");
  host("years", "svg"); host("periodHead");
  const months = [{entry_date: "2023-01-05", net_pnl: 10}, {entry_date: "2023-03-05", net_pnl: -4}];
  dist.renderPeriods(months);
  eq(document.getElementById("periodHead").textContent, "by month", "a short run is monthly");
  const years = [{entry_date: "2015-01-05", net_pnl: 10}, {entry_date: "2023-03-05", net_pnl: -4}];
  dist.renderPeriods(years);
  eq(document.getElementById("periodHead").textContent, "by year", "a long run is yearly");
  // Both ends of the scale carry their own sign — an absolute value through a signed
  // formatter is how a loss printed as "+31,220".
  const texts = [...document.getElementById("years").querySelectorAll("text")]
    .map(t => t.textContent);
  ok(texts.some(t => t.startsWith("−")), "the floor of the scale is labelled negative");
  ok(texts.some(t => t.startsWith("+")), "the ceiling is labelled positive");
});

/* ── the chart stack: the shared axis, the brush and the shading ── */
suite("axis — stack, brush and holding-period shading", async () => {
  const ax = await import("./charts/axis.js");
  const box = host("stack_probe");
  box.style.width = "600px";
  const dates = ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"];
  const stack = ax.chartStack(box, {
    dates,
    layers: [ax.lineLayer({key: "eq", title: "equity", values: [1, 2, 3, 4]})],
    holdOut: {from: "2023-01-04", to: "2023-01-05"},
    onBrush: () => {},
  });
  ok(box.querySelectorAll("svg").length > 0, "the stack draws");
  ok(box.querySelector("rect.holdout"), "a declared hold-out is hatched");
  stack.setHeld({from: "2023-01-02", to: "2023-01-03"});
  ok(box.querySelector("rect.held"), "a selected trade shades its holding period");
  stack.setHeld(null);
  ok(!box.querySelector("rect.held"), "and clears");
  stack.setBrush({from: "2023-01-02", to: "2023-01-04"});
  stack.setBrush(null);
  stack.destroy();
});

/* ── tables ── */
suite("tables — buckets and the robustness list", async () => {
  const t = await import("./charts/tables.js");
  host("buckets", "table"); host("dimNote");
  host("robust", "table"); host("cross", "table"); host("crossWrap");
  t.renderBuckets([], "entry_iv", 4);
  ok(document.getElementById("buckets").textContent.trim().length > 0,
     "no rows says why rather than drawing an empty table");
  t.renderBuckets(TRADES.concat(TRADES, TRADES, TRADES), "entry_iv", 2);
  t.renderRobust(TRADES);
  ok(document.getElementById("robust").querySelectorAll("tbody tr").length >= 2,
     "the robustness table has a baseline row and at least one cut");
  ok(!/without the best 1 trades/.test(document.getElementById("robust").textContent),
     "one trade is not pluralised");
});

/* ── the live API: the contract, and the shapes the page depends on ── */
suite("api — meta and a stored run satisfy the contract", async () => {
  const net = await import("./net.js");
  const c = await import("./contract.js");
  const meta = await net.getJSON(net.API.meta);
  for(const path of c.CONTRACT.meta)
    ok(c.resolvePath(meta, path) !== "missing", `meta is missing ${path}`);
  const runs = await net.getJSON(net.API.runs);
  ok(runs.runs.length > 0, "the store has runs to check against");
  const run = await net.getJSON(net.API.run, {id: runs.runs[0].id});
  for(const path of c.CONTRACT.run)
    ok(c.resolvePath(run, path) !== "missing", `a stored run is missing ${path}`);
});

suite("api — a refusal arrives as a message, not as a crash", async () => {
  const net = await import("./net.js");
  let caught = null;
  try{ await net.getJSON("/api/run", {id: 99999999}); }
  catch(e){ caught = e; }
  ok(caught, "a request for a run that does not exist is refused");
  ok(String(caught.message).length > 0, "and the refusal carries a message");
});

/* ── net: the one file that names an /api path ── */
suite("net — every declared endpoint answers", async () => {
  const net = await import("./net.js");
  // A path that is listed and does not exist is a broken feature nobody notices until the
  // control is pressed. GET endpoints are probed; the POST ones take a body and are
  // exercised by the surfaces that use them.
  for(const key of ["health", "meta", "runs", "strategies", "notes"]){
    const got = await net.getJSON(net.API[key]);
    ok(got && typeof got === "object", `${key} (${net.API[key]}) returned nothing usable`);
  }
});

suite("net — a notice is raised rather than a silent failure", async () => {
  const net = await import("./net.js");
  const strip = document.createElement("div");
  strip.id = "issues";
  document.getElementById("sandbox").append(strip);
  net.clearIssues();
  net.notice("a problem a person can act on");
  ok(strip.textContent.includes("a problem a person can act on"),
     "notice reaches the strip");
  net.clearIssues();
  eq(strip.textContent.trim(), "", "and clears");
});

/* ── tip: the hover layer, which must not become a question mark ── */
suite("tip — shows on hover and never changes the cursor", async () => {
  const {startTips} = await import("./ui/tip.js");
  startTips();
  const el = document.createElement("button");
  el.dataset.tip = "a tip";
  el.textContent = "x";
  document.getElementById("sandbox").append(el);
  ok(getComputedStyle(el).cursor !== "help",
     "no element carrying a tip gets the help cursor");
});

/* ── the views render, which is the only test that covers their markup ── */
suite("views — the run page renders every tab without throwing", async () => {
  const net = await import("./net.js");
  const runs = await net.getJSON(net.API.runs);
  const id = runs.runs[0].id;
  const frame = document.createElement("iframe");
  frame.style.cssText = "width:1200px;height:800px;border:0";
  document.getElementById("sandbox").append(frame);
  const tabs = ["overview", "spread", "slice", "attribution", "trades",
                "execution", "robustness", "settings", "exposure"];
  for(const tab of tabs){
    frame.src = `/#/runs/${id}?tab=${tab}`;
    await new Promise(r => { frame.onload = r; setTimeout(r, 2500); });
    await new Promise(r => setTimeout(r, 900));
    const doc = frame.contentDocument;
    const issues = doc.getElementById("issues");
    ok(!issues || issues.textContent.trim() === "",
       `${tab} raised a notice: ${issues && issues.textContent.trim().slice(0, 120)}`);
    const mid = doc.getElementById("tierMid");
    ok(mid && mid.textContent.trim().length > 30, `${tab} rendered nothing`);
    const text = doc.getElementById("view-run").textContent;
    ok(!/\bundefined\b|\bNaN\b/.test(text), `${tab} leaked an undefined or a NaN`);
    ok(!/\*\*/.test(text), `${tab} printed raw markdown`);
  }
  frame.remove();
});

suite("views — the composer renders and refuses an incomplete rule", async () => {
  const frame = document.createElement("iframe");
  frame.style.cssText = "width:1200px;height:800px;border:0";
  document.getElementById("sandbox").append(frame);
  frame.src = "/#/bench";
  await new Promise(r => { frame.onload = r; setTimeout(r, 2500); });
  await new Promise(r => setTimeout(r, 1200));
  const doc = frame.contentDocument;
  ok(doc.getElementById("form"), "the composer is there");
  ok(doc.querySelectorAll(".comp").length >= 7, "every slot has a card");
  ok([...doc.getElementById("form").elements].filter(e => e.name).length > 20,
     "the fields are generated");
  const issues = doc.getElementById("issues");
  ok(!issues || issues.textContent.trim() === "", "the composer raised no notice");
  // A registered rule with a parameter nobody filled must block, not run.
  const pick = doc.querySelector('input[data-pick="stop_loss"][value="trailing"]');
  if(pick){
    pick.checked = true;
    pick.dispatchEvent(new frame.contentWindow.Event("change", {bubbles: true}));
    await new Promise(r => setTimeout(r, 400));
    ok(doc.querySelector('[data-param]') || doc.querySelector(".variant .fields"),
       "choosing a rule offers its parameters");
  }
  frame.remove();
});

/* ── the addendum's state and failure rules ── */
suite("router — a tab switch is a step, a brush is not", async () => {
  const r = await import("./router.js");
  const was = location.hash;
  // `history.length` is capped and coalesced by browsers, so it says nothing. What matters
  // is observable: after a step, going back returns to the state before it; after a
  // replace, it does not.
  location.hash = "#/x?tab=a";
  await new Promise(x => setTimeout(x, 80));
  r.step({tab: "b"});
  await new Promise(x => setTimeout(x, 120));
  eq(r.location_().query.tab, "b", "the step took effect");
  history.back();
  await new Promise(x => setTimeout(x, 200));
  eq(r.location_().query.tab, "a", "and back returns to the tab before it");
  history.forward();
  await new Promise(x => setTimeout(x, 200));
  eq(r.location_().query.tab, "b", "and forward returns to it");

  // A replace must not be reachable by going back, or a drag would bury the last real
  // navigation under a hundred steps.
  r.replace({range: "1..2"});
  await new Promise(x => setTimeout(x, 120));
  history.back();
  await new Promise(x => setTimeout(x, 200));
  eq(r.location_().query.tab, "a", "a replace left no step of its own");
  location.hash = was;
});

suite("views — a tab that throws keeps its failure to itself", async () => {
  const net = await import("./net.js");
  const runs = await net.getJSON(net.API.runs);
  const id = runs.runs[0].id;
  const real = await net.getJSON(net.API.run, {id});

  const frame = document.createElement("iframe");
  frame.style.cssText = "width:1200px;height:800px;border:0";
  document.getElementById("sandbox").append(frame);
  // Load the shell on a page that fetches no run, patch `fetch` on **that** window, then
  // reach the run by changing the hash. Setting `src` or `location.href` builds a new
  // window and throws the patch away with the old one, which is why the first attempt at
  // this case found nothing to assert on.
  frame.src = "/#/bench";
  await new Promise(r => { frame.onload = r; setTimeout(r, 2500); });
  await new Promise(r => setTimeout(r, 800));

  // The failure is planted in the **data**, not by patching a module: an ES export cannot
  // be redefined, and a test that needs to would be testing a shape the product does not
  // have. A row whose `entry_date` is a number makes the period chart throw on `.slice`,
  // which is exactly the kind of fault this rule exists for.
  frame.contentWindow.__poison = {id, poisoned: {...real, rows: [{...real.rows[0], entry_date: 42}]}};
  frame.contentWindow.eval(`
    const realFetch = window.fetch;
    window.fetch = (u, o) => String(u).includes("/api/run?id=" + window.__poison.id)
      ? Promise.resolve(new Response(JSON.stringify(window.__poison.poisoned),
          {headers: {"content-type": "application/json"}}))
      : realFetch(u, o);
  `);
  frame.contentWindow.location.hash = `#/runs/${id}?tab=spread`;
  await new Promise(r => setTimeout(r, 2500));

  const doc = frame.contentDocument;
  ok(doc.querySelector(".layer-error"), "the failure is drawn where the layer was");
  ok(doc.querySelector("#retryTab"), "with a way to try again");
  ok(doc.querySelectorAll(".card").length >= 8,
     "the metric cards above it still rendered");
  frame.remove();
});

/* ── run the lot ── */
export async function run(){
  const out = document.getElementById("out");
  let passed = 0;
  const failures = [];
  for(const {name, fn} of CASES){
    const row = document.createElement("div");
    row.className = "case";
    out.append(row);
    try{
      await fn();
      passed++;
      row.innerHTML = `<b class="ok">pass</b> ${name}`;
    }catch(e){
      failures.push(`${name} — ${e.message}`);
      row.innerHTML = `<b class="bad">FAIL</b> ${name}<p>${e.message}</p>`;
      row.className = "case bad";
    }
  }
  document.getElementById("tally").textContent =
    `${passed} of ${CASES.length} passed` + (failures.length ? `, ${failures.length} failed` : "");
  window.SELFTEST = {passed, total: CASES.length, failures};
}
